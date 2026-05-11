"""
Build two notes from detected changes:

1. attestation_note  — clinician-facing, full reconciliation note for signing.
                       Goes to Visit Summary via native encounter note API.

2. patient_note      — patient-facing, plain language summary.
                       Goes to "Link/Add Issues to This Visit" as a document
                       titled "Medication changes made today".
                       Always generated — even when no changes found — so the
                       clinician always confirms before the AMC box is checked.
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
    Full reconciliation note for the clinician to review, edit, and sign.
    Lands in the Visit Summary (native encounter note endpoint).
    """
    today = date.today().strftime("%B %d, %Y")

    lines = [
        "MEDICATION RECONCILIATION NOTE",
        f"Date: {today}",
        f"Patient ID: {patient_id}",
        f"Clinician: {clinician_name}",
        "",
    ]

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
            "Medication list reviewed with patient. All changes discussed. "
            "Patient verbalized understanding. No unresolved discrepancies."
        ) if changes else (
            "Medication list reviewed with patient. No changes identified. "
            "List confirmed current and accurate."
        ),
        "",
        "[Clinician signature] _______________",
    ]

    return "\n".join(lines)


def build_patient_note(changes: list[MedChange]) -> str:
    """
    Plain-language patient-facing summary.
    Posted as a DocumentReference titled "Medication changes made today"
    linked to the visit — visible in the patient portal and Visit Summary
    under Link/Add Issues to This Visit.

    Always generated regardless of whether changes were found, so the
    clinician always has something to confirm before the AMC box is checked.
    """
    today = date.today().strftime("%B %d, %Y")

    lines = [
        f"MEDICATION CHANGES MADE TODAY — {today}",
        "",
    ]

    if changes:
        added   = [c for c in changes if c.type == "added"]
        stopped = [c for c in changes if c.type == "stopped"]
        changed = [c for c in changes if c.type == "changed"]

        if added:
            lines.append("New medications started:")
            for c in added:
                lines.append(f"  • {c.medication} — {c.detail}")
            lines.append("")

        if stopped:
            lines.append("Medications stopped:")
            for c in stopped:
                lines.append(f"  • {c.medication}")
            lines.append("")

        if changed:
            lines.append("Medications with dose or frequency changes:")
            for c in changed:
                lines.append(f"  • {c.medication} — {c.detail}")
            lines.append("")

        lines += [
            "If you have questions about any of these changes, please contact your care team.",
        ]
    else:
        lines += [
            "No changes were made to your medications at today's visit.",
            "",
            "Your current medication list was reviewed and confirmed accurate.",
            "If you have any questions, please contact your care team.",
        ]

    return "\n".join(lines)

