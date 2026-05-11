"""
AMC checkbox helper.

Patches the "Medication Reconciliation Performed?" checkbox on an OpenEMR
encounter via the native REST API (not FHIR — this field lives in
form_encounter, not as a FHIR resource).

HOW TO CONFIRM THE EXACT ENDPOINT FOR YOUR INSTANCE:
  1. Open an encounter in OpenEMR (demo.openemr.io works)
  2. Open DevTools → Network tab → clear it
  3. Manually check "Medication Reconciliation Performed?"
  4. Read the request that fires — that's the exact URL and payload
  5. Update NATIVE_BASE and the payload dict below to match

The placeholder endpoint pattern below follows the OpenEMR native REST
convention. The field name inside the payload must be verified against
your instance before deploying — it is NOT standardised across versions.
"""

import os
import httpx

NATIVE_BASE = os.getenv(
    "OPENEMR_NATIVE_BASE",
    "https://localhost:9300/apis/default/api",
)


async def patch_amc_checkbox(
    client: httpx.AsyncClient,
    encounter_id: str,
    token: str,
) -> bool:
    """
    Check "Medication Reconciliation Performed?" on the encounter record.

    Always called on submit (Option B) — the clinician confirmed they
    reviewed the list, which is the reconciliation act regardless of
    whether any changes were found.

    Returns True if successful, False if the call failed (non-fatal —
    the notes are already written by the time this runs).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Field name TBD — verify via DevTools. Common candidates:
    #   med_reconciliation, reconciliation_performed, or a numeric form field ID.
    payload = {"med_reconciliation": 1}

    resp = await client.patch(
        f"{NATIVE_BASE}/encounter/{encounter_id}",
        json=payload,
        headers=headers,
    )

    if resp.status_code not in (200, 201):
        print(
            f"[warn] AMC checkbox PATCH returned {resp.status_code}: {resp.text}\n"
            "Verify the endpoint and field name via DevTools — see amc.py comments."
        )
        return False

    return True
