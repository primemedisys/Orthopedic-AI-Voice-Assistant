# Importing every model here lets Alembic see all tables.
from app.models.call_logs import CallLog
from app.models.call_summary import CallSummary
from app.models.callback import Callback
from app.models.patient import Patient

__all__ = ["Patient", "CallLog", "Callback", "CallSummary"]