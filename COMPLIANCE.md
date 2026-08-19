# Build 2 Compliance Audit

Compared against `~/Build-2-Automation-Brief.txt`  
Live stack rules (banner): **no n8n, no Airtable** — nginx site + widget + dual agents + SQLite.

Generated: 2026-08-10 · App version **1.4.0**

## Intent mapping (original → implemented)

| Brief requirement | Status | Implementation |
|---|---|---|
| Website lead-gen chat agent | **DONE** | nginx site + Dave widget @ http://192.168.1.210/ |
| Local hosted web server | **DONE** | nginx (Apache not used; nginx was already primary) |
| Company SOL-RIGHT Solar / Installed RIGHT!! | **DONE** | Site + agent identity |
| Solar marketing site + sunshine visuals | **DONE** | Marketing page, gallery photos, animations, how-solar diagram |
| Chat widget on site | **DONE** | Top-right launcher → slide-in panel (brief said bottom-right; UX later moved per request) |
| FAQ answers from knowledge base | **DONE** | Chroma vector DB + `retrieve_knowledge` tool + `industry_faq.md` + `company.md` |
| Collect US address + monthly bill | **DONE** | Dave intake Wave A (+ optional kWh/rate/site Wave B) |
| Geocode → Google Solar buildingInsights | **DONE** | `solar_estimate` tool server-side |
| Format estimate summary fields | **DONE** | recommended/max panels, kWh, notes, segments, confidence |
| Present estimate in plain English | **DONE** | Dave + Solar Analyst narrative |
| Ask name + phone after estimate | **DONE** | Specialist handoff copy in prompt |
| Store lead + estimate in CRM | **DONE** | SQLite (replaces Airtable) with session id + extended intake fields |
| Confirm 24h follow-up | **DONE** | Prompt + create_lead message |
| Solar API fail → manual follow-up | **DONE** | Prompted behavior |
| Two tools: SolarEstimate + CreateLead | **DONE** | Plus `retrieve_knowledge` for FAQ vector retrieve |
| USA-only validation | **DONE** | Geocode country check + address heuristics |
| API keys server-side only | **DONE** | `/opt/sol-right/.env`, never browser |
| Session tracking + unique web id | **DONE** | `session_id` + `web_id` in SQLite `chat_sessions` |
| Welcome: Hello Welcome to Sol-Right… | **DONE** | `/api/welcome` exact brief string |
| Vector store for KB | **DONE** | Free local **ChromaDB** @ `/opt/sol-right/data/chroma` (41 chunks) |
| n8n workflows | **N/A by design** | Replaced per project decision |
| Airtable CRM | **N/A by design** | SQLite CRM |
| Gemini 2.5 Flash single agent | **SUPERSEDED** | Dual agents on Grok 4.20 Reasoning (your model choice) |
| n8n form upload for KB | **SUPERSEDED** | Markdown files under `app/kb/` + `/api/knowledge/reindex` |

## Live verification snapshot

- Services: `nginx`, `sol-right-api`, `hermes-proxy-xai` = active
- Health reports:
  - dual agents both `grok-4.20-0309-reasoning`
  - `vector_db.ok=true`, engine `chromadb`, count ≥ 40
  - `crm=sqlite`, `stack=nginx + widget + dual agents`
- CRM fields include Name, Phone, Address, Monthly Bill, Lat/Lng, annual/monthly kWh, max/recommended panels, Notes, chat_session_id (+ richer intake)
- Overlay: georeferenced UTM placement on roof

## Gaps / residual differences (acceptable)

1. **Chat corner**: brief original bottom-right → implemented top-right per later UX direction.
2. **Model**: Gemini not used; Grok 4.20 Reasoning for both agents per your direction.
3. **CRM product**: Airtable → SQLite (your direction).
4. **Orchestrator**: n8n → FastAPI dual-agent (your direction).
5. **KB loading**: file-based markdown + reindex API instead of n8n upload form.

## How to re-check

```bash
curl -s http://192.168.1.210/api/health | python3 -m json.tool
curl -s http://192.168.1.210/api/welcome | python3 -m json.tool
curl -s -X POST http://127.0.0.1:8791/api/knowledge/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query":"solar panel warranty years"}' | python3 -m json.tool
```

## Verdict

**Build 2 core automation purpose is implemented** under the locked local architecture (nginx + widget + 2 agents + SQLite + Chroma FAQ DB + Google Solar). Original n8n/Airtable/Gemini lines are historical only.
