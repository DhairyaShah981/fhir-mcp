# Security policy

`fhir-mcp` ships software designed to handle Protected Health Information (PHI) by way of FHIR resources. **Take any suspected vulnerability seriously.** This policy describes how to report one and what to expect.

## Reporting a vulnerability

**Do not file public GitHub issues for security reports.** Instead, use one of:

- **GitHub private vulnerability report** (preferred): https://github.com/DhairyaShah981/fhir-mcp/security/advisories/new
- Encrypted email: open the [GitHub profile](https://github.com/DhairyaShah981) — the security email is listed in the public profile contact.

Please include:

1. A clear description of the issue and its impact.
2. Reproduction steps or a proof-of-concept (a failing test is gold).
3. The affected version (output of `fhir-mcp version` plus the commit SHA).
4. Any suggested fix, if you have one.

## Response targets

| Stage | Target |
|---|---|
| Acknowledge receipt | within 48 hours |
| Triage + severity assessment | within 7 days |
| Fix landed on `main` | within 30 days for High/Critical; 90 days for Medium/Low |
| Public disclosure (CVE if applicable) | coordinated; default 90 days after fix lands |

## In-scope

- The `fhir-mcp` Python package (`src/fhir_mcp/`).
- The MCP server transport (stdio + SSE) it exposes.
- The de-identification pipeline (`deid/`), audit logger (`audit.py`), vault (`deid/vault.py`), SMART OAuth2 client (`auth/`).
- CI workflows under `.github/workflows/`.

## Out of scope

- Third-party FHIR servers (HAPI, Epic on FHIR sandbox, etc.) we call out to.
- The MCP client (Claude Desktop, Cursor, etc.).
- Misconfigurations of operator-controlled cloud services (Supabase, Langfuse) when the configuration ignores documented hardening guidance.

## Known limitations (not vulnerabilities)

These are documented trade-offs, not flaws to report:

- **The PHI vault stores values plaintext at rest in v0.2.x.** AES-GCM encryption is tracked in [#1](https://github.com/DhairyaShah981/fhir-mcp/issues/1) and is on the v0.3 roadmap. Operators are advised to treat the vault file (or Postgres table) as PHI for backup, ACL, and retention purposes. See `docs/THREAT_MODEL.md` TB-C for full STRIDE analysis.
- **Re-identification keys (`FHIR_MCP_REID_KEY`) have no revocation channel** in v0.2.x — rotation is manual. SMART tokens carrying the `fhir-mcp/reid` scope expire naturally.
- **Audit-row `args` are populated only when `FHIR_MCP_VERBOSE_AUDIT=true`.** Tools may declare `phi_args` via the `@audited` decorator to strip PHI fields even in verbose mode (e.g. `create_clinical_note.free_text`).

## Disclosure credit

We credit reporters in the release notes for the fix unless you ask us not to. We do not currently run a paid bug bounty.
