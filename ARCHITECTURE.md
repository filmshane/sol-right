# Build 2 — Implementation Record (authoritative)

Original planning brief: `~/Build-2-Automation-Brief.txt`

## Decision (locked)

**n8n is not used in this build.**  
**Airtable is not used in this build.**

Replaced by:

1. **Local website** hosted on **nginx** (`http://192.168.1.210/`)
2. **AI-Agent chat widget** (AI-Agent Dave) on that site
3. **Two backend agents** (same Grok 4.20 Reasoning model family):
   - Dave — conversation / intake / tools
   - Solar Analyst — quote interpretation from Google Solar + intake
4. **Local SQLite CRM** at `/opt/sol-right/data/leads.db`

## Runtime map

```
Browser → nginx :80 @ 192.168.1.210
            ├─ /          static SOL-RIGHT site + widget
            └─ /api/*     FastAPI dual-agent backend (127.0.0.1:8791)
                            ├─ Dave (grok-4.20-0309-reasoning)
                            ├─ Solar Analyst (grok-4.20-0309-reasoning)
                            ├─ Google Geocoding + Solar (server-side)
                            └─ SQLite leads
```

## Code home

`/opt/sol-right/` — see `README.md` for full detail.

Updated: 2026-08-10
