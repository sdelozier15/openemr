"""Pydantic models for request/response shapes."""

from pydantic import BaseModel
from typing import Optional, Literal


class MedChange(BaseModel):
    type: Literal["added", "stopped", "changed"]
    medication: str
    detail: str


class BaselineResponse(BaseModel):
    """Returned on GET /baseline — the before-snapshot captured when med list opens."""
    patient_id: str
    encounter_id: str
    snapshot: list[dict]          # raw FHIR MedicationRequest resources, held by frontend


class DiffResponse(BaseModel):
    """Returned on GET /diff — always returned; panel always shown on close."""
    patient_id: str
    encounter_id: str
    changes_detected: bool        # True = changes found; False = no changes, list confirmed
    changes: list[MedChange]
    patient_note: str             # patient-facing: "Medication changes made today"
    attestation_note: str         # clinician-facing: full reconciliation note for signing


class SubmitRequest(BaseModel):
    encounter_id: str
    attestation_note: str         # clinician may have edited this before confirming
    patient_note: str             # patient-facing note text


class SubmitResponse(BaseModel):
    status: str
    document_id: Optional[str]
    encounter_note_written: bool
    amc_checked: bool
