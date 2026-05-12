"""Tests for med reconciliation microservice v0.3."""

import pytest
from app.diff import compute_diff
from app.models import MedChange
from app.note import build_attestation_note


def _med(name, status="active", dose="10", unit="mg", timing="once daily"):
    return {
        "resourceType": "MedicationRequest",
        "status": status,
        "medicationCodeableConcept": {"coding": [{"display": name}]},
        "dosageInstruction": [{
            "doseAndRate": [{"doseQuantity": {"value": dose, "unit": unit}}],
            "timing": {"code": {"text": timing}},
        }],
    }


# ── Diff logic ────────────────────────────────────────────────────────────────

def test_detects_new_medication():
    changes = compute_diff([], [_med("Empagliflozin")])
    assert any(c.type == "added" and c.medication == "Empagliflozin" for c in changes)

def test_detects_stopped_medication():
    changes = compute_diff([_med("Omeprazole")], [_med("Omeprazole", status="stopped")])
    assert any(c.type == "stopped" and c.medication == "Omeprazole" for c in changes)

def test_detects_dose_change():
    baseline = [_med("Metformin", dose="500", timing="twice daily")]
    post     = [_med("Metformin", dose="1000", timing="twice daily")]
    changes  = compute_diff(baseline, post)
    assert any(c.type == "changed" and c.medication == "Metformin" for c in changes)

def test_no_changes_returns_empty():
    med = _med("Lisinopril")
    assert compute_diff([med], [med]) == []

def test_multiple_change_types():
    baseline = [_med("Omeprazole"), _med("Metformin", dose="500")]
    post     = [_med("Metformin", dose="1000"), _med("Empagliflozin"), _med("Omeprazole", status="stopped")]
    types    = {c.type for c in compute_diff(baseline, post)}
    assert types == {"added", "stopped", "changed"}

def test_unchanged_med_not_in_diff():
    baseline = [_med("Lisinopril"), _med("Omeprazole")]
    post     = [_med("Lisinopril"), _med("Omeprazole", status="stopped"), _med("Empagliflozin")]
    names    = {c.medication for c in compute_diff(baseline, post)}
    assert "Lisinopril" not in names
    assert "Omeprazole" in names
    assert "Empagliflozin" in names


# ── Note builder ──────────────────────────────────────────────────────────────

def test_note_with_changes_contains_audit_fields():
    changes = [
        MedChange(type="added",   medication="Empagliflozin", detail="New: 10 mg daily"),
        MedChange(type="stopped", medication="Omeprazole",    detail="Status: stopped"),
    ]
    note = build_attestation_note("00482917", "Dr. Smith", changes)
    assert "CHANGES THIS ENCOUNTER" in note
    assert "Empagliflozin" in note
    assert "Omeprazole" in note
    assert "ATTESTATION" in note
    assert "[Clinician signature]" in note

def test_note_no_changes_still_complete():
    note = build_attestation_note("00482917", "Dr. Smith", [])
    assert "NO MEDICATION CHANGES" in note
    assert "ATTESTATION" in note
    assert "[Clinician signature]" in note

def test_note_contains_patient_section():
    changes = [MedChange(type="added", medication="Empagliflozin", detail="10 mg daily")]
    note = build_attestation_note("00482917", "Dr. Smith", changes)
    assert "FOR YOUR RECORDS" in note
    assert "care team" in note

def test_note_no_changes_patient_section():
    note = build_attestation_note("00482917", "Dr. Smith", [])
    assert "FOR YOUR RECORDS" in note
    assert "No changes were made" in note

def test_single_note_function_only():
    """Confirm build_patient_note no longer exists — one note only."""
    import app.note as note_module
    assert not hasattr(note_module, "build_patient_note"), \
        "build_patient_note should be removed — use build_attestation_note only"
