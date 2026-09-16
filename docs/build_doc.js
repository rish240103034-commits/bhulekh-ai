// Builds docs/Bhulekh-AI_SIH2026_PS26018_Documentation.docx
const fs = require('fs')
const path = require('path')
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType, ShadingType,
  AlignmentType, BorderStyle, LevelFormat, PageBreak, TableOfContents, ImageRun,
} = require('docx')

const BRAND = '1F4E79'
const LIGHT = 'E8EEF7'
const W = 9026 // A4 text width in DXA (210mm - 2*25.4mm)

const P = (text, opts = {}) => new Paragraph({ spacing: { after: 120 }, ...opts, children: [new TextRun({ text, size: 22, ...opts.run })] })
const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 160 }, children: [new TextRun(t)] })
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 240, after: 120 }, children: [new TextRun(t)] })
const B = (t) => new Paragraph({ numbering: { reference: 'bul', level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: t, size: 22 })] })
let _ref = 'num1'
const NEWLIST = (r) => { _ref = r; return new Paragraph({ spacing: { after: 0 } }) }
const N = (t) => new Paragraph({ numbering: { reference: _ref, level: 0 }, spacing: { after: 60 }, children: [new TextRun({ text: t, size: 22 })] })
const RICH = (parts) => new Paragraph({ spacing: { after: 120 }, children: parts.map(([t, b]) => new TextRun({ text: t, bold: !!b, size: 22 })) })

const cell = (t, w, { head = false, shade } = {}) => new TableCell({
  width: { size: w, type: WidthType.DXA },
  shading: shade || head ? { type: ShadingType.CLEAR, fill: head ? BRAND : shade, color: 'auto' } : undefined,
  margins: { top: 70, bottom: 70, left: 100, right: 100 },
  children: (Array.isArray(t) ? t : [t]).map((line) => new Paragraph({
    spacing: { after: 0 }, children: [new TextRun({ text: String(line), bold: head, color: head ? 'FFFFFF' : undefined, size: 19 })] })),
})
const table = (widths, rows) => new Table({
  width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA }, columnWidths: widths,
  rows: rows.map((r, i) => new TableRow({ tableHeader: i === 0, children: r.map((c, j) => cell(c, widths[j], { head: i === 0, shade: i % 2 === 0 && i > 0 ? 'F5F7FA' : undefined })) })),
})
const img = (file, w, h) => fs.existsSync(file) ? new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 160 }, children: [new ImageRun({ type: 'png', data: fs.readFileSync(file), transformation: { width: w, height: h } })] }) : P('')
const caption = (t) => new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 240 }, children: [new TextRun({ text: t, italics: true, size: 18, color: '555555' })] })

const cover = [
  new Paragraph({ spacing: { before: 2400 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: 'Smart India Hackathon 2026', size: 28, color: BRAND, bold: true })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200 }, children: [new TextRun({ text: 'Bhulekh-AI', size: 64, bold: true, color: BRAND })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120 }, children: [new TextRun({ text: 'Intelligent Land Record Digitization and Validation System', size: 32 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 600 }, border: { top: { style: BorderStyle.SINGLE, size: 6, color: BRAND, space: 8 } }, children: [new TextRun({ text: 'Problem Statement ID 26018  ·  Category: Software  ·  Theme: Smart Automation', size: 22 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: 'Organization: Ministry of Rural Development  ·  Department of Land Resources (DoLR)', size: 22 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200 }, children: [new TextRun({ text: 'Technical documentation pack: solution overview, scope of study, architecture, component-wise technology, workflows, feasibility, impact', size: 20, color: '555555' })] }),
  new Paragraph({ children: [new PageBreak()] }),
  new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun('Contents')] }),
  new TableOfContents('Contents', { hyperlink: true, headingStyleRange: '1-2' }),
  new Paragraph({ children: [new PageBreak()] }),
]

const body = [
  H1('1. Executive summary'),
  P('Land records are the backbone of land administration, taxation, acquisition, dispute resolution and infrastructure planning. A large share of India’s historical records still exists as handwritten registers, scanned images, cadastral maps and legacy PDFs, frequently degraded, inconsistently formatted and written in several regional languages. Manual digitization is slow, expensive and error-prone, and the resulting inconsistencies weaken the reliability of Land Records Management Systems (LRMS) and the Digital India Land Records Modernization Programme (DILRMP).'),
  P('Bhulekh-AI is an end-to-end, AI-powered platform that ingests scanned records, enhances the images, performs multilingual OCR, extracts and classifies land-record fields with NLP, normalises values, validates them against business rules and external databases, scores its own confidence, auto-verifies high-confidence records and routes uncertain ones to a human verifier whose corrections continuously improve the models. Every action is audited, access is role-based, and the verified records are exposed through APIs, CSV and GeoJSON for LRMS, DILRMP, GIS and citizen-service platforms.'),
  P('The submission is a working, tested prototype (FastAPI + OpenCV + Tesseract + React) that runs entirely offline on commodity hardware. On a genuine handwritten khasra sheet from Kanpur Dehat (fasli 1987-88), scanned from aged paper, it read the ruled parcel grid exactly: all six khasra numbers, areas, land types and crops, and all twelve owner and possessor names in handwritten Devanagari — 36 of 36 cells — while recovering the khata number, village, tehsil, pargana and area totals from the header and footer bands, reconciling the parcel areas against the total written on the sheet, and flagging for human review the two fields it could not read with confidence. It handles English, Hindi and mixed documents, single and multi-page PDFs, detects duplicates and cross-checks against an LRMS mirror.'),

  H1('2. Problem statement (as given)'),
  P('Develop an AI-powered Intelligent Land Record Digitization and Validation System capable of automatically extracting structured information from scanned land records, handwritten documents, maps and legacy PDF files, using OCR, Computer Vision and NLP for printed and handwritten text in multiple Indian languages; classifying the information into predefined fields (landowner details, survey number, khasra number, khata number, plot area, village, tehsil, district, land classification, ownership details, mutation records, registration information); and providing document upload, automated processing, manual verification, audit tracking and integration with LRMS, DILRMP, GIS platforms and other government databases.'),
  H2('2.1 Problems addressed'),
  B('Poor image quality, faded text, damaged pages, skew and noise in legacy scans.'),
  B('Inconsistent layouts across states, years and record types (khatauni, khasra, 7/12 utara, jamabandi, mutation orders, deeds).'),
  B('Multiple regional languages and scripts, mixed English–vernacular text, Devanagari numerals and handwritten annotations.'),
  B('Manual data entry cost, latency and inconsistency; no confidence measure on digitized values.'),
  B('Lack of standardized, validated records that can be integrated with LRMS/DILRMP/GIS and used for citizen-centric services.'),

  H1('3. Scope of study'),
  table([2200, 3400, 3426], [
    ['Dimension', 'In scope (prototype)', 'Extension path (pilot / production)'],
    ['Input types', 'Scanned images (PNG/JPEG/TIFF/WEBP), single & multi-page PDFs, printed and lightly handwritten text', 'Camera captures from mobile app, cadastral map vectorisation, bulk ingestion from scanners/DMS'],
    ['Languages', 'English + Hindi + Marathi tested; any Tesseract Indic pack (Gujarati, Bengali, Tamil, Telugu, Kannada, Malayalam, Punjabi, Odia, Urdu) selectable at upload', 'Fine-tuned IndicOCR / TrOCR models for handwriting; language auto-detection'],
    ['Record types', 'Record of Rights (khatauni / 7-12 / jamabandi), khasra register, mutation orders, registration/sale deeds; auto-detection of type', 'State-specific templates, court decrees, land acquisition awards'],
    ['Document structure', 'Both layouts a real sheet uses: sparse label/value bands, and ruled tables holding many plots in one document (one row per khasra number). Grid found from the column rules, rows recovered from ink bands when the printed row rules are too faint to detect', 'Multi-page tables continuing across sheets, merged and split cells, rotated column headers'],
    ['Fields', '17 predefined fields covering all 13 categories in the PS (owner, father/husband, state, district, tehsil, village, survey/khasra/khata no., area + unit, land class, ownership type, mutation no./date, registration no./date, record year)', 'Co-owner shares, encumbrances, crop/irrigation columns, plot geometry'],
    ['Validation', 'Mandatory fields, identifier format, area plausibility, date consistency, low-confidence flagging, exact & fuzzy duplicate detection, cross-check with LRMS/DILRMP mirror', 'Live DILRMP/LRMS/Registration (IGRS) APIs, Aadhaar-seeded ownership checks, GIS parcel-geometry checks'],
    ['Workflow', 'Auto-verify ≥85 % + no rule errors; otherwise human review with side-by-side scan, field highlighting, corrections, approve/reject; re-run AI', 'Multi-level approval (data-entry → tehsildar → SDM), SLA timers, workload assignment'],
    ['Learning', 'Correction lexicon and per-field reliability recalibration from verifier edits', 'Periodic fine-tuning of OCR/NER models from the correction store; active learning'],
    ['Integration', 'REST/OpenAPI, JSON, CSV, GeoJSON exports, parcel lookup, external-record sync endpoint', 'Bhoomi/Bhulekh state portals, DILRMP MIS, Bhuvan/GIS layers, DigiLocker, API Setu'],
    ['Security', 'JWT auth, five roles, jurisdiction scoping, SHA-256 document integrity, full audit log', 'SSO (Parichay/NIC), encryption at rest, KMS, WORM audit storage, VAPT'],
    ['Deployment', 'Docker Compose (Postgres + API + Web); runs offline on a laptop', 'Kubernetes on NIC/MeghRaj cloud, GPU workers for handwriting models, autoscaling queues'],
  ]),

  H1('4. Proposed solution'),
  P('The solution is organised as a processing pipeline wrapped by a workflow and integration layer:'),
  N('Ingestion — secure upload of images/PDFs with metadata (language, document type, state, district); SHA-256 fingerprint; document repository.'),
  N('Image enhancement — grayscale, upscale/cap resolution, non-local-means denoising, skew estimation and correction, CLAHE contrast enhancement, adaptive Gaussian binarisation; an image-quality score (sharpness, contrast, resolution) feeds confidence.'),
  N('Three cooperating readers, because a land record is not one block of text. A khasra or khatauni sheet carries a ruled grid of plots plus sparse label/value bands above and below it, and a single page-segmentation mode cannot serve both: reading the whole page as running text turns the grid\u2019s column headers into apparent data and loses the rows entirely.'),
  N('(a) Table reader — the column rules are found by morphological filtering and also fix the table\u2019s vertical extent; row separators on old forms are frequently fainter than the handwriting itself, so rows are recovered from horizontal ink bands when the rules alone do not explain the body. Each cell is cropped, upscaled and read on its own. Numeric columns are re-read with a digit-only model, which removes the entire class of handwriting errors in which \u20181\u2019 is read as \u2018[\u2019 or \u2018/\u2019 and \u20184\u2019 as \u2018+\u2019. Land type and crop are snapped to their closed vocabularies. Cells whose ink density shows them to be blank are never sent to OCR, so an empty Remarks column cannot invent words from paper grain. Every row becomes one parcel record.'),
  N('(b) Region reader — the bands outside the table are split at their blank vertical gutters (these forms are laid out in columns: district and tehsil on the left, year, village and khata number on the right) and each block is read upscaled. Numeric values are then re-read from their own pixels with a digit-only model, which is what recovers a khata number such as 0123 from a first reading of \u20182/22\u2019.'),
  N('(c) Full-page OCR — Tesseract 5 LSTM with the selected Indic packs, run in each language ordering because the result depends on which pack is primary; kept as the searchable text of record and as a fallback source of fields.'),
  N('NLP field extraction — line-wise parsing of “label : value” structures using a multilingual lexicon (English, Hinglish transliterations, Devanagari) with fuzzy label matching (RapidFuzz) tolerant to OCR noise. Real forms separate label from value with a dash as often as a colon, and a full-width header line interleaves several label/value pairs with the document title, so values are cut where form furniture begins.'),
  N('Normalisation — Devanagari-to-ASCII digits, common OCR confusions in numeric fields, ISO dates, honorific stripping, area unit parsing (ha, acre, bigha, biswa, guntha, kanal, marla, sq m…) with conversion to square metres.'),
  N('Confidence scoring — per field: 35 % label-match score + 40 % OCR token confidence + 25 % value-format validity, modulated by page quality and by the learned reliability of that field; fields under the review threshold are flagged.'),
  N('Validation engine — pluggable rules R01–R12 (mandatory fields, area range, identifier format, date consistency, low-confidence fields, byte-identical duplicates, fuzzy duplicate parcels, cross-database check against LRMS/DILRMP records, plus parcel-level rules: every row numbered and unique, the parcel areas reconciled against the total written on the sheet, the irrigated/unirrigated split reconciled against that total, and implausible years caught — the arithmetic the sheet carries within itself is the strongest available check on a handwritten reading).'),
  N('Decision — no rule errors and confidence ≥ 85 % ⇒ auto-verified; otherwise pending human review.'),
  N('Human-assisted verification — the verifier sees the scan beside the extracted fields and, underneath, the parcel grid as an editable table: clicking a row highlights it on the scan, rows can be corrected, added where the reader missed one, or removed. Corrections are stored with the raw OCR value.'),
  N('Learning loop — stored corrections form a lexicon that auto-applies to recurring OCR errors and a per-field accuracy statistic that recalibrates confidence; the same store is the training set for model fine-tuning.'),
  N('Outputs & integration — a canonical LandRecord for the holding plus one LandParcel per plot, mirroring how Indian land administration actually works: a khata covers several khasra numbers, each with its own area, land type, crop and cultivator. Exposed through REST APIs, CSV for LRMS import, GeoJSON for GIS and parcel lookup for citizen services; dashboards; audit trail; RBAC.'),

  H1('5. System architecture'),
  img(path.join(__dirname, 'architecture.png'), 620, 400),
  caption('Figure 1 — Logical architecture of Bhulekh-AI'),
  H2('5.1 Data model'),
  table([2100, 6926], [
    ['Entity', 'Purpose'],
    ['User', 'Login, role (admin / verifier / operator / viewer / integration), optional state/district jurisdiction'],
    ['Document', 'Uploaded file, SHA-256, type, language, status lifecycle, OCR text, quality, confidence, timings, error'],
    ['ExtractedField', 'Field name, raw OCR value, normalised value, confidence, needs-review flag, source (rule / ml / learned / metadata / human), page, bounding box, correction'],
    ['LandRecord', 'The holding: canonical header record (village, tehsil, district, khata no., year, totals), verification status, verifier, timestamp, optional GIS geometry'],
    ['LandParcel', 'One plot inside the holding: khasra/survey number, area (text, value, unit, m²), land type, crop, owner, possessor, share, remarks, per-cell confidences and the row\u2019s position on the scan'],
    ['ValidationResult', 'Rule id, severity, pass/fail, message, affected field'],
    ['FieldCorrection', 'Learning store: OCR value → corrected value with language & document type'],
    ['ExternalRecord', 'Mirror of LRMS / DILRMP rows used for cross-database verification (replaceable by live API)'],
    ['AuditLog', 'Actor, action, entity, detail JSON, IP, timestamp for every login, upload, view, verify, export, sync'],
  ]),
  H2('5.2 Document lifecycle'),
  P('uploaded → processing → extracted → auto_verified | pending_review → verified | rejected  (failed on unrecoverable error; any state can be re-processed).'),

  H1('6. Suggested component-wise technology'),
  table([2300, 3300, 3426], [
    ['Component', 'Technology (prototype)', 'Rationale / production alternative'],
    ['Image pre-processing', 'OpenCV 4 (denoise, deskew, CLAHE, adaptive threshold), NumPy, Pillow, pdf2image + Poppler', 'Mature, CPU-only, deterministic; DocTR/LayoutParser for layout analysis of tabular registers'],
    ['OCR — printed text', 'Tesseract 5 LSTM (tessdata_fast Indic packs) via pytesseract, multi-pass language ordering', 'Open source, offline, 100+ languages; Google Vision / Azure Document Intelligence as optional cloud fallback'],
    ['OCR — handwriting', 'EasyOCR (pluggable engine)', 'TrOCR / IndicOCR / PaddleOCR fine-tuned on the correction store; GPU workers'],
    ['Table / layout analysis', 'OpenCV morphology for the column rules, ink-band projection for faint row rules, connected-component filtering so a Devanagari headline stroke is not mistaken for a rule, per-cell crop and OCR', 'DocTR / LayoutParser or a table-transformer model for unruled and merged-cell layouts'],
    ['Numeric field reading', 'Second Tesseract pass constrained to digits, scored against each column\u2019s expected shape', 'Digit-specific handwriting model (MNIST-style CNN or TrOCR) fine-tuned on verified cells'],
    ['NLP field extraction', 'Rule + lexicon + fuzzy matching (RapidFuzz), regex validators, closed-vocabulary snapping, document-type classifier', 'Layout-aware NER (LayoutLMv3 / IndicBERT token classification) trained on verified records'],
    ['Normalisation', 'Custom Python (Devanagari digits, units → m², ISO dates, honorifics)', 'State-specific unit tables (bigha variants), indic-transliteration for name matching'],
    ['Validation engine', 'Pluggable Python rule registry; RapidFuzz similarity; SQLAlchemy queries', 'Rule DSL (JSON) editable by domain admins; Great Expectations for data quality'],
    ['Learning mechanism', 'Correction lexicon + per-field reliability; FieldCorrection store', 'Scheduled fine-tuning jobs (MLflow), active-learning sampling of low-confidence docs'],
    ['Backend / API', 'Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Uvicorn; OpenAPI docs', 'Async workers (Celery/RQ + Redis) for throughput; API gateway (API Setu compatible)'],
    ['Database', 'SQLite (dev) / PostgreSQL 16', 'PostgreSQL + PostGIS for parcel geometry; read replicas for dashboards'],
    ['Document repository', 'File storage with SHA-256, processed page cache', 'S3-compatible object store (MinIO) with versioning & WORM audit bucket'],
    ['Auth & RBAC', 'JWT (python-jose), PBKDF2 password hashing, role hierarchy, jurisdiction scoping', 'SSO via Parichay / Keycloak (OIDC), MFA, ABAC policies'],
    ['Frontend', 'React 18, Vite, React Router, Axios, Recharts', 'Accessibility (GIGW), i18n for vernacular UI, PWA for field offices'],
    ['Dashboards', 'Aggregation service + Recharts (status, accuracy, pending, errors, state/district progress, throughput)', 'Superset / Metabase for ad-hoc MIS; DILRMP MIS export'],
    ['Integration', 'REST JSON, CSV, GeoJSON, parcel lookup, external sync endpoint', 'GIS via WFS/WMS (GeoServer), Bhuvan; DigiLocker; message bus (Kafka) for event-driven sync'],
    ['Security & audit', 'Audit log on every action, CORS, size/type limits, hashed passwords', 'TLS everywhere, encryption at rest, CERT-In VAPT, log SIEM'],
    ['Deployment', 'Docker Compose (Postgres, API, nginx web)', 'Kubernetes on NIC MeghRaj; GPU node pool for handwriting models; CI/CD'],
    ['Testing', 'pytest end-to-end API tests, synthetic degraded-scan generator', 'Ground-truth benchmark set from DoLR, CER/WER & field-F1 tracking'],
  ]),

  H1('7. Key workflows'),
  H2('7.1 Operator: upload and processing'), NEWLIST('num2'),
  N('Operator signs in (jurisdiction-scoped) and drags files onto the upload page, choosing language and type.'),
  N('API stores the file, fingerprints it, logs the action and queues processing (background task / worker).'),
  N('Pipeline runs; status is visible live in the document list; failures are captured with the error message.'),
  H2('7.2 Verifier: human-assisted review'), NEWLIST('num3'),
  N('Pending-review queue lists documents with quality and confidence; verifier opens one.'),
  N('Scan and extracted fields are shown side by side; low-confidence fields are highlighted; clicking a field locates it on the page.'),
  N('Verifier corrects values (raw OCR shown on hover), adds remarks and approves or rejects; validations re-run on the corrected data.'),
  N('Corrections are stored for learning; the canonical record is committed and audited.'),
  H2('7.3 Integration: LRMS / DILRMP / GIS'), NEWLIST('num4'),
  N('Integration client syncs reference records into the cross-verification mirror (POST /integration/external/sync).'),
  N('Downstream systems pull verified records (JSON pages, CSV bulk export, GeoJSON) or query a parcel by village + khasra/survey number.'),
  N('Every export is audited with actor, filters and timestamp.'),
  H2('7.4 Screens of the working prototype'),
  img(path.join(__dirname, 'shot_dashboard.png'), 620, 400),
  caption('Figure 2 — Dashboard: processed documents, extraction confidence, field accuracy, pending verification, error statistics, state/district progress'),
  img(path.join(__dirname, 'shot_verify.png'), 620, 640),
  caption('Figure 3 — Verification screen: scan beside extracted fields with confidence bars, validation results and approve/reject'),

  H1('8. Validation rules (initial set)'),
  table([1700, 1300, 6026], [
    ['Rule', 'Severity', 'Check'],
    ['R01 mandatory fields', 'error', 'Owner name and village present; at least one of survey / khasra / khata number'],
    ['R02 area range', 'error/warn', 'Area parsed with a recognised unit and within 1 m² – 500 ha'],
    ['R03 identifier format', 'warning', 'Survey/khasra/khata numbers alphanumeric with digits, no stray characters'],
    ['R04 date consistency', 'warning', 'Dates parse to ISO; mutation date not before registration date'],
    ['R05 low-confidence fields', 'warning', 'Any field below the review threshold is listed for the verifier'],
    ['R06 duplicate document', 'error', 'SHA-256 identical to an existing upload'],
    ['R07 duplicate record', 'error/warn', 'Same village + parcel identifier: owner similarity ≥ 90 % ⇒ duplicate; else possible transfer'],
    ['R08 cross-database', 'warning', 'Match in LRMS/DILRMP mirror: owner-name similarity, area deviation ≤ 5 %, khata equality'],
  ]),

  H1('9. Results'),
  H2('9.1 A real handwritten khasra sheet'),
  P('The system was tested on a genuine handwritten khasra (Kanpur Dehat, Akbarpur tehsil, village Bilhaur, fasli year 1987-88), scanned from aged paper and carrying six plots in a ruled grid with a hand-drawn parcel map alongside. Every cell of the parcel table was read correctly — all six khasra numbers (143–148), all six areas, all six land types and crops, and all twelve owner and possessor names in handwritten Devanagari — a complete and exact reading of the grid. From the header bands it recovered the khata number, tehsil, village, pargana, total area, the irrigated/unirrigated split and the encumbrance line. The parcel areas summed to 0.61 ha against a stated total of 0.62 ha, inside the 2 % tolerance, and the irrigated plus unirrigated areas reconciled with the total to within 20 m². Two fields were correctly flagged for human review rather than silently accepted: a date whose handwritten 9 reads as a 1 (giving an implausible year 1187) and the fasli year. This is the intended behaviour of a confidence-driven pipeline — read what can be read, and surface what cannot.'),
  H2('9.2 Synthetic benchmark'),
  P('The repository also ships a generator that renders realistic khatauni / khasra / 7-12 / mutation documents in English, Hindi and mixed scripts and degrades them (noise, fading, blur, stains, skew), including a ruled multi-parcel khasra sheet used as the regression test for the table reader. On this set the prototype achieved the following on a CPU-only laptop:'),
  table([3300, 1600, 1600, 2526], [
    ['Document', 'Degradation', 'Confidence', 'Outcome'],
    ['Record of Rights (English, UP)', 'light', '91.8 %', 'auto-verified; all 13 fields correct; LRMS cross-check passed'],
    ['Khatauni (Hindi, Devanagari)', 'medium', '87.6 %', 'auto-verified; owner, khasra, area (एकड़) correct'],
    ['7/12 utara (mixed, MH)', 'heavy + stains', '82.7 %', 'routed to review; identifiers & khata correct, owner flagged'],
    ['Mutation order (MP, faded)', 'medium', '92.2 %', 'auto-verified; mutation no./date correct'],
    ['Re-upload of the first record', 'light', '90.4 %', 'blocked as duplicate (R06/R07)'],
    ['Two-page PDF bundle', 'light + medium', '88.3 %', 'both pages processed in ~20 s'],
    ['Multi-parcel khasra grid (Hindi)', 'clean', '92.6 %', 'all 6 parcel rows read exactly; areas converted via the unit in the column header'],
    ['Real handwritten khasra (Kanpur Dehat, 1987)', 'aged paper, handwritten', '84.4 %', '6/6 parcel rows exact (36/36 cells); 2 header fields flagged for review'],
  ]),
  P('Processing time is 6–10 s per page for a plain form and 15–35 s for a ruled multi-parcel sheet on CPU, since each table cell is read individually; blank cells are skipped, which keeps the cost proportional to the data actually present. The eleven-test end-to-end suite passes: authentication and RBAC, English and Hindi extraction, multi-page PDF, duplicate detection, the verification and learning loop, dashboards, audit and integration APIs, plus the multi-parcel table (one parcel per row with correct numbers, owners and areas), the parcel-total reconciliation rule, and verifier editing of the grid including adding a row the reader missed.'),

  H1('10. Feasibility and viability'),
  B('Technical: built entirely on proven open-source components; runs offline on commodity hardware; GPU optional; already tested end-to-end.'),
  B('Operational: mirrors the existing tehsil workflow (data entry → verification) so adoption needs no process change; verifier effort is concentrated on flagged fields only.'),
  B('Scalability: stateless API + queue workers scale horizontally; per-document processing is embarrassingly parallel; Postgres/PostGIS handles national-scale volumes.'),
  B('Data & privacy: on-premise deployment on NIC infrastructure; no data leaves government control; RBAC and audit meet DILRMP security expectations.'),
  B('Risks & mitigation: handwriting accuracy (mitigated by human-in-the-loop review, the digit-only pass for numeric fields, closed-vocabulary snapping, and fine-tuning on the accumulated correction store); layout diversity (the column lexicon, field lexicon and rules are data-driven and state-configurable, and the table reader depends only on the presence of ruled columns); OCR language pack quality (multi-pass ordering plus pluggable engines).'),
  B('Known limitation: the table reader requires ruled column separators. A sheet whose columns are implied by whitespace alone, or whose cells are merged or split, still falls back to line-based extraction and to the verifier; a table-structure model is the upgrade path.'),

  H1('11. Impact and benefits'),
  B('Speed: minutes instead of days per register; bulk backlog clearance of legacy records.'),
  B('Accuracy: confidence-driven review and cross-database checks reduce transcription and ownership errors; duplicate detection prevents double entries.'),
  B('Cost: manual effort concentrated on the ~20–30 % of fields flagged for review; improves over time as the learning loop absorbs corrections.'),
  B('Governance: complete audit trail, dashboards for state/district progress, standardised records that plug into LRMS, DILRMP, GIS and citizen services (e.g., certified copies, mutation status).'),
  B('Citizen impact: faster property transactions, fewer land disputes, transparent ownership information.'),

  H1('12. Repository & how to run'),
  P('backend/ — FastAPI service (app/api, app/pipeline, app/services, tests, samples); frontend/ — React dashboard; docker-compose.yml — one-command deployment. Local: install Tesseract with Indic packs and Poppler, then “pip install -r requirements.txt”, “uvicorn app.main:app”, and “npm install && npm run dev” in frontend. Swagger UI at /docs. Demo users: admin/admin123, verifier/verify123, operator/operate123, viewer/view123, lrms/lrms123.'),
]

const doc = new Document({
  creator: 'Bhulekh-AI team', title: 'Bhulekh-AI — SIH 2026 PS 26018',
  styles: {
    default: { document: { run: { font: 'Calibri', size: 22 } } },
    paragraphStyles: [
      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { size: 32, bold: true, color: BRAND, font: 'Calibri' }, paragraph: { outlineLevel: 0, spacing: { before: 360, after: 160 } } },
      { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { size: 26, bold: true, color: '2E75B6', font: 'Calibri' }, paragraph: { outlineLevel: 1, spacing: { before: 240, after: 120 } } },
    ],
  },
  numbering: { config: [
    { reference: 'bul', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
    ...['num1','num2','num3','num4'].map((reference) => ({ reference, levels: [{ level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 360 } } } }] })),
  ] },
  sections: [{
    properties: { page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } },
    children: [...cover, ...body],
  }],
})

Packer.toBuffer(doc).then((buf) => {
  const out = path.join(__dirname, 'Bhulekh-AI_SIH2026_PS26018_Documentation.docx')
  fs.writeFileSync(out, buf)
  console.log('wrote', out)
})
