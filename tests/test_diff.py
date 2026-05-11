"""Pydantic models for request/response shapes."""

from pydantic import BaseModel
from typing import Optional, Literal


class MedChange(BaseModel):
    type: Literal["added", "stopped", "changed"]
    medication: str
    detail: str


class ReconciliationResult(BaseModel):
    patient_id: str
    changes_detected: bool
    changes: list[MedChange]
    attestation_note: Optional[str]
