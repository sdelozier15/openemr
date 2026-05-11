"""
Compute medication changes by diffing two FHIR MedicationRequest snapshots:

  baseline     — captured when clinician opens the medication list (GET 1)
  post_close   — captured when clinician closes the medication list (GET 2)

Detects:
  added   — in post_close as active, not in baseline at all
  stopped — in baseline as active, now stopped/cancelled/on-hold in post_close
  changed — same med in both, but dose or frequency differs
"""

from app.models import MedChange


def _med_name(resource: dict) -> str:
    """Extract human-readable name from a FHIR MedicationRequest resource."""
    med = resource.get("medicationCodeableConcept", {})
    codings = med.get("coding", [])
    if codings:
        return codings[0].get("display", "Unknown")
    return med.get("text", "Unknown")


def _dosage_summary(resource: dict) -> str:
    """Flatten dosageInstruction into a short comparable string."""
    instructions = resource.get("dosageInstruction", [])
    if not instructions:
        return ""
    parts = []
    for d in instructions:
        dose = d.get("doseAndRate", [{}])[0]
        qty = dose.get("doseQuantity", {})
        value = qty.get("value", "")
        unit = qty.get("unit", "")
        timing = d.get("timing", {}).get("code", {}).get("text", "")
        parts.append(f"{value} {unit} {timing}".strip())
    return "; ".join(parts)


def compute_diff(
    baseline: list[dict],
    post_close: list[dict],
) -> list[MedChange]:
    """
    Diff baseline (on-open snapshot) against post_close (on-close snapshot).

    baseline and post_close are both lists of raw FHIR MedicationRequest
    resources. The frontend captures baseline on open and passes it back
    to /diff on close — the microservice is stateless between the two events.

    Returns a list of MedChange objects. Empty list = no changes this session.
    """
    changes: list[MedChange] = []

    # Index baseline by name
    baseline_by_name: dict[str, dict] = {
        _med_name(m): m for m in baseline
    }

    # Index post_close by name
    post_close_by_name: dict[str, dict] = {
        _med_name(m): m for m in post_close
    }

    baseline_names = set(baseline_by_name.keys())
    post_close_names = set(post_close_by_name.keys())

    # Added — active in post_close, not present in baseline at all
    for name in post_close_names - baseline_names:
        med = post_close_by_name[name]
        if med.get("status") == "active":
            changes.append(MedChange(
                type="added",
                medication=name,
                detail=f"New: {_dosage_summary(med)}",
            ))

    # Stopped — was in baseline, now stopped/cancelled/on-hold in post_close
    for name in baseline_names:
        post = post_close_by_name.get(name)
        if post is None:
            # Disappeared entirely — treat as stopped
            changes.append(MedChange(
                type="stopped",
                medication=name,
                detail="Removed from medication list",
            ))
        elif post.get("status") in ("stopped", "cancelled", "on-hold"):
            changes.append(MedChange(
                type="stopped",
                medication=name,
                detail=f"Status: {post.get('status')}",
            ))

    # Changed — present in both, dose or frequency differs
    for name in baseline_names & post_close_names:
        before = _dosage_summary(baseline_by_name[name])
        after = _dosage_summary(post_close_by_name[name])
        if before and after and before != after:
            changes.append(MedChange(
                type="changed",
                medication=name,
                detail=f"{before} → {after}",
            ))

    return changes
