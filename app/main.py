"""
Medication Reconciliation Microservice
=======================================

Two-trigger architecture:

  TRIGGER 1 — Clinician clicks "Edit medications" (med list opens)
    → Frontend calls GET /baseline/{patient_id}
    → Returns active med snapshot; frontend holds it in memory
    → No UI change, no panel, nothing visible to the clinician

  TRIGGER 2 — Clinician clicks X to close med list (dlgopen onClosed)
    → Frontend passes the baseline snapshot back to POST /diff/{patient_id}
    → Second FHIR GET fires, diff computed
    → Panel ALWAYS appears — even if no changes — with pre-filled notes
    → Clinician edits if needed, clicks "Confirm & sign"
    → POST /submit writes two notes + checks AMC checkbox

Note destinations on submit:
  - Attestation note → native encounter note API (Visit Summary)
  - Patient note     → FHIR DocumentReference titled "Medication changes
                       made today", linked to encounter (patient-facing,
                       appears under Link/Add Issues to This Visit)
  - AMC checkbox     → PATCH native encounter API (always checked — Option B)
"""

import base64
import os
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends, Query, Body
from fastapi.middleware.cors import CORSMiddleware

from app.fhir import fetch_baseline, fetch_post_close
from app.diff import compute_diff
from app.models import BaselineResponse, DiffResponse, SubmitRequest, SubmitResponse
from app.note import build_attestation_note, build_patient_note
from app.amc import patch_amc_checkbox

app = FastAPI(
    title="Medication Reconciliation Service",
    description=(
        "Two-trigger reconciliation: baseline on med list open, "
        "diff + panel on med list close."
    ),
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FHIR_BASE = os.getenv(
    "OPENEMR_FHIR_BASE",
    "https://localhost:9300/apis/default/fhir",
)
NATIVE_BASE = os.getenv(
    "OPENEMR_NATIVE_BASE",
    "https://localhost:9300/apis/default/api",
)


async def get_token(authorization: str = Header(...)) -> str:
    """Extract Bearer token from the SMART on FHIR Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    return authorization.removeprefix("Bearer ")


# ─────────────────────────────────────────────────────────────────────────────
# TRIGGER 1 — Med list opens
# Called the moment the clinician clicks "Edit medications".
# Silent. No UI change. Returns the before-snapshot to the frontend.
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/baseline/{patient_id}",
    response_model=BaselineResponse,
    summary="Capture baseline snapshot when medication list opens",
)
async def get_baseline(
    patient_id: str,
    encounter_id: str = Query(..., description="Current encounter ID"),
    token: str = Depends(get_token),
):
    """
    GET 1 — fires when clinician clicks 'Edit medications'.

    Returns the active medication list as a raw snapshot.
    Frontend holds this in memory — microservice is stateless,
    nothing is stored server-side between the two triggers.

    No UI change occurs. Clinician sees nothing.
    """
    async with httpx.AsyncClient(verify=False) as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/fhir+json",
        }
        snapshot = await fetch_baseline(client, FHIR_BASE, patient_id, headers)

    return BaselineResponse(
        patient_id=patient_id,
        encounter_id=encounter_id,
        snapshot=snapshot,
    )


# ─────────────────────────────────────────────────────────────────────────────
# TRIGGER 2 — Med list closes (dlgopen onClosed callback)
# Called when clinician clicks X on the medication dialog.
# Always returns notes. Panel ALWAYS appears.
# ─────────────────────────────────────────────────────────────────────────────

@app.post(
    "/diff/{patient_id}",
    response_model=DiffResponse,
    summary="Compute diff when medication list closes — always shows panel",
)
async def post_diff(
    patient_id: str,
    encounter_id: str = Query(..., description="Current encounter ID"),
    clinician_name: str = Query(default="[Clinician]", description="Clinician display name for note"),
    baseline_snapshot: list[dict] = Body(..., description="Snapshot returned by /baseline, held by frontend"),
    token: str = Depends(get_token),
):
    """
    POST (with baseline body) — fires when clinician closes the medication list.

    GET 2 fires here to capture the post-edit state.
    Diff is computed against the baseline snapshot the frontend passes back.

    Panel ALWAYS appears — even when no changes were found.
    The clinician must confirm before anything is written.

    Both notes are pre-built here for the panel:
      - attestation_note: clinician signs this, goes to Visit Summary
      - patient_note: patient-facing, goes to Link/Add Issues as a document
    """
    async with httpx.AsyncClient(verify=False) as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/fhir+json",
        }
        post_close_meds = await fetch_post_close(client, FHIR_BASE, patient_id, headers)

    changes = compute_diff(baseline_snapshot, post_close_meds)

    attestation = build_attestation_note(patient_id, clinician_name, changes)
    patient_note = build_patient_note(changes)

    return DiffResponse(
        patient_id=patient_id,
        encounter_id=encounter_id,
        changes_detected=bool(changes),
        changes=changes,
        patient_note=patient_note,
        attestation_note=attestation,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SUBMIT — Clinician confirms and signs
# Only fires after explicit confirmation in the panel.
# Writes three things: encounter note, patient document, AMC checkbox.
# ─────────────────────────────────────────────────────────────────────────────

@app.post(
    "/submit/{patient_id}",
    response_model=SubmitResponse,
    summary="Write notes and check AMC checkbox after clinician confirmation",
)
async def submit(
    patient_id: str,
    body: SubmitRequest,
    token: str = Depends(get_token),
):
    """
    Fires only after the clinician clicks 'Confirm & sign' in the panel.

    Writes in order:
      1. Native encounter note — attestation note lands in Visit Summary
      2. FHIR DocumentReference — patient-facing note titled
         "Medication changes made today", linked to the encounter,
         visible under Link/Add Issues to This Visit
      3. AMC checkbox PATCH — "Medication Reconciliation Performed?" checked
         (Option B: always checked — reviewing the list IS reconciliation)
    """
    fhir_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/fhir+json",
    }
    native_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    document_id = None
    encounter_note_written = False
    amc_checked = False

    async with httpx.AsyncClient(verify=False) as client:

        # ── 1. Attestation note → Visit Summary (native encounter note) ───────
        note_resp = await client.post(
            f"{NATIVE_BASE}/patient/{patient_id}/encounter/{body.encounter_id}/note",
            json={
                "note": body.attestation_note,
                "authorized": 1,
            },
            headers=native_headers,
        )
        encounter_note_written = note_resp.status_code in (200, 201)
        if not encounter_note_written:
            print(
                f"[warn] Encounter note returned {note_resp.status_code}: {note_resp.text}"
            )

        # ── 2. Patient note → DocumentReference (Link/Add Issues to This Visit) ─
        # Posted as a FHIR DocumentReference with:
        #   - type: Medication summary (LOINC 56445-0)
        #   - title: "Medication changes made today"
        #   - context.encounter: links it to this visit
        #   - subject: patient
        # This is what surfaces in the patient portal and visit document list.
        doc_ref = {
            "resourceType": "DocumentReference",
            "status": "current",
            "type": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": "56445-0",
                    "display": "Medication summary Document",
                }]
            },
            "subject": {"reference": f"Patient/{patient_id}"},
            "context": {
                "encounter": [{"reference": f"Encounter/{body.encounter_id}"}]
            },
            "description": "Medication changes made today",
            "content": [{
                "attachment": {
                    "contentType": "text/plain",
                    "title": "Medication changes made today",
                    "data": base64.b64encode(body.patient_note.encode()).decode(),
                }
            }],
        }

        doc_resp = await client.post(
            f"{FHIR_BASE}/DocumentReference",
            json=doc_ref,
            headers=fhir_headers,
        )
        if doc_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=doc_resp.status_code,
                detail=f"DocumentReference failed: {doc_resp.text}",
            )
        document_id = doc_resp.json().get("id")

        # ── 3. AMC checkbox — always checked (Option B) ───────────────────────
        amc_checked = await patch_amc_checkbox(client, body.encounter_id, token)

    return SubmitResponse(
        status="written",
        document_id=document_id,
        encounter_note_written=encounter_note_written,
        amc_checked=amc_checked,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}
