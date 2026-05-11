# med-reconciliation

A lightweight Python microservice that detects medication changes in OpenEMR and surfaces a pre-filled reconciliation note — triggered only when a clinician clicks **Edit medications**, never on chart open.

## How it works

```
Clinician clicks "Edit medications"
        │
        ▼
GET /reconcile/{patient_id}          ← your EHR calls this with the SMART Bearer token
        │
        ├─ GET /fhir/MedicationRequest?status=active
        ├─ GET /fhir/MedicationRequest?status=active,stopped&_sort=-date
        └─ diff computed in-process
        │
        ▼
{ changes_detected: true/false, changes: [...], attestation_note: "..." }
        │
   if true → show reconciliation panel to clinician
   if false → medications editor opens normally, nothing extra shown
        │
        ▼
Clinician edits note, clicks "Confirm & sign"
        │
        ▼
POST /reconcile/{patient_id}/submit  ← fires two calls on confirmation
        │
        ├─ POST /fhir/DocumentReference               (attestation note → patient chart)
        └─ PATCH /api/encounter/{encounter_id}/amc    (checks "Medication Reconciliation Performed?")
```

No polling. No background jobs. One GET on a user action, one POST on explicit confirmation. The AMC checkbox is OpenEMR's own built-in encounter field — no custom flags or FHIR extensions needed.

---

## Quickstart

```bash
git clone https://github.com/your-org/med-reconciliation
cd med-reconciliation

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your OpenEMR FHIR base URL

uvicorn app.main:app --reload
```

API docs auto-generated at `http://localhost:8000/docs`.

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reconcile/{patient_id}` | Fetch diff + draft note. Requires `Authorization: Bearer <token>`. |
| `POST` | `/reconcile/{patient_id}/submit` | Write confirmed note to OpenEMR + check the AMC checkbox. |
| `GET` | `/health` | Liveness check. |

### Example request

```bash
curl -H "Authorization: Bearer <your_smart_token>" \
     http://localhost:8000/reconcile/00482917
```

### Example response (changes detected)

```json
{
  "patient_id": "00482917",
  "changes_detected": true,
  "changes": [
    { "type": "stopped",  "medication": "Omeprazole",    "detail": "Status: stopped" },
    { "type": "changed",  "medication": "Metformin",     "detail": "500 mg BID → 1000 mg BID" },
    { "type": "added",    "medication": "Empagliflozin", "detail": "New: 10 mg once daily" }
  ],
  "attestation_note": "MEDICATION RECONCILIATION NOTE\nDate: ..."
}
```

### Example response (no changes)

```json
{
  "patient_id": "00482917",
  "changes_detected": false,
  "changes": [],
  "attestation_note": null
}
```

When `changes_detected` is `false`, your frontend should open the medications editor normally — no panel, no interruption.

---

## The AMC checkbox

OpenEMR's encounter form includes a native **"Medication Reconciliation Performed?"** checkbox under the AMC Requires panel. This is the reconciliation flag — no custom FHIR extension or Task resource needed.

When the clinician clicks "Confirm & sign", the `/submit` endpoint fires two calls in sequence:

```python
# 1. Write the attestation note to the patient chart
POST /apis/default/fhir/DocumentReference

# 2. Check the AMC checkbox on the current encounter
PATCH /apis/default/api/encounter/{encounter_id}
Body: { "pc_recurrtype": 1 }   # ← exact field TBD — see note below
```

> **Important:** The exact native API call for the AMC checkbox needs to be confirmed against your OpenEMR instance. The cleanest way to find it: open the encounter in the demo, check the box manually, and inspect the network tab in DevTools. OpenEMR's own UI will show you the exact endpoint and payload it uses. That's the call to replicate.
>
> The checkbox lives in `form_encounter` or a related AMC table internally — not as a FHIR resource — so it's reached via the native REST API (`/apis/default/api/...`), not the FHIR base URL.

Once checked, the checkbox persists on the encounter record. Re-running the diff on a future edit-medications click will return `changes_detected: false` if nothing new has changed, so the panel won't reappear.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENEMR_FHIR_BASE` | `https://localhost:9300/apis/default/fhir` | Base URL for your OpenEMR FHIR R4 endpoint |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | Comma-separated CORS origins |

---

## OpenEMR setup

This service expects an OpenEMR instance with the FHIR API enabled. The fastest path is Docker:

```yaml
# docker-compose.yml
services:
  openemr:
    image: openemr/openemr:latest
    ports:
      - 8300:80
      - 9300:443
    environment:
      OPENEMR_SETTING_rest_fhir_api: 1
      OPENEMR_SETTING_site_addr_oath: 'https://localhost:9300'
      OE_USER: admin
      OE_PASS: pass
  mysql:
    image: mariadb:10.11
    environment:
      MYSQL_ROOT_PASSWORD: root
```

Then register a SMART client and get a Bearer token — see [OpenEMR API README](https://github.com/openemr/openemr/blob/master/API_README.md).

The service uses the token your EHR passes at SMART launch. It never stores credentials.

---

## Running tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Project structure

```
app/
  main.py      # FastAPI app, route handlers
  fhir.py      # FHIR R4 fetch helpers (MedicationRequest)
  diff.py      # Detects added / stopped / changed medications
  models.py    # Pydantic request/response models
  note.py      # Builds the pre-filled attestation note text
tests/
  test_diff.py # Unit tests for the diff logic
```

---

## Key design decisions

**Why trigger on the edit click, not on chart open?**
A banner that fires on every chart open would interrupt clinicians constantly. The reconciliation prompt is only relevant when they're already thinking about medications — so that's the only moment it appears.

**Why a microservice and not a plugin?**
Keeps the reconciliation logic independently deployable and testable. The EHR calls one endpoint; it doesn't need to know anything about the diff or note-building logic.

**Why no database?**
State lives in OpenEMR. This service is stateless — it reads from FHIR, computes a diff in-process, and writes back via FHIR. No extra persistence layer to manage.

**Does the diff run against signed or unsigned medication changes?**
The diff runs against whatever is currently written to OpenEMR's database — not the signed state. If your workflow commits medication changes on save (before signing), the diff picks them up immediately. If your workflow only commits on sign, the trigger point for the GET may need to shift to post-signing. Confirm this against your instance before deploying: check whether a draft/unsigned medication order is visible via `GET /fhir/MedicationRequest` before the encounter is signed.

**Why use the native AMC checkbox instead of a custom flag?**
OpenEMR already has a "Medication Reconciliation Performed?" checkbox built into every encounter's AMC Requires panel. Patching that field programmatically after confirmed reconciliation means no custom FHIR extensions, no separate tracking table, and the checkbox shows up exactly where clinical staff already expect to see it.
