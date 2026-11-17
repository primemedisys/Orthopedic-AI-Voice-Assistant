"""
app/services/patient_service.py
───────────────────────────────
Patient helpers: phone normalizing, lookup by phone, and identity verification.
"""

import logging
from datetime import date
from difflib import SequenceMatcher

import phonenumbers
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.patient import Patient

logger = logging.getLogger(__name__)


def normalize_phone(raw: str, region: str = "IN") -> str:
    """Convert '098765 43210' to '+919876543210'. Returns the input unchanged if it is not valid."""
    try:
        parsed = phonenumbers.parse(raw, region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        logger.warning("⚠️ [PatientService] Could not parse phone number")
    return raw


def find_by_phone(db: Session, phone: str) -> Patient | None:
    """Return the patient with this phone number, or None for a new caller."""
    return db.scalar(select(Patient).where(Patient.phone_number == phone))


def _norm(name: str) -> str:
    """Lowercase a name and collapse extra spaces so comparisons are fair."""
    return " ".join(name.lower().split())


def verify(patient: Patient, given_name: str, given_dob: str) -> bool:
    """Check name (fuzzy, because speech-to-text misspells) AND date of birth (YYYY-MM-DD)."""
    try:
        dob = date.fromisoformat(given_dob)
    except ValueError:
        logger.warning("⚠️ [PatientService] Caller gave an invalid date of birth")
        return False
    score = SequenceMatcher(None, _norm(patient.full_name), _norm(given_name)).ratio()
    return patient.date_of_birth == dob and score >= 0.8