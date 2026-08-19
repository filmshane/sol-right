# SOL-RIGHT Solar — Build 2 (Implemented Architecture)

Build 2 replaces the original n8n + Airtable design with a fully local stack.

## What this build is (authoritative)

| Layer | Implementation |
|-------|----------------|
| Frontend | Local marketing website + AI chat widget |
| Web server | **nginx** on `http://192.168.1.210/` |
| Chat UI | Embedded widget (AI-Agent Dave) — not n8n Chat Trigger |
| Backend API | FastAPI `sol-right-api` on `127.0.0.1:8791` (proxied via nginx `/api/`) |
| Agent 1 | **Dave** — conversation, intake, tool calling |
| Agent 2 | **Solar Analyst** — interprets Google Solar + intake into quote narrative |
| LLM | Hermes xAI OAuth proxy `:8645` — `grok-4.20-0309-reasoning` for **both** agents |
| Tools | `solar_estimate` (Geocode + Google Solar), `create_lead` |
| CRM | **SQLite** `/opt/sol-right/data/leads.db` — **not Airtable** |
| Workflow engine | **None / not n8n** — agents + tools in-process |

## Explicitly NOT used

- n8n (no workflows, no Chat Trigger, no vector-store form trigger)
- Airtable
- Public SaaS chat embed as the primary path

## URLs

- Site: http://192.168.1.210/
- Health: http://192.168.1.210/api/health
- Chat: `POST http://192.168.1.210/api/chat`
- Direct API: `http://127.0.0.1:8791`

## Runtime services

```bash
sudo systemctl status hermes-proxy-xai sol-right-api nginx
```

| Unit | Role |
|------|------|
| `nginx` | Serves website + reverse-proxies `/api/` |
| `sol-right-api` | Dual-agent FastAPI backend |
| `hermes-proxy-xai` | OpenAI-compatible proxy to xAI OAuth (Grok) |

## Dual-agent flow

1. Visitor messages Dave in the website widget.
2. Dave collects address + usage (and optional site details).
3. `solar_estimate` calls Google Geocoding + Solar buildingInsights (server-side).
4. **Solar Analyst** (same reasoning model) writes the homeowner-facing analysis.
5. UI shows georeferenced roof overlay, aerial image, layout map, how-it-works graphic.
6. After name + phone, `create_lead` writes SQLite CRM row.

## Google Solar notes

- Google Solar API receives **lat/lng only** (from geocoded address).
- Bill / kWh / rate / offset select system size from Google panel configs.
- Roof/electrical/HOA answers improve confidence + CRM notes.
- Overlay panels are **georeferenced** onto the roof (UTM GeoTIFF transform), not freehand.

## Paths

```
/opt/sol-right/
  website/           # nginx document root
  app/
    agent.py         # Dave (agent 1)
    solar_analyst.py # Solar Analyst (agent 2)
    imagery.py       # georeferenced roof overlays
    tools/solar.py
    tools/leads.py
    db.py
  data/leads.db      # local CRM
  .env               # secrets (GOOGLE_MAPS_API_KEY, model IDs)
```

## Config

`/opt/sol-right/.env`:

- `LLM_MODEL=grok-4.20-0309-reasoning`  (Dave)
- `SOLAR_ANALYST_MODEL=grok-4.20-0309-reasoning`  (Analyst)
- `GOOGLE_MAPS_API_KEY=...`  (Geocoding + Solar)

## Brief mapping (original Automation Brief → this build)

| Original brief item | This build |
|---------------------|------------|
| n8n Chat Trigger / widget | nginx website chat widget → `/api/chat` |
| AI Tools Agent + 2 tools | Dave + tools `solar_estimate`, `create_lead` |
| SolarEstimateTool sub-workflow | `solar_estimate` + Solar Analyst |
| CreateLeadTool → Airtable | `create_lead` → SQLite |
| Knowledge base vector store | `app/kb/company.md` in system prompt |
| Gemini 2.5 Flash | Grok 4.20 Reasoning (both agents) via Hermes proxy |

## Test

```bash
curl -s http://192.168.1.210/api/health | python3 -m json.tool

curl -s http://127.0.0.1:8791/api/tools/solar_estimate \
  -H 'Content-Type: application/json' \
  -d '{"address":"1513 18th St NW, Cleveland, TN 37311","monthly_bill_usd":375,"monthly_usage_kwh":1800,"usd_per_kwh":0.14}' \
  | python3 -m json.tool
```

## Persistent LAN IP

`192.168.1.210/24` in `/etc/netplan/50-cloud-init.yaml` (with `192.168.1.191/24`).
