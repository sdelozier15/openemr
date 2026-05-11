"""FHIR R4 helpers for fetching MedicationRequest resources from OpenEMR."""

import httpx
from typing import Any


async def fetch_active_meds(
    client: httpx.AsyncClient,
    base: str,
    patient_id: str,
    headers: dict,
) -> list[dict[str, Any]]:
    """GET active MedicationRequests for a patient."""
    resp = await client.get(
        f"{base}/MedicationRequest",
        params={"patient": patient_id, "status": "active"},
        headers=headers,
    )
    resp.raise_for_status()
    bundle = resp.json()
    return [entry["resource"] for entry in bundle.get("entry", [])]


async def fetch_all_meds(
    client: httpx.AsyncClient,
    base: str,
    patient_id: str,
    headers: dict,
) -> list[dict[str, Any]]:
    """
    GET all MedicationRequests (active + stopped), sorted newest first.
    Used to detect recently stopped or changed medications.
    """
    resp = await client.get(
        f"{base}/MedicationRequest",
        params={
            "patient": patient_id,
            "status": "active,stopped,on-hold",
            "_sort": "-date",
            "_count": "100",
        },
        headers=headers,
    )
    resp.raise_for_status()
    bundle = resp.json()
    return [entry["resource"] for entry in bundle.get("entry", [])]
