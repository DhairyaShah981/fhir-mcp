# Changelog

All notable changes to **fhir-mcp** are documented here.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] — 2026-05-23

Trust-hardening patch driven by a multi-agent review of the v0.2.0 surface.

### Security
- **CDS Hooks live calls no longer leak source PHI.** `cds_hooks/client.py` now de-identifies every resource in `prefetch` before posting to the third-party endpoint. Adds `tests/test_cds_hooks_client.py::test_live_prefetch_is_deidentified_before_posting` as the regression gate. Previously `allow_live=True` would have sent raw `Patient.name`, MRN, etc. to `cds.logicahealth.org`.
- **`@audited(phi_args=...)` strips declared PHI fields** even when `FHIR_MCP_VERBOSE_AUDIT=true`. `create_clinical_note.free_text` is now annotated. Removes a documented operator footgun. New tests in `tests/test_audit_phi_safety.py`.
- **Pseudonym width bumped from 32 → 64 bits** with detect-and-extend on collision; vault gained `asyncio.Lock` + `init_lock` to eliminate cache-state races on first-write.

### Fixed
- HAPI search: `date_ge` + `date_le` now go on the wire as two `date=` params instead of being string-concatenated (was broken for closed-range queries).
- HAPI `$everything`: now sends `_count` and caps results at 200 to protect LLM context windows; emits a `hapi_everything_truncated` warning when the cap is hit.
- `CdsHooksClient.invoke` returns a `CdsHooksResponse` dataclass — callers now know definitively whether the mock fired (incl. on live-failure fallback) via `used_mock` + `fallback_reason`.
- PHI leak scanner (`scan_for_phi_leaks` + `collect_known_phi_from_bundle`) now walks every resource type, including notes / DocumentReference content / Observation valueString — not just Patient/Practitioner/RelatedPerson. The leak gate is now much wider.
- README + CHANGELOG + threat model reconciled — removed claims about Presidio and AES-GCM being shipped (they're optional / roadmap), removed `fhirclient` from the architecture diagram (we don't use it), softened the competitor table with linked citations, added [langcare-mcp-fhir](https://github.com/langcare/langcare-mcp-fhir) as the newest active competitor.

### Engineering
- 206 tests passing (+8 net), **96% line coverage**, ruff + pyright clean.

## [0.2.0] — 2026-05-23

### Added — M3 (production hardening)
- **SMART on FHIR OAuth2 v2** (`src/fhir_mcp/auth/`):
  - `.well-known/smart-configuration` discovery
  - **PKCE authorization-code flow** for interactive launches (sandbox / Epic on FHIR / Cerner)
  - **Backend-services asymmetric JWT bearer flow** (RFC 7523 / SMART v2) with `RS384` default
  - `SmartToken` shape carrying scopes, expiry, patient context
  - SMART scope parser supporting both v1 (`patient/*.read`) and v2 (`patient/Observation.crudsa`) forms
- **Custom scope `fhir-mcp/reid`** gating re-identification. `reidentify()` now accepts either a static key *or* a SMART token; the token's `Patient/...` context (or `client_id`) is recorded as the audit actor.
- **Supabase / Postgres cloud mode**: setting `SUPABASE_DB_URL` routes audit + vault to Postgres via asyncpg; `FHIR_MCP_VAULT_URL` allows splitting vault and audit across stores. Schemes are auto-normalized to `postgresql+asyncpg://`.
- **CLI surface**: `fhir-mcp smart-discover`, `smart-authorize-url`, `smart-jwt`.
- **Cross-model judge harness** (`evals/cross_model.py`) — runs the same rubric through Claude / GPT / Gemini judges and reports score deltas. Skipped silently without API keys; emits JSON report for CI artifact.
- **STRIDE threat model** at `docs/THREAT_MODEL.md` — per-component STRIDE table across all 12 components and 5 trust boundaries.
- **2 more golden Synthea bundles**: `pediatric_asthma_8yo` (acute care + spacer + peanut allergy), `geriatric_polypharmacy_82yo` (8 active meds — exercises apixaban+aspirin DDI and Beers Criteria territory).
- **PyPI release workflow** (`.github/workflows/publish.yml`) using OIDC trusted publishing — no API tokens stored.

### Changed
- Bumped to **0.2.0** (semver: minor — new public CLI subcommands, new optional `cloud` extra needs `asyncpg`, `reidentify()` signature extended).
- `pyjwt[crypto]` is now a direct dependency (needed for SMART backend-services flow).

### Engineering
- **198 tests passing, 97% coverage** (+29 tests, +1pp coverage vs M2). Ruff + pyright clean.

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
