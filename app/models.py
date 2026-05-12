"""Pydantic models for request/response shapes."""

from pydantic import BaseModel
from typing import Optional, Literal


class MedChange(BaseModel):
    type: Literal["added", "stopped", "changed"]
    medication: str
    detail: str


class BaselineResponse(BaseModel):
    """Returned on GET /baseline — before-snapshot captured when med list opens."""
    patient_id: str
    encounter_id: str
    snapshot: list[dict]


class DiffResponse(BaseModel):
    """Returned on POST /diff — always returned; panel always shown on close."""
    patient_id: str
    encounter_id: str
    changes_detected: bool
    changes: list[MedChange]
    note: str                  # single combined note — clinical + patient-readable


class SubmitRequest(BaseModel):
    encounter_id: str
    note: str                  # clinician may have edited before confirming
    patient_id: str            # needed for AMC DB insert


class SubmitResponse(BaseModel):
    status: str
    soap_note_written: bool
    amc_checked: bool
