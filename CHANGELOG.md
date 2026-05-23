# Changelog

All notable changes to **fhir-mcp** are documented here.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — M2 (v0.1 feature-complete, 2026-05-23)
- **All 8 MCP tools shipped:** `search_observations` (with trend-window slope/mean/last stats), `search_conditions`, `get_medications` (with auto CDS-Hooks drug-drug check), `validate_code` (offline LOINC/SNOMED/RxNorm/ICD-10 + optional tx.fhir.org), `create_clinical_note` (SOAP/discharge/prior-auth), `run_cds_hook`.
- **3 MCP Resources:** `fhir://patient/{pseudonym}/{summary,trends,medications}`.
- **3 MCP Prompts:** `soap_note`, `discharge_summary`, `prior_auth_letter` — versioned templates that orchestrate tool calls.
- **CDS Hooks bridge:** offline deterministic mock service (drug-drug, drug-allergy, glycemic, statin) + live-with-fallback client. Mock-first keeps eval scoreboards reproducible.
- **Terminology client:** offline lookup table covers every code used in evals; tx.fhir.org `$lookup` available as opt-in fallback.
- **2 more golden Synthea bundles:** `chf_warfarin_70yo` (exercises drug-drug interactions), `pregnant_with_htn_28yo` (exercises ACE-I/pregnancy considerations).
- **Full eval scoreboard:** clinical accuracy 7/7, de-id leakage 1/1 across 3 bundles, code validation 31/31, CDS Hooks correctness 4/4.
- **MCP stdio end-to-end integration test** (guards the contract that logs go to stderr and never contaminate the JSON-RPC channel).

### Fixed
- Server bootstrap previously emitted structlog output to stdout, which broke MCP stdio clients. All logging is now routed to stderr unconditionally.

### Engineering
- 169 tests passing, **96% line coverage**, ruff + pyright clean.

### Added — M0 + M1 (2026-05-23)
- M0 bootstrap: `pyproject.toml` (uv-managed), Apache-2.0 license, CI workflow stub, founder-facing README.
- M1 MVP: MCP server skeleton, FHIR client wrapper, Synthea + HAPI backends, structural FHIR de-identification pipeline with reversible token vault (HMAC-SHA256 + per-vault salt), SQLite audit logger joinable to Langfuse trace IDs, `search_patients` and `get_patient_summary` tools, first clinical-accuracy + PHI-leakage evals.
