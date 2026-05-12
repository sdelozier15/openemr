"""
Build a single reconciliation note that serves two purposes:

1. Clinical record — structured attestation satisfying MIPS / TJC audit
   requirements: patient identity, encounter, changes with clinical detail,
   clinician attestation, and signature line.

2. Patient-readable summary — plain-language "For Your Records" section
   appended at the bottom, suitable for the patient to keep.

One note. One POST to soap_note. No separate DocumentReference needed.
"""

from datetime import date
from app.models import MedChange

TYPE_LABELS = {
    "added":   "ADDED",
    "stopped": "STOPPED",
    "changed": "CHANGED",
}


def build_attestation_note(
    patient_id: str,
    clinician_name: str,
    changes: list[MedChange],
) -> str:
    """
    Single note combining clinical attestation (audit-ready) and patient
    plain-language summary (for the patient portal / visit summary).

    Posted via POST /api/patient/{pid}/encounter/{eid}/soap_note
    with authorized=1 so it lands in the Visit Summary and is signed.
    """
    today = date.today().strftime("%B %d, %Y")

    lines = [
        f"MEDICATION RECONCILIATION — {today}",
        f"Patient ID: {patient_id}",
        f"Clinician: {clinician_name}",
        "",
    ]

    # ── Clinical section (audit-facing) ──────────────────────────────────────
    if changes:
        lines.append("CHANGES THIS ENCOUNTER:")
        for i, c in enumerate(changes, 1):
            label = TYPE_LABELS.get(c.type, c.type.upper())
            lines.append(f"  {i}. {label} — {c.medication} · {c.detail}")
    else:
        lines += [
            "NO MEDICATION CHANGES THIS ENCOUNTER:",
            "  Medication list reviewed. All current medications confirmed accurate.",
        ]

    lines += [
        "",
        "ATTESTATION:",
        (
            "Medication list reviewed with patient. All changes discussed and understood. "
            "No unresolved discrepancies. Patient verbalized understanding."
        ) if changes else (
            "Medication list reviewed with patient. No changes identified. "
            "List confirmed current and accurate."
        ),
        "",
        "[Clinician signature] _______________",
        "",
    ]

    # ── Patient-readable section ──────────────────────────────────────────────
    lines.append("FOR YOUR RECORDS:")

    if changes:
        added   = [c for c in changes if c.type == "added"]
        stopped = [c for c in changes if c.type == "stopped"]
        changed = [c for c in changes if c.type == "changed"]

        if stopped:
            lines.append("  Medications stopped:")
            for c in stopped:
                lines.append(f"    • {c.medication}")
        if changed:
            lines.append("  Medications adjusted:")
            for c in changed:
                lines.append(f"    • {c.medication} — {c.detail}")
        if added:
            lines.append("  New medications started:")
            for c in added:
                lines.append(f"    • {c.medication} — {c.detail}")

        lines.append("  Questions? Contact your care team.")
    else:
        lines += [
            "  No changes were made to your medications at today's visit.",
            "  Your medication list was reviewed and confirmed accurate.",
            "  Questions? Contact your care team.",
        ]

    return "\n".join(lines)
