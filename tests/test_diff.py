"""
Tests for the medication reconciliation microservice.
Covers: diff logic, note building, and the two-trigger flow.
"""

import pytest
from app.diff import compute_diff
from app.models import MedChange
from app.note import build_attestation_note, build_patient_note


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _med(name: str, status: str = "active", dose_value: str = "10", unit: str = "mg", timing: str = "once daily") -> dict:
    return {
        "resourceType": "MedicationRequest",
        "status": status,
        "medicationCodeableConcept": {
            "coding": [{"display": name}]
        },
        "dosageInstruction": [{
            "doseAndRate": [{"doseQuantity": {"value": dose_value, "unit": unit}}],
            "timing": {"code": {"text": timing}},
        }],
    }


# ─── Diff logic ───────────────────────────────────────────────────────────────

def test_detects_new_medication():
    """A med appearing only once in post-close history = newly added this session."""
    baseline = []
    post_close = [_med("Empagliflozin")]
    changes = compute_diff(baseline, post_close)
    assert any(c.type == "added" and c.medication == "Empagliflozin" for c in changes)


def test_detects_stopped_medication():
    """A med in baseline but stopped in post-close = stopped this session."""
    baseline = [_med("Omeprazole")]
    post_close = [_med("Omeprazole", status="stopped")]
    changes = compute_diff(baseline, post_close)
    assert any(c.type == "stopped" and c.medication == "Omeprazole" for c in changes)


def test_detects_dose_change():
    """Same med, different dose between baseline and post-close = changed."""
    baseline = [_med("Metformin", dose_value="500", timing="twice daily")]
    post_close = [_med("Metformin", dose_value="1000", timing="twice daily")]
    changes = compute_diff(baseline, post_close)
    assert any(c.type == "changed" and c.medication == "Metformin" for c in changes)


def test_no_changes_returns_empty():
    """Identical baseline and post-close = no changes detected."""
    med = _med("Lisinopril")
    changes = compute_diff([med], [med])
    assert changes == []


def test_multiple_change_types_detected():
    """All three change types detected in a single diff."""
    baseline = [
        _med("Omeprazole"),
        _med("Metformin", dose_value="500"),
    ]
    post_close = [
        _med("Metformin", dose_value="1000"),   # changed
        _med("Empagliflozin"),                   # added
        _med("Omeprazole", status="stopped"),    # stopped
    ]
    changes = compute_diff(baseline, post_close)
    types = {c.type for c in changes}
    assert "added" in types
    assert "stopped" in types
    assert "changed" in types


# ─── Note building ────────────────────────────────────────────────────────────

def test_attestation_note_with_changes():
    changes = [
        MedChange(type="added",   medication="Empagliflozin", detail="New: 10 mg daily"),
        MedChange(type="stopped", medication="Omeprazole",    detail="Status: stopped"),
    ]
    note = build_attestation_note("00482917", "Dr. Smith", changes)
    assert "CHANGES THIS ENCOUNTER" in note
    assert "Empagliflozin" in note
    assert "Omeprazole" in note
    assert "Dr. Smith" in note
    assert "[Clinician signature]" in note


def test_attestation_note_no_changes():
    """No-change note always generated — clinician still confirms (Option B)."""
    note = build_attestation_note("00482917", "Dr. Smith", [])
    assert "NO MEDICATION CHANGES" in note
    assert "confirmed current and accurate" in note
    assert "[Clinician signature]" in note


def test_patient_note_with_changes():
    changes = [
        MedChange(type="added",   medication="Empagliflozin", detail="New: 10 mg daily"),
        MedChange(type="stopped", medication="Omeprazole",    detail="Status: stopped"),
        MedChange(type="changed", medication="Metformin",     detail="500 mg BID → 1000 mg BID"),
    ]
    note = build_patient_note(changes)
    assert "New medications started" in note
    assert "Medications stopped" in note
    assert "dose or frequency changes" in note
    assert "Empagliflozin" in note
    assert "Omeprazole" in note
    assert "Metformin" in note
    assert "care team" in note


def test_patient_note_no_changes():
    """Patient note always generated — plain language 'no changes' message."""
    note = build_patient_note([])
    assert "No changes" in note
    assert "confirmed accurate" in note
    assert "care team" in note


def test_patient_note_title_includes_date():
    note = build_patient_note([])
    assert "MEDICATION CHANGES MADE TODAY" in note


# ─── Two-trigger flow contract ────────────────────────────────────────────────

def test_diff_uses_baseline_not_history():
    """
    Core contract: diff compares baseline (from open) vs post-close state.
    A med present in baseline and still active = no change.
    A med present in baseline but now stopped = stopped.
    A med not in baseline but now active = added.
    """
    baseline = [_med("Lisinopril"), _med("Omeprazole")]
    post_close = [
        _med("Lisinopril"),                     # unchanged
        _med("Omeprazole", status="stopped"),   # stopped this session
        _med("Empagliflozin"),                  # added this session
    ]
    changes = compute_diff(baseline, post_close)
    names = {c.medication for c in changes}
    assert "Lisinopril" not in names        # unchanged = not in diff
    assert "Omeprazole" in names            # stopped = in diff
    assert "Empagliflozin" in names         # added = in diff
