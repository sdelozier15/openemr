"""
Compute medication changes by diffing the active list against the full history.
Detects: newly added, stopped, and dose/frequency changes.
"""

from app.models import MedChange


def _med_name(resource: dict) -> str:
    """Extract a human-readable name from a MedicationRequest resource."""
    med = resource.get("medicationCodeableConcept", {})
    codings = med.get("coding", [])
    if codings:
        return codings[0].get("display", "Unknown")
    return med.get("text", "Unknown")


def _dosage_summary(resource: dict) -> str:
    """Flatten dosageInstruction into a short string for comparison."""
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
    active: list[dict],
    all_meds: list[dict],
) -> list[MedChange]:
    """
    Compare active snapshot against full history.
    Returns a list of MedChange objects for stopped, changed, and new medications.
    """
    changes: list[MedChange] = []

    # Build lookup by medication name from the full history
    history: dict[str, list[dict]] = {}
    for med in all_meds:
        name = _med_name(med)
        history.setdefault(name, []).append(med)

    active_names = {_med_name(m) for m in active}

    # Detect newly added (only one entry in history = never prescribed before)
    for med in active:
        name = _med_name(med)
        if len(history.get(name, [])) == 1:
            changes.append(MedChange(
                type="added",
                medication=name,
                detail=f"New: {_dosage_summary(med)}",
            ))

    # Detect stopped (in history but not in active)
    for name, entries in history.items():
        if name not in active_names:
            stopped = next(
                (e for e in entries if e.get("status") in ("stopped", "cancelled", "on-hold")),
                entries[0],
            )
            changes.append(MedChange(
                type="stopped",
                medication=name,
                detail=f"Status: {stopped.get('status', 'unknown')}",
            ))

    # Detect dose/frequency changes (multiple entries for same med, all active)
    for med in active:
        name = _med_name(med)
        entries = history.get(name, [])
        if len(entries) >= 2:
            current_dose = _dosage_summary(entries[0])
            previous_dose = _dosage_summary(entries[1])
            if current_dose != previous_dose and previous_dose:
                changes.append(MedChange(
                    type="changed",
                    medication=name,
                    detail=f"{previous_dose} → {current_dose}",
                ))

    return changes
