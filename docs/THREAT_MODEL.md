# Threat model — `fhir-mcp` v0.2

> STRIDE analysis of every trust boundary in the server. This document is the
> authoritative answer to a clinical-safety officer's first question: *"What
> happens when each layer is compromised?"*

Updated: 2026-05-23 · Maintained alongside code. PRs that change the trust
surface must update this file.

---

## Components in scope

| ID  | Component             | File / surface                                |
|-----|-----------------------|------------------------------------------------|
| C1  | MCP server (stdio)    | `src/fhir_mcp/server.py::run_stdio`            |
| C2  | MCP server (SSE/HTTP) | `src/fhir_mcp/server.py::run_sse`              |
| C3  | Tool dispatch         | every file under `src/fhir_mcp/tools/`         |
| C4  | De-id pipeline        | `src/fhir_mcp/deid/pipeline.py`                |
| C5  | PHI vault             | `src/fhir_mcp/deid/vault.py`                   |
| C6  | Re-identification     | `src/fhir_mcp/deid/reid.py`                    |
| C7  | Audit logger          | `src/fhir_mcp/audit.py`                        |
| C8  | SMART OAuth2 client   | `src/fhir_mcp/auth/smart.py`                   |
| C9  | CDS Hooks client      | `src/fhir_mcp/cds_hooks/client.py`             |
| C10 | Terminology client    | `src/fhir_mcp/terminology.py`                  |
| C11 | FHIR backend (HAPI)   | `src/fhir_mcp/backends/hapi.py`                |
| C12 | Synthea backend       | `src/fhir_mcp/backends/synthea.py`             |

## Trust boundaries

| Boundary | Description |
|---|---|
| TB-A | MCP client ↔ MCP server (stdio or SSE). Client may be adversarial. |
| TB-B | MCP server ↔ FHIR backend. Backend is partially trusted (HAPI = public test). |
| TB-C | MCP server ↔ Vault / Audit DB (local SQLite or Supabase Postgres). |
| TB-D | MCP server ↔ External services (tx.fhir.org, CDS Hooks sandbox, SMART issuer). |
| TB-E | MCP server ↔ Langfuse (observability). |

---

## STRIDE per boundary

Severity legend: **C**ritical · **H**igh · **M**edium · **L**ow

### TB-A — MCP client ↔ server

| Threat | Vector | Sev | Mitigation |
|---|---|:-:|---|
| **S**poofing | Client claims to be a different user / agent in tool arguments | M | Audit row records the JSON-RPC `clientInfo` if provided. SMART scopes (TB-D) bind actions to a verifiable subject. |
| **T**ampering | Client mutates pseudonyms returned by one tool before calling another | L | Pseudonyms are HMAC-SHA256 over a per-vault salt — guessable manipulation is detected (vault lookup fails); but a legitimately-issued pseudonym can be reused. By design — the LLM chains tool calls. |
| **R**epudiation | Client denies having called a tool / re-id | L | Every tool call writes an `audit_events` row carrying the Langfuse trace id, the args hash, and the resource refs touched. |
| **I**nfo disclosure | PHI leaks through tool outputs | C | De-id pipeline (C4) runs on every egress; PHI vault stays server-side; `evals/test_deid.py` runs every push and fails CI on any known-token leak. |
| **D**oS | Pathological tool arguments cause OOM / runaway query | M | Pydantic Field constraints bound `limit`, `trend_window_days`, `max_observations`. Synthea is in-memory. HAPI calls have a 15-s httpx timeout. |
| **E**op | Client invokes the privileged `reid` path without auth | C | `reid` is *not* exposed as an MCP tool. CLI / direct API only. Two-factor: `FHIR_MCP_ENABLE_REID=true` AND (matching key OR SMART token with `fhir-mcp/reid` scope). |

### TB-B — Server ↔ FHIR backend

| Threat | Vector | Sev | Mitigation |
|---|---|:-:|---|
| **S** | Adversarial HAPI returns crafted bundles to confuse the LLM | M | All resources flow through de-id pipeline before leaving the server. Document content fields are scanned for PHI tokens. |
| **T** | Backend returns mutated codes (e.g., wrong SNOMED display) | M | `validate_code` uses an embedded offline table for any code referenced in evals. `allow_live=true` is opt-in. |
| **I** | Live backend returns real PHI by accident | C | De-id pipeline is mandatory; never disabled per call. |
| **D** | Slow backend stalls tool calls | M | httpx timeout (15 s). |
| **E** | Backend writes attempted by tool | M | v0.1 = read-only on HAPI; writes only land in the in-memory Synthea backend or as a local `DocumentReference` from `create_clinical_note`. |

### TB-C — Server ↔ Vault / Audit DB

| Threat | Vector | Sev | Mitigation |
|---|---|:-:|---|
| **S** | Process with file-system access reads the SQLite vault | H | Vault is **plaintext at rest in local mode** — assumed-OS-trusted. Cloud mode (Supabase Postgres) places it behind PG ACLs; AES-GCM at rest planned for v0.3. **Operators must treat the local SQLite as PHI.** |
| **T** | Vault rows mutated externally | H | UNIQUE constraint on `pseudonym`. Schema is small and easy to audit; row-level signatures planned for v0.3. |
| **R** | Audit DB lost / deleted | M | Operators must back up the audit DB. Supabase mode inherits the project's PITR. |
| **I** | Vault dump leaks all original PHI | C | Same as **S** — relies on OS / Postgres ACLs. Plaintext storage in local mode is documented in README. |
| **D** | Vault grows unbounded | L | Pseudonymization is deterministic; identical inputs reuse the same row. Practical vault size ≈ unique PHI tokens, not call volume. |
| **E** | Audit hook bypassed in code | H | `@audited` decorator wraps every registered tool; PRs that add a tool without it will be caught by `tests/test_server_handlers.py` (which exercises every registered tool through the FastMCP layer). |

### TB-D — Server ↔ external services

| Threat | Vector | Sev | Mitigation |
|---|---|:-:|---|
| **S** | DNS / TLS-MITM hijacks `tx.fhir.org`, `cds.logicahealth.org`, or the SMART issuer | M | httpx defaults verify TLS. Public-CA pinning is operator-side. Offline-first design means many calls never reach the network. |
| **T** | Compromised CDS Hooks endpoint returns malicious cards | M | Cards are surfaced to the LLM as text only — no auto-action. Default = offline mock. |
| **I** | SMART tokens accidentally logged | H | `audit_events.args` is never populated unless `verbose_audit=true` (dev only); the token is only ever held in process memory. |
| **D** | External outage breaks evals | L | Offline-first: terminology and CDS Hooks default to embedded mock; only `allow_live=true` callers hit the network. |
| **E** | Stolen SMART access token replayed | H | Token expiry (5 s clock skew) checked on every `reidentify()` call; future v0.3: introspection endpoint + refresh-token rotation. |

### TB-E — Server ↔ Langfuse

| Threat | Vector | Sev | Mitigation |
|---|---|:-:|---|
| **I** | Langfuse traces accidentally contain PHI | C | Spans receive pseudonyms only; the vault's plaintext side never enters the observability pipeline. Verified by `tests/test_resources_and_prompts.py` (every rendered resource passes through de-id). |
| **R** | Langfuse traces deleted / tampered | M | Audit rows carry the trace id but the source of truth is the audit DB, not Langfuse. |
| **D** | Langfuse outage breaks tool flow | L | Wrapped in try/except; failure logs but never raises into the tool path. |

---

## Top residual risks (v0.2)

1. **Local-mode vault is plaintext at rest.** Mitigation: only run local mode with synthetic data. Production deployments must use Supabase + the v0.3 AES-GCM layer.
2. **No revocation channel for re-id keys.** A leaked `FHIR_MCP_REID_KEY` requires manual rotation. SMART tokens have natural expiry but no introspection-driven revocation yet (v0.3).
3. **Backend response trust.** A compromised FHIR server could feed crafted bundles. The de-id pipeline catches PHI shapes but cannot guarantee clinical correctness.
4. **Judge LLM drift.** Eval scoreboard depends on a pinned judge model. Cross-model harness (`evals/cross_model.py`) is the regression check.

## Out of scope for v0.2

- Hardware-rooted key custody (HSM / KMS integration).
- BAA-level compliance attestation (HIPAA, HITRUST). Architecture is HIPAA-aware; certifications belong to deployments.
- Penetration testing of the SSE transport in production deployments. Run `/cso` against your deployment before going live.
