"""
Medication Reconciliation Microservice
Connects to OpenEMR FHIR R4 API and detects medication changes
at the point a clinician opens the medication editor.
"""

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from typing import Optional

from app.fhir import fetch_active_meds, fetch_all_meds
from app.diff import compute_diff
from app.models import ReconciliationResult, MedChange
from app.note import build_attestation_note

app = FastAPI(
    title="Medication Reconciliation Service",
    description="Event-triggered med reconciliation using OpenEMR FHIR R4",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENEMR_BASE = os.getenv("OPENEMR_FHIR_BASE", "https://localhost:9300/apis/default/fhir")


async def get_token(authorization: str = Header(...)) -> str:
    """Extract Bearer token from Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    return authorization.removeprefix("Bearer ")


@app.get("/reconcile/{patient_id}", response_model=ReconciliationResult)
async def reconcile(patient_id: str, token: str = Depends(get_token)):
    """
    Called when clinician clicks 'Edit medications'.
    Returns a diff of medication changes and a pre-filled attestation note.
    Only returns changes_detected=True if there is something to reconcile.
    """
    async with httpx.AsyncClient(verify=False) as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/fhir+json",
        }

        active = await fetch_active_meds(client, OPENEMR_BASE, patient_id, headers)
        all_meds = await fetch_all_meds(client, OPENEMR_BASE, patient_id, headers)

    changes = compute_diff(active, all_meds)

    if not changes:
        return ReconciliationResult(
            patient_id=patient_id,
            changes_detected=False,
            changes=[],
            attestation_note=None,
        )

    note = build_attestation_note(patient_id, changes)

    return ReconciliationResult(
        patient_id=patient_id,
        changes_detected=True,
        changes=changes,
        attestation_note=note,
    )


@app.post("/reconcile/{patient_id}/submit")
async def submit_note(
    patient_id: str,
    note_text: str,
    token: str = Depends(get_token),
):
    """
    POSTs the confirmed attestation note back to OpenEMR as a DocumentReference.
    Only called after explicit clinician confirmation.
    """
    import base64

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
        "content": [{
            "attachment": {
                "contentType": "text/plain",
                "data": base64.b64encode(note_text.encode()).decode(),
            }
        }],
    }

    async with httpx.AsyncClient(verify=False) as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/fhir+json",
        }
        resp = await client.post(
            f"{OPENEMR_BASE}/DocumentReference",
            json=doc_ref,
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"OpenEMR rejected the note: {resp.text}",
            )

    return {"status": "written", "document_id": resp.json().get("id")}


@app.get("/health")
async def health():
    return {"status": "ok"}
