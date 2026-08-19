# Retell AI setup for SOL-RIGHT outbound calls

**Status (lab):**
- API key: configured
- From number: +1 (423) 464-7493 bound outbound → agent_3f938b75a9e4c545737bff7db2
- Agent prompt: dynamic variables + AI identity + opt-out
- Public HTTPS webhook: NOT yet (site is LAN `107.221.94.155` only). Call results can still be polled via Get Call; set a tunnel later for `/api/voice/retell-webhook`.

## Architecture (what calls what)

```
Website chat (Dave)
  → create_lead (name, phone, intent, explicit consent, transcript)
  → DNC check
  → POST https://api.retellai.com/v2/create-phone-call   ← THIS places the call
       from_number = your Retell number
       to_number   = customer
       override_agent_id = agent_3f938b75a9e4c545737bff7db2
       retell_llm_dynamic_variables = savings + name + address...

Retell phone agent talks to customer
  → Retell webhook → POST /api/voice/retell-webhook
  → CRM writeback (score, meeting, opt-out)
```

**Important:** `/api/ai-calls/claim` is for a *local* phone-agent worker.
With Retell you usually do **not** need claim — Retell places the call via API.
Keep claim as a fallback / debug path.

---

## 1) Retell dashboard checklist

### A. API key
1. Dashboard → **Settings → API Keys**
2. Create key → copy once
3. Put in `/opt/sol-right/.env` as `RETELL_API_KEY=...`

### B. Phone number (required for outbound)
1. Dashboard → **Phone Numbers**
2. Buy or import a US number
3. Open the number → bind **Outbound agent** =
   `agent_3f938b75a9e4c545737bff7db2`
4. Complete **KYC** if Retell prompts (outbound blocked without it)
5. Copy number in E.164 form, e.g. `+14245550100`
6. Put in `.env` as `RETELL_FROM_NUMBER=+1...`

### C. Agent prompt variables
In the agent prompt editor, use double-brace variables Retell injects from our API:

| Variable | Meaning |
|---|---|
| `{{customer_name}}` | Full name |
| `{{first_name}}` | First name |
| `{{address}}` | Service address |
| `{{monthly_bill_usd}}` | Bill |
| `{{recommended_panels}}` | Panels |
| `{{system_size_kw}}` | kW |
| `{{estimated_monthly_savings_usd}}` | $/mo savings |
| `{{estimated_yearly_savings_usd}}` | $/yr |
| `{{estimated_10yr_savings_usd}}` | 10yr |
| `{{consent_verbatim}}` | Their yes text |
| `{{company_name}}` | SOL-RIGHT Solar |
| `{{opening_script}}` | AI identity + opt-out open |
| `{{lead_id}}` / `{{queue_id}}` | CRM ids |

Suggested open (or rely on `{{opening_script}}`):

```
{{opening_script}}

You already estimated about ${{estimated_monthly_savings_usd}}/month in savings
for {{address}} with about {{recommended_panels}} panels ({{system_size_kw}} kW).
Confirm that ballpark, then qualify budget, authority, need, timeline.
Never guarantee exact install price; give ranges and offer a site survey.
If they say stop / do not call, end immediately and mark opt-out.
```

### D. Webhooks (Retell → your server)
Retell must reach a **public HTTPS URL**. LAN-only `http://107.221.94.155` will **not** work from Retell’s cloud.

Options:
1. **Cloudflare Tunnel / ngrok / Tailscale Funnel** to this host, then:
   - Webhook URL: `https://YOUR_PUBLIC_HOST/api/voice/retell-webhook`
2. Or any reverse proxy with TLS to nginx → `/api/` → `127.0.0.1:8791`

In Retell:
1. Dashboard → **Settings → Webhooks** (or agent-level webhook)
2. URL = `https://YOUR_PUBLIC_HOST/api/voice/retell-webhook`
3. Subscribe at least:
   - `call_started`
   - `call_ended`
   - `call_analyzed`

Optional custom analysis fields on the agent (for CRM scoring):
`lead_score`, `qual_budget`, `qual_authority`, `qual_need`, `qual_timeline`,
`meeting_slot`, `human_transfer`, `opt_out`, `call_status`

---

## 2) SOL-RIGHT `.env`

```bash
# Retell
RETELL_API_KEY=key_xxxxxxxx
RETELL_AGENT_ID=agent_3f938b75a9e4c545737bff7db2
RETELL_FROM_NUMBER=+1XXXXXXXXXX

# Public base used inside webhook payload callbacks (use tunnel HTTPS in prod)
PUBLIC_BASE_URL=https://YOUR_PUBLIC_HOST
```

Then:
```bash
sudo systemctl restart sol-right-api
curl -s http://127.0.0.1:8791/api/health | python3 -m json.tool
```

---

## 3) How a customer gets called

1. Customer chats with Dave → savings estimate  
2. Confirms name + phone  
3. Says **Yes** to:
   > Yes, you can call me with an AI agent to discuss my solar savings estimate and an estimated installation cost.
4. Backend:
   - stores lead + full transcript
   - DNC check
   - `POST /v2/create-phone-call` to Retell **immediately**
5. Customer’s phone rings from `RETELL_FROM_NUMBER`
6. After call, Retell hits `/api/voice/retell-webhook` → CRM updated

### Manual test (after keys set)
```bash
# Re-fire outbound for an existing consented lead
curl -s -X POST http://127.0.0.1:8791/api/voice/dispatch/4 | python3 -m json.tool
```

### Debug without Retell keys
```bash
# Pull next queue item + scripts (local worker mode)
curl -s -X POST http://127.0.0.1:8791/api/ai-calls/claim | python3 -m json.tool
```

---

## 4) Common failures

| Symptom | Fix |
|---|---|
| `queued_local_only` | `RETELL_API_KEY` missing |
| HTTP 401 from Retell | Bad API key |
| HTTP 400 from_number | Number not in Retell account / not E.164 |
| Outbound not allowed | Complete KYC; bind number to outbound agent |
| Webhooks never arrive | Need public HTTPS tunnel; LAN IP won’t work |
| Wrong agent prompt | Check `RETELL_AGENT_ID` matches dashboard agent |

---

## 5) Compliance (already coded)

- Explicit website consent before dial  
- DNC local store (National/TN vendor hooks ready)  
- Opening script identifies **AI + SOL-RIGHT** and offers opt-out  
- Opt-out API: `POST /api/voice/opt-out`

---

## 6) Quick path for you right now

1. Get API key + from-number in Retell  
2. Bind phone number → agent `agent_3f938b75a9e4c545737bff7db2`  
3. Put keys in `.env`, restart API  
4. Run a website consent flow OR `POST /api/voice/dispatch/{lead_id}`  
5. Phone should ring  
6. Later: expose HTTPS webhook for call results  

If you paste your Retell **from number** (and confirm API key is in the dashboard), I can write the `.env` values and run a live test dispatch (without printing secrets).
