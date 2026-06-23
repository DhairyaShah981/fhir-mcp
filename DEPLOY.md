# Deploying fhir-mcp as a live demo

> Goal: a publicly reachable MCP server URL anyone can point an MCP-compatible client at, serving 5 synthetic patients (Synthea-style — **no real PHI**) over SSE / streamable HTTP.

Time to live URL: **~25 minutes** after `fly auth login`.

---

## 0. Prerequisites

```bash
# macOS:
brew install flyctl

# Linux:
curl -L https://fly.io/install.sh | sh
```

```bash
fly auth signup        # if you don't have an account
# OR
fly auth login         # if you do
```

Fly.io free tier as of 2026: 3 shared-CPU machines × 256 MB RAM, 3 GB storage, 160 GB outbound transfer/month — plenty for this demo. Credit card required on signup but not charged within free-tier limits.

---

## 1. Deploy

From this repo root (after the `live-demo-prep` branch is merged or while on it):

```bash
fly launch --copy-config --no-deploy
```

- When asked for an app name, accept the default (`dhairya-fhir-mcp`) or pick your own. If you change it, update `fly.toml` `app = "..."` to match.
- When asked for a region, accept `bom` (Mumbai) or pick the one nearest your audience (`sin`, `iad`, `lhr` are good alternates).
- It will detect the Dockerfile and write a `.fly/` cache. Do **not** let it overwrite `fly.toml` — that's why we pass `--copy-config`.

Create the persistent volume for SQLite audit log + de-id vault (1 GB is plenty):

```bash
fly volumes create fhir_mcp_state --size 1 --region bom
```

Now ship it:

```bash
fly deploy
```

First deploy takes 3-5 minutes (builds the Docker image, pushes to Fly's registry, boots the machine, runs the healthcheck). Subsequent deploys take ~60 seconds.

Once green, your live endpoint is:

```
https://dhairya-fhir-mcp.fly.dev
```

(Replace `dhairya-fhir-mcp` with whatever you chose in `fly launch`.)

---

## 2. Verify it's live

```bash
# Healthcheck — should return 200 OK.
curl -fsS https://dhairya-fhir-mcp.fly.dev/healthz

# SSE handshake — should hold the connection open and stream events.
curl -N https://dhairya-fhir-mcp.fly.dev/sse
```

For an end-to-end smoke test using any MCP-compatible client, point it at:

```
https://dhairya-fhir-mcp.fly.dev/sse
```

Then call any of the tools — `search_patients`, `get_patient_summary`, `get_medications`, `search_conditions`, `search_observations`, `validate_code`, `run_cds_hook`, `create_clinical_note`.

Synthetic patients you can search:

| Pseudonym | Profile |
|---|---|
| `pediatric_asthma_8yo` | 8-year-old asthma exacerbation |
| `diabetic_60yo` | 60-year-old T2DM with HbA1c trend |
| `chf_warfarin_70yo` | 70-year-old CHF on warfarin (drug-drug interaction scenario) |
| `pregnant_with_htn_28yo` | 28-year-old pregnant with hypertension |
| `geriatric_polypharmacy_82yo` | 82-year-old polypharmacy (deprescribing scenario) |

---

## 3. Add the URL to README

After deploy succeeds:

```bash
# Update the placeholder in README.md
sed -i.bak 's|https://dhairya-fhir-mcp\.fly\.dev|<your actual URL>|g' README.md
rm README.md.bak

git add README.md
git commit -m "live demo: pin Fly.io URL"
git push origin live-demo-prep
```

(Or just hand-edit if `sed` feels heavy.)

---

## 4. Cost discipline

- Machines auto-stop after idle (configured in `fly.toml` as `auto_stop_machines = "stop"`).
- First request after idle pays a ~1-2 second cold-start.
- Estimated cost at this config: **$0 within free tier** for a demo-level traffic pattern. Monitor with `fly status -a dhairya-fhir-mcp`.

---

## 5. Tear-down (when the demo's done)

```bash
fly apps destroy dhairya-fhir-mcp
fly volumes destroy fhir_mcp_state   # only if no other app uses it
```

---

## Troubleshooting

**Healthcheck failing on first deploy.** Check `fly logs -a dhairya-fhir-mcp` — most likely the volume mount isn't found. Create the volume (`fly volumes create ...`) before `fly deploy`.

**SSE connection drops every 30s.** That's Fly's idle-timeout for HTTP/1.1. The SSE transport sends keep-alive comments, but some intermediaries strip them. If this bites a real client, switch the deploy to use streamable HTTP transport instead (Cartesia/Anthropic's preferred path) — adjust `src/fhir_mcp/server.py` accordingly.

**Memory pressure under load.** Bump `[[vm]] memory = "1gb"` and redeploy. The Synthea backend keeps all bundles in memory — 5 bundles × ~80 KB each is trivial, but if you swap to the HAPI backend with thousands of patients, this matters.
