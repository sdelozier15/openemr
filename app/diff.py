"""
FHIR R4 helpers for OpenEMR.

Two separate GETs, called at different moments:
  fetch_baseline()  — called when clinician opens the medication list.
                      Captures the before-snapshot. Fast, active-only.
  fetch_post_close() — called when clinician closes the medication list (dlgopen onClosed).
                      Captures full history including anything changed during the session.
"""

import httpx
from typing import Any


async def fetch_baseline(
    client: httpx.AsyncClient,
    base: str,
    patient_id: str,
    headers: dict,
) -> list[dict[str, Any]]:
    """
    GET 1 — fires when clinician clicks 'Edit medications' (med list opens).
    Active medications only. This is the before-snapshot.
    Result is returned to the frontend and held there between the two events.
    The microservice does not store it — no server state.
    """
    resp = await client.get(
        f"{base}/MedicationRequest",
        params={"patient": patient_id, "status": "active"},
        headers=headers,
    )
    resp.raise_for_status()
    bundle = resp.json()
    return [entry["resource"] for entry in bundle.get("entry", [])]


async def fetch_post_close(
    client: httpx.AsyncClient,
    base: str,
    patient_id: str,
    headers: dict,
) -> list[dict[str, Any]]:
    """
    GET 2 — fires when clinician closes the medication list (dlgopen onClosed callback).
    Full history: active + stopped + on-hold, newest first.
    Diffed against the baseline snapshot the frontend passes back.
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
