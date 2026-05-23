"""MCP Resources — live, fetchable read-only views over FHIR data."""

from . import lab_trends, medications, patient_summary

__all__ = ["lab_trends", "medications", "patient_summary"]
