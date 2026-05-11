# med-reconciliation

A lightweight Python microservice for event-triggered medication reconciliation using OpenEMR's FHIR R4 and native REST APIs.

---

## How it works

```
Clinician clicks "Edit medications"
        │
        ▼
GET /baseline/{patient_id}                    ← fires silently on open
        │
        └─ GET /fhir/MedicationRequest?status=active
           Returns before-snapshot to frontend. Frontend holds it.
           No UI change. Clinician sees nothing.
        │
        ▼
Clinician works through med list normally
(add, stop, change — via OpenEMR's own dialog boxes)
        │
        ▼
Clinician clicks X to close the medication list
(dlgopen onClosed callback fires)
        │
        ▼
POST /diff/{patient_id}  +  baseline snapshot in body
        │
        └─ GET /fhir/MedicationRequest?status=active,stopped,on-hold
           Diff computed: baseline vs post-close state
        │
        ▼
Panel ALWAYS appears — even if no changes found
        │
        ├─ Shows pre-filled attestation note (clinician edits if needed)
        ├─ Shows patient-facing note preview
        └─ "Confirm & sign" button
        │
        ▼
Clinician clicks "Confirm & sign"
        │
        ▼
POST /submit/{patient_id}
        │
        ├─ POST /api/patient/{id}/encounter/{id}/note
        │       Attestation note → Visit Summary
        │
        ├─ POST /fhir/DocumentReference
        │       Title: "Medication changes made today"
        │       Linked to encounter → appears under Link/Add Issues to This Visit
        │       Patient-facing language, visible in patient portal
        │
        └─ PATCH /api/encounter/{id}
                "Medication Reconciliation Performed?" checkbox → checked
                Always checked (Option B) — reviewing the list IS reconciliation
```

---

## Quickstart

```bash
git clone https://github.com/your-org/med-reconciliation
cd med-reconciliation

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your OpenEMR base URLs

uvicorn app.main:app --reload
```

API docs at `http://localhost:8000/docs`.

---

## Endpoints

| Method | Path | Trigger | Description |
|--------|------|---------|-------------|
| `GET` | `/baseline/{patient_id}` | Med list **opens** | Captures before-snapshot. Silent. |
| `POST` | `/diff/{patient_id}` | Med list **closes** | Runs second GET, diffs, returns both notes. |
| `POST` | `/submit/{patient_id}` | Clinician confirms | Writes notes + checks AMC checkbox. |
| `GET` | `/health` | — | Liveness check. |

---

## Frontend integration (SMART app JavaScript)

```javascript
const patientId = FHIR.context.patientId;
const encounterId = FHIR.context.encounterId;
const token = FHIR.context.accessToken;
let baselineSnapshot = [];

// ── TRIGGER 1: Med list opens ─────────────────────────────────────────────
// Intercept the existing "Edit medications" button click.
// Fire baseline GET silently. No UI change.

document.querySelector('#edit-medications-btn').addEventListener('click', async () => {
  const res = await fetch(
    `/baseline/${patientId}?encounter_id=${encounterId}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  const data = await res.json();
  baselineSnapshot = data.snapshot;   // held in memory until close

  // Open the existing OpenEMR medication dialog as normal
  dlgopen('/interface/patient_file/medication_list.php', '_blank', 900, 600, '', '', {

    // ── TRIGGER 2: Med list closes ─────────────────────────────────────────
    // onClosed fires when clinician clicks X. Always fires.
    onClosed: async () => {
      const diffRes = await fetch(
        `/diff/${patientId}?encounter_id=${encounterId}&clinician_name=${encodeURIComponent(clinicianName)}`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(baselineSnapshot),
        }
      );
      const diff = await diffRes.json();

      // Panel ALWAYS appears — even if diff.changes_detected is false
      showReconciliationPanel(diff);
    }
  });
});

// ── SUBMIT: Clinician confirms ────────────────────────────────────────────
async function handleConfirm(attestationNote, patientNote) {
  await fetch(`/submit/${patientId}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      encounter_id: encounterId,
      attestation_note: attestationNote,
      patient_note: patientNote,
    }),
  });
}
```

---

## What gets written on submit

| Destination | Content | Where it appears |
|-------------|---------|-----------------|
| Native encounter note | Full attestation note, clinician-signed | Visit Summary |
| FHIR DocumentReference | "Medication changes made today" — plain language | Link/Add Issues to This Visit · patient portal |
| AMC checkbox | "Medication Reconciliation Performed?" | Encounter AMC Requires panel |

The DocumentReference is linked to the encounter via `context.encounter`, which is what causes it to appear under **Link/Add Issues to This Visit** in the Visit Summary — this is the standard FHIR mechanism for attaching documents to a specific encounter.

---

## The AMC checkbox

OpenEMR's encounter form has a native "Medication Reconciliation Performed?" checkbox under AMC Requires. No custom flag needed — this is the field.

**Option B is implemented:** the checkbox is always checked on submit, regardless of whether changes were found. The clinician confirmed they reviewed the list — that act is the reconciliation.

> **Important:** The exact PATCH field name must be confirmed against your instance. Open the encounter in the demo, check the box manually, and inspect the network tab in DevTools. That shows the exact endpoint and payload. Update `amc.py` accordingly.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENEMR_FHIR_BASE` | `https://localhost:9300/apis/default/fhir` | FHIR R4 base URL |
| `OPENEMR_NATIVE_BASE` | `https://localhost:9300/apis/default/api` | Native REST base URL |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | Comma-separated CORS origins |

---

## OpenEMR setup (Docker)

```yaml
services:
  openemr:
    image: openemr/openemr:latest
    ports:
      - 8300:80
      - 9300:443
    environment:
      OPENEMR_SETTING_rest_api: 1
      OPENEMR_SETTING_rest_fhir_api: 1
      OPENEMR_SETTING_site_addr_oath: 'https://localhost:9300'
      OE_USER: admin
      OE_PASS: pass
  mysql:
    image: mariadb:10.11
    environment:
      MYSQL_ROOT_PASSWORD: root
```

Register a SMART client and get a Bearer token: [OpenEMR API README](https://github.com/openemr/openemr/blob/master/API_README.md).

---

## Running tests

```bash
pytest tests/ -v
```

11 tests covering: diff logic (added/stopped/changed/unchanged), both note builders (with and without changes), and the two-trigger flow contract.

---

## Project structure

```
app/
  main.py      # FastAPI routes: /baseline, /diff, /submit
  fhir.py      # fetch_baseline() and fetch_post_close() — two separate GETs
  diff.py      # Diffs baseline vs post-close snapshot
  models.py    # Pydantic schemas for all request/response shapes
  note.py      # build_attestation_note() and build_patient_note()
  amc.py       # PATCH helper for the AMC checkbox
tests/
  test_diff.py # 11 unit tests
```

---

## Key design decisions

**Why does the panel always appear even when no changes are found?**
The clinician reviewed the list — that is the reconciliation, regardless of outcome. Always surfacing the panel means they always confirm, the AMC checkbox always gets checked, and the patient always receives a note ("No changes were made to your medications today"). This satisfies AMC reporting correctly and gives the patient useful communication either way.

**Why is the microservice stateless between the two triggers?**
The frontend holds the baseline snapshot in memory between open and close. This means the service works correctly across restarts, multiple instances, and scaled deployments with no shared cache or database.

**Why two separate note destinations?**
The attestation note (Visit Summary) is for the clinician's record — detailed, signed, clinical language. The patient note (Link/Add Issues to This Visit) is for the patient — plain language, brief, focused on what changed. These serve different audiences and different regulatory purposes.

**Why `dlgopen onClosed` and not a DOM observer?**
OpenEMR's `dlgopen()` function has a native `onClosed` callback used throughout the codebase (see `encounters.php`, `facility_user.php`). This is the clean, supported hook — no fragile DOM mutation observers needed.
