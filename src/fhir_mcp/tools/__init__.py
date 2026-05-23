"""MCP tools — one module per tool. Registered in ``server.py``."""

from . import get_patient_summary, search_patients

__all__ = ["get_patient_summary", "search_patients"]
