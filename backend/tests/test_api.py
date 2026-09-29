"""End-to-end API tests: auth/RBAC -> upload -> pipeline -> validation -> verification -> dashboard."""
import os
from pathlib import Path

import pytest

os.environ["BHULEKH_DATABASE_URL"] = "sqlite:///./test_bhulekh.db"
os.environ["BHULEKH_STORAGE_DIR"] = "./test_storage"
os.environ["BHULEKH_UPLOAD_DIR"] = "./test_storage/uploads"
os.environ["BHULEKH_PROCESSED_DIR"] = "./test_storage/processed"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

SAMPLES = Path(__file__).parent.parent / "samples" / "generated"


@pytest.fixture(scope="module")
def client():
    for p in ("./test_bhulekh.db",):
        Path(p).unlink(missing_ok=True)
    with TestClient(app) as c:
        yield c


def login(client, user, pw):
    r = client.post("/api/v1/auth/login", data={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health(client):
    r = client.get("/health")
    body = r.json()
    assert r.status_code == 200
    assert "eng" in body["tesseract"]["languages_usable"]
    # A pack that is listed but unusable is the failure mode this endpoint exists to catch.
    assert not body["tesseract"]["languages_unusable"]


def test_login_and_rbac(client):
    assert client.post("/api/v1/auth/login", data={"username": "admin", "password": "wrong"}).status_code == 401
    viewer = login(client, "viewer", "Viewer@12345")
    r = client.post("/api/v1/documents/upload", headers=viewer,
                    files={"files": ("x.png", b"123", "image/png")})
    assert r.status_code == 403  # viewer cannot upload
    assert client.get("/api/v1/auth/users", headers=viewer).status_code == 403
    admin = login(client, "admin", "Admin@12345")
    assert client.get("/api/v1/auth/users", headers=admin).status_code == 200


def test_upload_process_and_extract(client):
    op = login(client, "operator", "Operate@12345")
    with open(SAMPLES / "ror_english_up.png", "rb") as f:
        r = client.post("/api/v1/documents/upload", headers=op,
                        data={"language": "eng+hin", "state": "Uttar Pradesh", "district": "Lucknow", "sync": "true"},
                        files={"files": ("ror_english_up.png", f, "image/png")})
    assert r.status_code == 201, r.text
    doc = r.json()[0]
    assert doc["status"] in ("auto_verified", "pending_review")
    d = client.get(f"/api/v1/documents/{doc['id']}", headers=op).json()
    fields = {f["field_name"]: f for f in d["fields"]}
    assert fields["khasra_number"]["normalized_value"] == "123/4"
    assert fields["owner_name"]["normalized_value"] == "Ram Prasad Verma"
    assert d["record"]["plot_area_sqm"] == 25000.0
    assert d["record"]["mutation_date"] == "2019-03-12"
    assert d["doc_type"] == "ror"
    rules = {v["rule_id"]: v for v in d["validations"]}
    assert rules["R08_cross_database"]["passed"] is True  # matches seeded LRMS mirror
    pytest.doc_id = doc["id"]


def test_hindi_document(client):
    op = login(client, "operator", "Operate@12345")
    with open(SAMPLES / "khasra_hindi_up.png", "rb") as f:
        r = client.post("/api/v1/documents/upload", headers=op, data={"language": "hin+eng", "sync": "true"},
                        files={"files": ("khasra_hindi_up.png", f, "image/png")})
    d = client.get(f"/api/v1/documents/{r.json()[0]['id']}", headers=op).json()
    fields = {f["field_name"]: f["normalized_value"] for f in d["fields"]}
    assert fields["khasra_number"] == "127"
    assert "सीता" in fields["owner_name"]
    assert d["record"]["plot_area_unit"] == "एकड़"


def test_duplicate_detection(client):
    op = login(client, "operator", "Operate@12345")
    # Default upload path now rejects byte-identical duplicates outright so the
    # user gets an immediate 409 with the earlier document id — no wasted OCR.
    with open(SAMPLES / "ror_english_up.png", "rb") as f:
        r = client.post("/api/v1/documents/upload", headers=op, data={"sync": "true"},
                        files={"files": ("again.png", f, "image/png")})
    assert r.status_code == 409, r.text
    payload = r.json()["detail"]
    assert payload["code"] == "duplicate_upload"
    assert payload["existing_document_id"]

    # Callers that need the older behaviour (upload anyway, let R06 flag it) can
    # opt in via `allow_duplicate=true`. R06 still fires post-processing so the
    # duplicate is visible in the validation panel.
    with open(SAMPLES / "ror_english_up.png", "rb") as f:
        r = client.post("/api/v1/documents/upload", headers=op,
                        data={"sync": "true", "allow_duplicate": "true"},
                        files={"files": ("again.png", f, "image/png")})
    assert r.status_code == 201
    d = client.get(f"/api/v1/documents/{r.json()[0]['id']}", headers=op).json()
    dup = [v for v in d["validations"] if v["rule_id"] == "R06_duplicate_document"][0]
    assert dup["passed"] is False and dup["severity"] == "error"
    assert d["status"] == "pending_review"


def test_pdf_multipage(client):
    op = login(client, "operator", "Operate@12345")
    with open(SAMPLES / "two_page_bundle.pdf", "rb") as f:
        r = client.post("/api/v1/documents/upload", headers=op, data={"sync": "true"},
                        files={"files": ("bundle.pdf", f, "application/pdf")})
    assert r.status_code == 201
    assert r.json()[0]["page_count"] == 2


def test_verification_workflow_and_learning(client):
    op = login(client, "operator", "Operate@12345")
    assert client.post(f"/api/v1/documents/{pytest.doc_id}/verify", headers=op,
                       json={"decision": "approve"}).status_code == 403  # operator can't verify
    ver = login(client, "verifier", "Verify@12345")
    r = client.post(f"/api/v1/documents/{pytest.doc_id}/verify", headers=ver, json={
        "decision": "approve", "remarks": "ok",
        "corrections": [{"field_name": "record_year", "value": "1430 Fasli"}]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "verified" and d["record"]["is_verified"] is True
    f = [x for x in d["fields"] if x["field_name"] == "record_year"][0]
    assert f["source"] == "human" and f["confidence"] == 100.0
    # learning: reprocessing the same OCR value now applies the learned correction
    r = client.post(f"/api/v1/documents/{pytest.doc_id}/reprocess", headers=op)
    d = client.get(f"/api/v1/documents/{pytest.doc_id}", headers=op).json()
    f = [x for x in d["fields"] if x["field_name"] == "record_year"][0]
    assert f["source"] == "learned" and f["normalized_value"] == "1430 Fasli"


def test_dashboard_audit_integration(client):
    admin = login(client, "admin", "Admin@12345")
    s = client.get("/api/v1/dashboard/stats", headers=admin).json()
    assert s["total_documents"] >= 4 and s["by_state"] and s["error_statistics"]
    a = client.get("/api/v1/audit", headers=admin, params={"entity_id": pytest.doc_id}).json()
    assert any(x["action"] == "document.approved" for x in a)
    csv = client.get("/api/v1/integration/records/export.csv", headers=admin)
    assert csv.status_code == 200 and "khasra_number" in csv.text
    gj = client.get("/api/v1/integration/records/export.geojson", headers=admin).json()
    assert gj["type"] == "FeatureCollection"
    lk = client.get("/api/v1/integration/lookup", headers=admin,
                    params={"village": "Rampur", "khasra_number": "123/4"}).json()
    assert lk and lk[0]["owner_name"] == "Ram Prasad Verma"
    lrms = login(client, "lrms", "Lrms@12345")
    r = client.post("/api/v1/integration/external/sync", headers=lrms,
                    json=[{"source": "DILRMP", "village": "X", "khasra_number": "1", "owner_name": "A"}])
    assert r.status_code == 201


def test_multi_parcel_khasra_table(client):
    """A ruled khasra sheet must yield one parcel per row, not a single flat record."""
    op = login(client, "operator", "Operate@12345")
    with open(SAMPLES / "khasra_table_multi_parcel.png", "rb") as f:
        r = client.post("/api/v1/documents/upload", headers=op,
                        data={"language": "hin+eng", "state": "Uttar Pradesh",
                              "district": "Kanpur Dehat", "sync": "true"},
                        files={"files": ("khasra_table.png", f, "image/png")})
    assert r.status_code == 201, r.text
    doc_id = r.json()[0]["id"]
    d = client.get(f"/api/v1/documents/{doc_id}", headers=op).json()

    assert d["doc_type"] == "khasra"
    parcels = d["parcels"]
    assert len(parcels) == 6, [p["parcel_number"] for p in parcels]

    assert [p["parcel_number"] for p in parcels] == ["143", "144", "145", "146", "147", "148"]
    assert [p["owner_name"] for p in parcels] == [
        "रामस्वरूप", "गोपाल सिंह", "सीताराम", "शिवकुमार", "रामकली देवी", "महेन्द्र पाल"]
    assert [p["area_text"] for p in parcels] == [
        "0.0800", "0.1200", "0.1500", "0.0900", "0.1100", "0.0600"]
    # The unit is printed once in the column header, so every row must still convert.
    assert [p["area_sqm"] for p in parcels] == [800.0, 1200.0, 1500.0, 900.0, 1100.0, 600.0]
    assert parcels[3]["land_classification"] == "बंजर"      # the one non-agricultural plot
    assert parcels[0]["land_classification"] == "कृषि"

    # Header fields come from the region reader, not the table.
    fields = {f["field_name"]: f["normalized_value"] for f in d["fields"]}
    assert fields.get("khata_number") == "0123"
    assert d["parcel_count"] == 6

    rules = {v["rule_id"]: v for v in d["validations"] if v["rule_id"].startswith("R10")}
    assert all(v["passed"] for v in rules.values()), rules
    pytest.table_doc_id = doc_id


def test_parcel_area_total_rule(client):
    """Parcel areas are reconciled against the total written on the sheet."""
    admin = login(client, "admin", "Admin@12345")
    d = client.get(f"/api/v1/documents/{pytest.table_doc_id}", headers=admin).json()
    total = [v for v in d["validations"] if v["rule_id"] == "R09_parcel_area_total"]
    assert total, d["validations"]
    assert total[0]["passed"] is True, total[0]["message"]     # 0.61 ha stated and summed
    assert "6100" in total[0]["message"]


def test_verifier_can_edit_parcel_rows(client):
    """Verifier edits to the table are persisted, audited and fed to the learning store."""
    ver = login(client, "verifier", "Verify@12345")
    d = client.get(f"/api/v1/documents/{pytest.table_doc_id}", headers=ver).json()
    parcels = [{"id": p["id"], "row_index": i, "parcel_number": p["parcel_number"],
                "area_text": p["area_text"], "land_classification": p["land_classification"],
                "crop": p["crop"], "owner_name": p["owner_name"],
                "possessor_name": p["possessor_name"]}
               for i, p in enumerate(d["parcels"])]
    parcels[0]["owner_name"] = "राम स्वरूप वर्मा"          # correct a name
    parcels.append({"id": None, "row_index": 6, "parcel_number": "149", "area_text": "0.0500",
                    "land_classification": "कृषि", "crop": "चना", "owner_name": "नई प्रविष्टि",
                    "possessor_name": "नई प्रविष्टि"})      # add a row the reader missed

    r = client.post(f"/api/v1/documents/{pytest.table_doc_id}/verify", headers=ver,
                    json={"decision": "approve", "corrections": [], "parcels": parcels,
                          "remarks": "checked against the register"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "verified"
    assert len(out["parcels"]) == 7
    assert out["parcels"][0]["owner_name"] == "राम स्वरूप वर्मा"
    assert out["parcels"][0]["source"] == "human"
    assert out["parcels"][6]["parcel_number"] == "149"
    assert out["parcels"][6]["area_sqm"] == 500.0          # re-parsed after the edit

    audit = client.get("/api/v1/audit", headers=ver,
                       params={"entity_id": pytest.table_doc_id}).json()
    approved = [a for a in audit if a["action"] == "document.approved"]
    assert approved and any(c.get("field") == "owner_name" for c in approved[0]["detail"]["corrections"])
