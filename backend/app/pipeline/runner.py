"""End-to-end document processing.

Three readers cooperate, because one page-segmentation mode cannot serve a whole form:

  * a full-page OCR pass, kept as the searchable text of record;
  * a table reader for the ruled parcel grid (khasra rows), which produces one
    LandParcel per plot;
  * a region reader for the label/value bands above and below the table, which
    produces the document-level header fields.

Fields from the region pass and the full-page pass are merged by confidence, then
numeric fields are re-read with a digit-only model before validation runs.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import DocStatus, Document, ExtractedField, LandParcel, LandRecord
from app.pipeline.document import apply_rotation, detect_rotation, looks_like_document, rectify
from app.pipeline.extract import Extracted, ExtractionOutput, extract_fields, find_khasra_rows
from app.pipeline.learning import CorrectionLexicon, field_reliability
from app.pipeline.normalize import normalize_field, parse_area
from app.pipeline.ocr import missing_languages, resolve_languages, run_ocr
from app.pipeline.preprocess import load_pages, preprocess
from app.pipeline.regions import DIGIT_ONLY_FIELDS, read_regions, refine_numeric_fields
from app.pipeline.table import TableResult, extract_tables
from app.services.audit import log_action
from app.services.validation import validate_document

PARCEL_TEXT_COLUMNS = ("land_classification", "crop", "owner_name", "possessor_name",
                       "father_name", "share", "remarks")


def _merge_score(f: Extracted) -> float:
    """Rank a candidate reading: a value of the right shape outranks a slightly more
    confident one that is obviously malformed."""
    from app.pipeline.extract import VALIDATORS

    validator = VALIDATORS.get(f.field_name)
    score = f.confidence
    if validator and validator.match(f.value):
        score += 15.0
    if f.field_name in DIGIT_ONLY_FIELDS and not any(ch.isdigit() for ch in f.value):
        score -= 25.0          # a number field holding no number is not a reading at all
    return score


def _merge_fields(primary: ExtractionOutput, secondary: ExtractionOutput) -> dict[str, Extracted]:
    """Keep the best reading of each field across two passes."""
    merged: dict[str, Extracted] = {}
    for source in (primary, secondary):
        for f in source.fields:
            if f.field_name not in merged or _merge_score(merged[f.field_name]) < _merge_score(f):
                merged[f.field_name] = f
    return merged


def _normalize_for_compare(v: str) -> str:
    """Aggressive normalisation for Shadow-Mode value comparison.

    Two OCR passes almost never produce byte-identical strings even when they
    agree — one has a trailing space, the other has a Devanagari digit for an
    ASCII one, one includes a stray quote. Strip everything cosmetic and
    compare the residue: whitespace collapsed, case flattened, punctuation
    stripped, ASCII/Devanagari digits unified.
    """
    from app.pipeline.extract import DEVANAGARI_DIGITS

    if not v:
        return ""
    x = v.translate(DEVANAGARI_DIGITS).lower()
    x = "".join(c for c in x if c.isalnum() or c.isspace())
    return " ".join(x.split())


def _compare_shadow(name: str, primary: Extracted | None,
                    shadow: Extracted | None, winner: Extracted
                    ) -> tuple[str, str | None, str | None]:
    """Return (agreement, shadow_value_to_store, shadow_source).

    winner is what the merged pipeline chose. primary is the region-OCR reading
    (rich label context); shadow is the full-page reading (independent
    segmentation). If both saw the field and normalise-equal, agree. If both
    saw it and differ, disagree and record the other reading. If only one
    saw it, mark accordingly.
    """
    p_val = primary.value if primary else None
    s_val = shadow.value if shadow else None
    if p_val and s_val:
        if _normalize_for_compare(p_val) == _normalize_for_compare(s_val):
            return "agree", None, None
        # Winner is stored on the field's own value; the OTHER reading is the shadow.
        other = shadow if winner is primary or winner.value == p_val else primary
        return "disagree", other.value, other.source
    if p_val and not s_val:
        return "only_primary", None, None
    if s_val and not p_val:
        return "only_shadow", None, None
    return "only_primary", None, None


def _shadow_summary(fields: dict[str, Extracted]) -> dict:
    """Roll up per-field shadow agreements into a document-level summary."""
    counts = {"agree": 0, "disagree": 0, "only_primary": 0, "only_shadow": 0}
    disagreements: list[dict] = []
    for f in fields.values():
        if f.source == "metadata":
            continue
        state = f.shadow_agreement or "only_primary"
        counts[state] = counts.get(state, 0) + 1
        if state == "disagree":
            disagreements.append({
                "field": f.field_name,
                "primary_value": f.value,
                "shadow_value": f.shadow_value,
                "shadow_source": f.shadow_source,
            })
    total = sum(counts.values())
    verified = counts["agree"]
    return {
        "primary_engine": "region-ocr+labels+patterns",
        "shadow_engine": "full-page-ocr+independent-extraction",
        "compared": total,
        "verified_by_shadow": verified,
        "disagreements": counts["disagree"],
        "only_primary": counts["only_primary"],
        "only_shadow": counts["only_shadow"],
        "verification_rate": round(100 * verified / total, 1) if total else 0.0,
        "disagreement_details": disagreements,
    }


def _language_orderings(language: str) -> list[str]:
    """Return the language strings to run OCR with, reversed second-first when applicable.

    Tesseract's Devanagari output depends on which pack is primary in the lang string:
    "eng+hin" biases tie-breaks toward Latin and misreads handwritten `ग्राम` as `MA`;
    "hin+eng" reads it correctly. Running both catches labels that only one ordering sees.
    """
    parts = [p for p in language.replace(",", "+").split("+") if p]
    if len(parts) <= 1:
        return [language]
    reversed_ = "+".join(reversed(parts))
    return [language, reversed_]


def _merge_extractions(outs: list[ExtractionOutput]) -> ExtractionOutput:
    """Take best-per-field across multiple extraction passes (same page, different OCR)."""
    merged: dict[str, Extracted] = {}
    doc_type = "unknown"
    for o in outs:
        if o.doc_type != "unknown" and doc_type == "unknown":
            doc_type = o.doc_type
        for f in o.fields:
            if f.field_name not in merged or _merge_score(merged[f.field_name]) < _merge_score(f):
                merged[f.field_name] = f
    return ExtractionOutput(fields=list(merged.values()), doc_type=doc_type)


def _outside_band(fields: list[Extracted], band: tuple[int, int] | None) -> list[Extracted]:
    """Drop full-page readings that came from inside the table.

    The table is read properly by the table reader; the full-page pass sees its header
    row as an ordinary line and would otherwise register "मालिक का नाम" as an owner.
    """
    if not band:
        return fields
    top, bottom = band
    kept = []
    for f in fields:
        box = f.value_bbox or f.bbox
        if box and top <= box["y"] + box["h"] / 2 <= bottom:
            continue
        kept.append(f)
    return kept


def process_document(db: Session, doc: Document) -> Document:
    t0 = time.perf_counter()
    doc.status = DocStatus.PROCESSING
    doc.error = None
    db.commit()
    try:
        pages = load_pages(doc.stored_path)
        doc.page_count = len(pages)
        all_text: list[str] = []
        all_fields: dict[str, Extracted] = {}
        parcels: list[tuple[int, dict, dict, dict | None, str | None]] = []
        qualities: list[float] = []
        lexicon = CorrectionLexicon(db)
        reliability = field_reliability(db)
        absent = missing_languages(doc.language)
        diagnostics: dict = {
            "languages_requested": doc.language,
            "languages_used": resolve_languages(doc.language),
            "languages_missing": absent,
            "pages": [],
        }
        if absent:
            diagnostics["warning"] = (
                f"Language pack(s) not installed: {', '.join(absent)}. Text in that script "
                f"cannot be read and tabular records will not be detected. Install the pack "
                f"(e.g. hin.traineddata into Tesseract's tessdata folder) and re-run.")

        for pno, img in enumerate(pages, start=1):
            # Guard against wrong uploads (blank photo, selfie, floor picture).
            # Failing fast here beats a 20-second OCR pass returning nothing.
            ok, reason = looks_like_document(img)
            if not ok:
                diagnostics["pages"].append({
                    "page": pno, "quality": 0.0, "skew": 0.0,
                    "page_ocr_confidence": 0.0, "table_rows": 0,
                    "table_columns": [], "table_note": None,
                    "table_headers_read": None,
                    "content_check": {"is_document": False, "reason": reason},
                })
                continue

            # Sideways phone photos are common: someone shoots landscape while
            # the document itself is portrait, or an ID card upside-down. Try
            # each 90° orientation and keep the one that reads best.
            angle, rot_conf = detect_rotation(img)
            if angle != 0:
                img = apply_rotation(img, angle)

            # If this is a phone photo of a document on a background, warp it to an
            # orthogonal rectangle first — otherwise Tesseract OCRs the background too
            # and the ruled-table detector never sees straight column lines.
            img, warp = rectify(img)

            pre = preprocess(img)
            qualities.append(pre["quality"])
            out_path = Path(settings.processed_dir) / f"{doc.id}_p{pno}.png"
            cv2.imwrite(str(out_path), pre["binary"])

            page_ocr = run_ocr(pre, doc.language)
            all_text.append(f"--- page {pno} ({page_ocr.engine}/{page_ocr.languages}, "
                            f"conf {page_ocr.mean_conf}) ---\n{page_ocr.text}")

            table = extract_tables(pre, doc.language)
            diagnostics["pages"].append({
                "page": pno, "quality": pre["quality"], "skew": round(pre["skew"], 2),
                "page_ocr_confidence": page_ocr.mean_conf,
                "table_rows": len(table.rows),
                "table_columns": sorted(set(table.columns.values())) if table.columns else [],
                "table_note": table.reason,
                "table_headers_read": table.header_texts or None,
                "document_rectify": warp.as_dict(),
                "auto_rotation": {"angle": angle, "mean_conf": round(rot_conf, 1)} if angle else None,
                "content_check": {"is_document": True, "reason": ""},
            })
            band = None
            if table.rows and table.bbox:
                band = (table.bbox["y"], table.bbox["y"] + table.bbox["h"])
                for row in table.rows:
                    parcels.append((pno, dict(row.values), dict(row.confidences), row.bbox,
                                    table.area_unit_hint))
            elif len(pages) == 1:
                # Table detector missed the ruled grid — a common outcome on phone
                # photos where wooden noise, faded rules and residual perspective all
                # fight the morphology filter. Recover khasra numbers from row-start
                # patterns in the OCR text so a multi-parcel document still fans out
                # into its individual rows in the records showcase.
                khasras = find_khasra_rows(page_ocr.text)
                if len(khasras) >= 2:
                    diagnostics["pages"][-1]["synthesized_parcels"] = len(khasras)
                    for k in khasras:
                        parcels.append((pno, {"parcel_number": k}, {"parcel_number": 65.0},
                                        None, None))

            # Region OCR is run for each language ordering: Tesseract's Devanagari
            # readings differ when eng or hin is primary, and label extraction on
            # handwritten Hindi picks up different fields per ordering. Merge both.
            region_exts: list[ExtractionOutput] = []
            for order in _language_orderings(doc.language):
                r = read_regions(pre, order, band)
                r_ext = extract_fields(r, page=pno)
                refine_numeric_fields(pre, r_ext.fields)
                region_exts.append(r_ext)
            region_ext = _merge_extractions(region_exts)
            page_ext = extract_fields(page_ocr, page=pno)
            page_ext.fields = _outside_band(page_ext.fields, band)

            if pno == 1:
                detected = region_ext.doc_type if region_ext.doc_type != "unknown" else page_ext.doc_type
                if detected != "unknown":
                    doc.doc_type = detected
                if table.rows:
                    all_text.append(_table_as_text(table))

            # Shadow-Mode compare: region_ext acts as PRIMARY (dedicated region OCR,
            # higher label recall) and page_ext acts as SHADOW (independent full-page
            # OCR + extraction, different segmentation). Their per-field disagreements
            # are the demo signal for "AI vs AI, human decides".
            region_by_name = {f.field_name: f for f in region_ext.fields}
            page_by_name = {f.field_name: f for f in page_ext.fields}

            q_factor = 0.85 + 0.15 * (pre["quality"] / 100)
            for name, f in _merge_fields(region_ext, page_ext).items():
                if name in DIGIT_ONLY_FIELDS and not any(ch.isdigit() for ch in f.value):
                    continue          # a number field holding no number is not a reading

                f.confidence = round(f.confidence * q_factor * reliability.get(name, 1.0), 1)
                shadow_agreement, shadow_value, shadow_source = _compare_shadow(
                    name, region_by_name.get(name), page_by_name.get(name), f)
                f.shadow_value = shadow_value
                f.shadow_source = shadow_source
                f.shadow_agreement = shadow_agreement

                if name not in all_fields or all_fields[name].confidence < f.confidence:
                    all_fields[name] = f

        doc.diagnostics = diagnostics
        doc.ocr_text = "\n\n".join(all_text)
        doc.quality_score = round(sum(qualities) / len(qualities), 1) if qualities else 0.0

        # Jurisdiction chosen at upload is authoritative metadata, not an OCR guess.
        for meta_field in ("state", "district"):
            if getattr(doc, meta_field) and meta_field not in all_fields:
                all_fields[meta_field] = Extracted(meta_field, getattr(doc, meta_field), 100.0, "metadata", 1)

        db.query(ExtractedField).filter(ExtractedField.document_id == doc.id).delete()
        confs: list[float] = []
        for f in all_fields.values():
            if f.source == "metadata":
                confs.append(100.0)
                db.add(ExtractedField(document_id=doc.id, field_name=f.field_name, value=f.value,
                                      normalized_value=f.value, confidence=100.0, needs_review=False,
                                      source="metadata", page=1))
                continue
            value, learned = lexicon.apply(f.field_name, f.value)
            conf = min(99.0, f.confidence + 10) if learned else f.confidence
            confs.append(conf)
            # Shadow-Mode disagreement always routes a field to human review,
            # regardless of the primary AI's own confidence — that is exactly
            # the case where the reviewer's judgement is worth the most.
            needs_review = (conf < settings.auto_accept_threshold
                            or f.shadow_agreement == "disagree")
            db.add(ExtractedField(
                document_id=doc.id, field_name=f.field_name, value=f.value,
                normalized_value=normalize_field(f.field_name, value), confidence=conf,
                needs_review=needs_review,
                source="learned" if learned else f.source, page=f.page,
                bbox=f.value_bbox or f.bbox,
                shadow_value=f.shadow_value,
                shadow_source=f.shadow_source,
                shadow_agreement=f.shadow_agreement,
            ))

        _replace_parcels(db, doc, parcels, lexicon)
        confs.extend(p.confidence for p in doc.parcels)
        doc.overall_confidence = round(sum(confs) / len(confs), 1) if confs else 0.0
        db.flush()

        _upsert_record(db, doc)
        doc.status = DocStatus.EXTRACTED
        db.flush()

        results = validate_document(db, doc)
        has_error = any(r.severity == "error" and not r.passed for r in results)
        # Shadow-Mode summary: how the primary and shadow AIs compared, so the UI
        # can display "Shadow AI verified N of M fields" up front and block
        # auto-verification whenever they disagree on anything.
        shadow_stats = _shadow_summary(all_fields)
        diagnostics["shadow_mode"] = shadow_stats
        doc.diagnostics = diagnostics
        has_shadow_disagreement = shadow_stats["disagreements"] > 0
        if (not has_error and not has_shadow_disagreement
                and doc.overall_confidence >= settings.auto_accept_threshold and confs):
            doc.status = DocStatus.AUTO_VERIFIED
            doc.record.is_verified = True
            doc.record.verified_by = "system"
        else:
            doc.status = DocStatus.PENDING_REVIEW
    except Exception as exc:  # noqa: BLE001
        doc.status = DocStatus.FAILED
        doc.error = f"{type(exc).__name__}: {exc}"[:2000]
    doc.processing_ms = int((time.perf_counter() - t0) * 1000)
    db.commit()
    log_action(db, None, "system", "document.processed", "document", doc.id,
               {"status": doc.status.value, "confidence": doc.overall_confidence,
                "parcels": len(doc.parcels), "ms": doc.processing_ms})
    db.refresh(doc)
    return doc


def _table_as_text(table: TableResult) -> str:
    """Append the table reading to the document text so it is searchable and auditable."""
    order = [c for c in sorted(table.columns) if table.columns[c] != "map"]
    head = " | ".join(table.columns[c] for c in order)
    lines = [f"--- parcel table ({len(table.rows)} rows) ---", head]
    for row in table.rows:
        lines.append(" | ".join(str(row.values.get(table.columns[c], "")) for c in order))
    return "\n".join(lines)


def _replace_parcels(db: Session, doc: Document, rows, lexicon: CorrectionLexicon) -> None:
    db.query(LandParcel).filter(LandParcel.document_id == doc.id).delete()
    doc.parcels.clear()
    for idx, (page, values, confidences, bbox, unit_hint) in enumerate(rows):
        for col in PARCEL_TEXT_COLUMNS:
            if values.get(col):
                values[col], _ = lexicon.apply(col, values[col])
        area_text = values.get("area")
        area_value, area_unit, area_sqm = parse_area(area_text)
        if area_value is not None and area_sqm is None and unit_hint:
            # The cells carry a bare figure; the unit is printed once in the column header.
            area_value, area_unit, area_sqm = parse_area(f"{area_value} {unit_hint}")
        cell_confs = [c for c in confidences.values() if c > 0]
        confidence = round(sum(cell_confs) / len(cell_confs), 1) if cell_confs else 0.0
        parcel = LandParcel(
            document_id=doc.id, row_index=idx, page=page,
            parcel_number=values.get("parcel_number"),
            area_text=area_text, area_value=area_value,
            area_unit=area_unit or (values.get("area_unit") or None), area_sqm=area_sqm,
            land_classification=values.get("land_classification"), crop=values.get("crop"),
            owner_name=values.get("owner_name"), possessor_name=values.get("possessor_name"),
            father_name=values.get("father_name"), share=values.get("share"),
            remarks=values.get("remarks"),
            confidence=confidence, needs_review=confidence < settings.auto_accept_threshold,
            field_confidences={k: round(v, 1) for k, v in confidences.items()}, bbox=bbox,
        )
        db.add(parcel)
        doc.parcels.append(parcel)
    db.flush()


def _upsert_record(db: Session, doc: Document) -> LandRecord:
    vals = {f.field_name: (f.corrected_value if f.corrected_value is not None else f.normalized_value)
            for f in doc.fields}
    rec = doc.record or LandRecord(document_id=doc.id)
    for key in ("state", "district", "tehsil", "village", "survey_number", "khasra_number", "khata_number",
                "land_classification", "owner_name", "father_or_husband_name", "ownership_type",
                "mutation_number", "mutation_date", "registration_number", "registration_date", "record_year"):
        setattr(rec, key, vals.get(key))
    rec.state = doc.state or rec.state
    rec.district = doc.district or rec.district

    v, u, sqm = parse_area(vals.get("plot_area"))
    if v is None and vals.get("total_area"):
        v, u, sqm = parse_area(vals["total_area"])
    rec.plot_area_value, rec.plot_area_unit, rec.plot_area_sqm = v, u, sqm

    # Header fields that have no column of their own live in `extra` for export.
    rec.extra = {k: vals[k] for k in ("pargana", "total_area", "irrigated_area", "unirrigated_area",
                                      "record_date", "encumbrance") if vals.get(k)} or None

    # For a multi-parcel sheet the holding has no single khasra number; surface the list.
    if doc.parcels:
        numbers = [p.parcel_number for p in doc.parcels if p.parcel_number]
        if numbers and not rec.khasra_number:
            rec.khasra_number = numbers[0] if len(numbers) == 1 else f"{numbers[0]}-{numbers[-1]}"
        owners = [p.owner_name for p in doc.parcels if p.owner_name]
        if owners and not rec.owner_name:
            rec.owner_name = owners[0] if len(set(owners)) == 1 else f"{len(set(owners))} owners"
        total = sum(p.area_sqm for p in doc.parcels if p.area_sqm)
        if total and not rec.plot_area_sqm:
            rec.plot_area_sqm = round(total, 2)

    if not doc.record:
        db.add(rec)
        doc.record = rec
    return rec
