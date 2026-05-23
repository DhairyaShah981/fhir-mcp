# Contributing to `fhir-mcp`

Thanks for opening this. The project is small enough that contributor friction is the binding constraint, so the rules are short.

## Ground rules

1. **The eval scoreboard must stay green.** Any PR that lowers a row's score is blocked by CI.
2. **The PHI leakage gate must stay at zero.** `evals/test_deid.py` walks every golden bundle against the scanner; if you add a tool, you also extend the scanner if needed.
3. **No cloud-required code paths.** Local zero-config mode (`uvx fhir-mcp serve`, no `.env`, no signups) must always work. Cloud features sit behind opt-in env vars.
4. **No writes to real EHRs in v0.x.** Read-only against HAPI. Writes only against in-memory Synthea or a local `DocumentReference` from `create_clinical_note`.

## Local setup

```bash
git clone https://github.com/DhairyaShah981/fhir-mcp
cd fhir-mcp
uv sync --all-extras --dev
uv run pytest tests evals -q
```

Expected: `206 passed, 1 skipped` (the skipped one is the judge-LLM eval, which only runs when `ANTHROPIC_API_KEY` is set).

## Adding a new MCP tool

1. Create `src/fhir_mcp/tools/<name>.py` with Pydantic input/output models.
2. Wrap the handler with `@traced("<name>")` then `@audited("<name>", phi_args=(...))`. Declare any free-text PHI carrier fields in `phi_args`.
3. Register it in `src/fhir_mcp/server.py`.
4. Ensure every FHIR resource that leaves the handler passes through `deid.pipeline.deidentify_resource(...)`.
5. Add at least one eval to `evals/test_clinical_accuracy.py` (or the relevant suite).
6. Update the README tool catalog table.
7. If your tool calls a third party (CDS Hooks, terminology, SMART), de-identify resources *before* the wire egress — see `cds_hooks/client.py::_deid_prefetch` for the pattern.

## Adding a new golden bundle

1. Hand-craft a Synthea-shaped JSON at `evals/golden/<name>.json`. Keep it small and medically coherent.
2. Don't regenerate at test time. Reproducibility is non-negotiable.
3. Add a structural-accuracy test in `evals/test_clinical_accuracy.py` asserting the expected primary diagnosis surfaces correctly.

## Style

- Python 3.11+ · `uv` for everything · Pydantic v2 for tool I/O.
- `uv run ruff check src evals tests` and `uv run pyright src` must both pass.
- No comments that explain WHAT the code does — only WHY (constraint, invariant, workaround). See `CLAUDE.md` for the full convention set.

## Reporting bugs

- Reproducible test case is the fastest path to a fix.
- For security-sensitive issues, see [SECURITY.md](SECURITY.md) — please do NOT file a public issue.

## What's most valuable right now

Tagged `help wanted` in the issue tracker:

- AES-GCM vault encryption at rest ([#1](https://github.com/DhairyaShah981/fhir-mcp/issues/1))
- MCP `2026-07-28` stateless transport migration ([#2](https://github.com/DhairyaShah981/fhir-mcp/issues/2))
- Nightly live-FHIR-sandbox integration test ([#3](https://github.com/DhairyaShah981/fhir-mcp/issues/3))

If none of those grab you, run the eval suite with your favourite judge model and PR a `cross_model_report.json` — that's how the harness gets useful.
