"""
Medication Reconciliation Microservice v0.3
============================================

Two-trigger architecture:

  TRIGGER 1 — Clinician clicks "Edit medications"
    GET /baseline/{patient_id}
    → FHIR GET active meds (before-snapshot)
    → Returns snapshot to frontend; frontend holds in memory
    → Nothing visible to clinician

  TRIGGER 2 — Clinician clicks X to close med list (dlgopen onClosed)
    POST /diff/{patient_id}  +  baseline snapshot in body
    → Second FHIR GET (active + stopped, newest first)
    → Diff computed: baseline vs post-close
    → Single combined note built (audit-ready + patient-readable)
    → Panel ALWAYS appears with pre-filled note
    → Clinician edits if needed, clicks "Confirm & sign"

  SUBMIT — Clinician confirms
    POST /submit/{patient_id}
    → POST /api/.../soap_note  (note → Visit Summary, authorized=1)
    → INSERT amc_misc_data     (AMC checkbox via direct DB — Option 1)

One note. One destination. AMC checkbox via direct DB insert.
"""

import os
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends, Query, Body
from fastapi.middleware.cors import CORSMiddleware

from app.fhir import fetch_baseline, fetch_post_close
from app.diff import compute_diff
from app.models import BaselineResponse, DiffResponse, SubmitRequest, SubmitResponse
from app.note import build_attestation_note
from app.amc import insert_amc_checkbox

app = FastAPI(
    title="Medication Reconciliation Service",
    description="Two-trigger reconciliation · one combined note · AMC via direct DB",
    version="0.3.0",
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
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    return authorization.removeprefix("Bearer ")


# ─────────────────────────────────────────────────────────────────────────────
# TRIGGER 1 — Med list opens
# ─────────────────────────────────────────────────────────────────────────────

@app.get(
    "/baseline/{patient_id}",
    response_model=BaselineResponse,
    summary="Capture baseline snapshot when medication list opens",
)
async def get_baseline(
    patient_id: str,
    encounter_id: str = Query(...),
    token: str = Depends(get_token),
):
    """
    Fires when clinician clicks 'Edit medications'. Silent — no UI change.
    Returns active med snapshot to frontend; frontend holds it in memory.
    Microservice is stateless — nothing stored server-side.
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
# TRIGGER 2 — Med list closes
# ─────────────────────────────────────────────────────────────────────────────

@app.post(
    "/diff/{patient_id}",
    response_model=DiffResponse,
    summary="Compute diff when medication list closes — panel always appears",
)
async def post_diff(
    patient_id: str,
    encounter_id: str = Query(...),
    clinician_name: str = Query(default="[Clinician]"),
    baseline_snapshot: list[dict] = Body(...),
    token: str = Depends(get_token),
):
    """
    Fires when clinician closes the medication list (dlgopen onClosed).
    Runs second FHIR GET, diffs against baseline, builds single combined note.
    Panel ALWAYS appears — even if no changes — so clinician always confirms.
    """
    async with httpx.AsyncClient(verify=False) as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/fhir+json",
        }
        post_close_meds = await fetch_post_close(client, FHIR_BASE, patient_id, headers)

    changes = compute_diff(baseline_snapshot, post_close_meds)
    note = build_attestation_note(patient_id, clinician_name, changes)

    return DiffResponse(
        patient_id=patient_id,
        encounter_id=encounter_id,
        changes_detected=bool(changes),
        changes=changes,
        note=note,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SUBMIT — Clinician confirms and signs
# ─────────────────────────────────────────────────────────────────────────────

@app.post(
    "/submit/{patient_id}",
    response_model=SubmitResponse,
    summary="Write soap note and check AMC checkbox after clinician confirmation",
)
async def submit(
    patient_id: str,
    body: SubmitRequest,
    token: str = Depends(get_token),
):
    """
    Fires only after clinician clicks 'Confirm & sign'.

    1. POST /api/patient/{pid}/encounter/{eid}/soap_note
       Note lands in Visit Summary. authorized=1 marks it as signed.
       Fields: objective (note text), authorized.
       Scope required: user/soap_note.write

    2. INSERT INTO amc_misc_data (Option 1 — direct DB)
       Checks "Medication Reconciliation Performed?" on the encounter.
       amc_id = 'med_reconc_amc', map_category = 'form_encounter'
       Requires OPENEMR_DB_* env vars and aiomysql installed.
    """
    native_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    soap_note_written = False

    async with httpx.AsyncClient(verify=False) as client:
        # ── 1. Write note to Visit Summary ────────────────────────────────────
        soap_resp = await client.post(
            f"{NATIVE_BASE}/patient/{patient_id}/encounter/{body.encounter_id}/soap_note",
            json={
                "subjective": "",
                "objective": body.note,
                "assessment": "Medication reconciliation completed.",
                "plan": "",
                "authorized": 1,
            },
            headers=native_headers,
        )
        soap_note_written = soap_resp.status_code in (200, 201)

        if not soap_note_written:
            raise HTTPException(
                status_code=soap_resp.status_code,
                detail=f"soap_note endpoint failed: {soap_resp.text}",
            )

    # ── 2. Check AMC checkbox via direct DB insert ────────────────────────────
    amc_checked = await insert_amc_checkbox(body.patient_id, body.encounter_id)

    return SubmitResponse(
        status="written",
        soap_note_written=soap_note_written,
        amc_checked=amc_checked,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.3.0"}
