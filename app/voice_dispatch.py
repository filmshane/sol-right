"""Immediate voice dispatch on explicit AI-call consent (speed-to-lead).

Supports:
  1) Retell AI native: POST https://api.retellai.com/v2/create-phone-call
  2) Generic webhook: VOICE_WEBHOOK_URL
  3) Local queue only: /api/ai-calls/claim (no external keys)

Retell dashboard agent (example):
  agent_3f938b75a9e4c545737bff7db2
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app import db
from app.config import Settings

log = logging.getLogger("sol-right.voice")

RETELL_CREATE_CALL_URL = "https://api.retellai.com/v2/create-phone-call"


PHONE_OPENING_SCRIPT = """\
Hi {name}, this is an AI representative calling on behalf of {company} ({tagline}).
I'm an automated assistant — not a human. You can ask me to stop at any time and I will end the call and add you to our do-not-call list.
I'm following up on the website chat where you asked about solar savings for {address_or_home}.
Is now still an OK time for a quick 3–5 minute call about estimated savings and install cost ranges?
"""

PHONE_QUAL_SCRIPT = """\
Qualification goals (BANT + light MEDDIC):
1) Budget — comfortable monthly payment range or cash vs finance preference
2) Authority — are you the homeowner / decision maker? anyone else involved?
3) Need — main goal (lower bill, backup power, go green, home value)
4) Timeline — when do you want install / decision (weeks, months, researching)
5) Metrics — current bill awareness; confirm savings ballpark from chat
6) Decision process — next step they want (site survey, human specialist, email quote)
7) Competition/paper process — any other quotes in progress?

Scoring (0-100):
- +25 decision maker / homeowner confirmed
- +20 timeline <= 90 days
- +15 clear bill-pain / savings interest
- +15 budget/financing path identified
- +15 address in service area + usable roof from chat estimate
- +10 agreed to meeting or human transfer
Lead score >= 70 → offer calendar hold or warm transfer to human.
Lead score 40-69 → nurture + optional callback.
Lead score < 40 → polite close, no pressure.

Always:
- Identify company + AI nature at open
- Offer opt-out / stop
- Never argue DNC; end call and log opt-out
- Do not guarantee tax credits or exact pricing; ranges only pending site survey
"""


def _to_e164(phone: str | None) -> str | None:
    digits = re.sub(r"\D+", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"+1{digits}"


def build_dynamic_variables(settings: Settings, lead: dict[str, Any], queue_id: int) -> dict[str, str]:
    """Variables injected into Retell agent prompt ({{var_name}}). All values MUST be strings."""
    name = str(lead.get("name") or "there")
    first = name.split()[0] if name else "there"
    opening = PHONE_OPENING_SCRIPT.format(
        name=first,
        company=settings.company_name,
        tagline=settings.company_tagline,
        address_or_home=str(lead.get("address") or "your home"),
    )
    # Include both our names and the simple aliases from Retell examples
    return {
        # Retell example aliases
        "name": name,
        "callback_reason": "Solar savings follow-up from website chat",
        # Primary vars used in agent prompt
        "customer_name": name,
        "first_name": first,
        "customer_phone": str(lead.get("phone") or ""),
        "address": str(lead.get("address") or ""),
        "intent": str(lead.get("intent") or "solar_savings"),
        "monthly_bill_usd": str(lead.get("monthly_bill_usd") if lead.get("monthly_bill_usd") is not None else ""),
        "recommended_panels": str(lead.get("recommended_panels") if lead.get("recommended_panels") is not None else ""),
        "system_size_kw": str(lead.get("system_size_kw") if lead.get("system_size_kw") is not None else ""),
        "estimated_annual_kwh": str(
            lead.get("estimated_annual_kwh") if lead.get("estimated_annual_kwh") is not None else ""
        ),
        "estimated_monthly_savings_usd": str(
            lead.get("estimated_monthly_savings_usd")
            if lead.get("estimated_monthly_savings_usd") is not None
            else ""
        ),
        "estimated_yearly_savings_usd": str(
            lead.get("estimated_yearly_savings_usd")
            if lead.get("estimated_yearly_savings_usd") is not None
            else ""
        ),
        "estimated_10yr_savings_usd": str(
            lead.get("estimated_10yr_savings_usd")
            if lead.get("estimated_10yr_savings_usd") is not None
            else ""
        ),
        "consent_verbatim": str(lead.get("consent_verbatim") or ""),
        "company_name": str(settings.company_name),
        "company_tagline": str(settings.company_tagline),
        "service_area": str(settings.service_area),
        "lead_id": str(lead.get("id") or ""),
        "queue_id": str(queue_id),
        "chat_session_id": str(lead.get("chat_session_id") or ""),
        "opening_script": opening,
    }


def build_voice_payload(
    settings: Settings,
    *,
    lead: dict[str, Any],
    queue_id: int,
    dnc: dict[str, Any],
) -> dict[str, Any]:
    name = lead.get("name") or "there"
    address = lead.get("address") or "your home"
    opening = PHONE_OPENING_SCRIPT.format(
        name=name.split()[0] if name else "there",
        company=settings.company_name,
        tagline=settings.company_tagline,
        address_or_home=address,
    )
    return {
        "event": "outbound_ai_call",
        "speed_to_lead": True,
        "company": {
            "name": settings.company_name,
            "tagline": settings.company_tagline,
            "phone": settings.contact_phone,
            "email": settings.contact_email,
            "service_area": settings.service_area,
        },
        "call": {
            "to": lead.get("phone"),
            "to_e164": dnc.get("e164") or _to_e164(str(lead.get("phone") or "")),
            "queue_id": queue_id,
            "lead_id": lead.get("id"),
            "window": lead.get("ai_call_window") or "immediate",
            "purpose": "savings_explanation_install_estimate_qualification",
        },
        "consent": {
            "ai_call_consent": bool(lead.get("ai_call_consent")),
            "consent_text": lead.get("consent_text"),
            "consent_verbatim": lead.get("consent_verbatim"),
            "consent_recorded_at": lead.get("consent_recorded_at"),
        },
        "dnc": {
            "status": dnc.get("status"),
            "lists": dnc.get("lists") or [],
            "ok_to_call": dnc.get("ok_to_call"),
        },
        "lead": {
            "name": lead.get("name"),
            "phone": lead.get("phone"),
            "email": lead.get("email"),
            "address": lead.get("address"),
            "intent": lead.get("intent"),
            "monthly_bill_usd": lead.get("monthly_bill_usd"),
            "recommended_panels": lead.get("recommended_panels"),
            "system_size_kw": lead.get("system_size_kw"),
            "estimated_annual_kwh": lead.get("estimated_annual_kwh"),
            "estimated_monthly_savings_usd": lead.get("estimated_monthly_savings_usd"),
            "estimated_yearly_savings_usd": lead.get("estimated_yearly_savings_usd"),
            "estimated_10yr_savings_usd": lead.get("estimated_10yr_savings_usd"),
            "chat_session_id": lead.get("chat_session_id"),
        },
        "chat_transcript": lead.get("chat_transcript_json"),
        "chat_metadata": lead.get("chat_metadata_json"),
        "retell_llm_dynamic_variables": build_dynamic_variables(settings, lead, queue_id),
        "agent_scripts": {
            "opening": opening,
            "qualification": PHONE_QUAL_SCRIPT,
            "identify_ai": True,
            "offer_opt_out": True,
            "respect_dnc_national_and_tn": True,
        },
        "callbacks": {
            "writeback_url": f"{settings.public_base_url.rstrip('/')}/api/voice/writeback",
            "opt_out_url": f"{settings.public_base_url.rstrip('/')}/api/voice/opt-out",
            "retell_webhook_url": f"{settings.public_base_url.rstrip('/')}/api/voice/retell-webhook",
            "claim_url": f"{settings.public_base_url.rstrip('/')}/api/ai-calls/claim",
        },
    }


async def _dispatch_retell(
    settings: Settings,
    *,
    lead: dict[str, Any],
    queue_id: int,
    dnc: dict[str, Any],
) -> dict[str, Any]:
    api_key = (getattr(settings, "retell_api_key", None) or "").strip()
    agent_id = (getattr(settings, "retell_agent_id", None) or "").strip()
    from_number = (
        (getattr(settings, "retell_from_number", None) or "").strip()
        or (getattr(settings, "voice_from_number", None) or "").strip()
    )
    to_number = dnc.get("e164") or _to_e164(str(lead.get("phone") or ""))

    if not api_key:
        return {"ok": False, "error": "RETELL_API_KEY not set"}
    if not agent_id:
        return {"ok": False, "error": "RETELL_AGENT_ID not set"}
    if not from_number:
        return {"ok": False, "error": "RETELL_FROM_NUMBER / VOICE_FROM_NUMBER not set (E.164)"}
    if not to_number:
        return {"ok": False, "error": "customer phone not valid E.164"}

    from_e164 = _to_e164(from_number) or from_number
    dyn = build_dynamic_variables(settings, lead, queue_id)

    body: dict[str, Any] = {
        "from_number": from_e164,
        "to_number": to_number,
        "override_agent_id": agent_id,
        "retell_llm_dynamic_variables": dyn,
        "metadata": {
            "lead_id": str(lead.get("id") or ""),
            "queue_id": str(queue_id),
            "company": settings.company_name,
            "source": "sol-right-website-chat",
        },
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        r = await client.post(RETELL_CREATE_CALL_URL, headers=headers, json=body)
        text = r.text[:3000]
        if r.status_code >= 400:
            return {
                "ok": False,
                "provider": "retell",
                "http_status": r.status_code,
                "error": text,
                "request": {
                    "from_number": from_e164,
                    "to_number": to_number,
                    "agent_id": agent_id,
                },
            }
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": text}
        call_id = data.get("call_id") or data.get("id")
        return {
            "ok": True,
            "provider": "retell",
            "mode": "retell_create_phone_call",
            "http_status": r.status_code,
            "voice_call_id": call_id,
            "response": data,
            "queue_id": queue_id,
            "lead_id": lead.get("id"),
            "to_number": to_number,
            "from_number": from_e164,
            "agent_id": agent_id,
        }


async def _dispatch_generic_webhook(
    settings: Settings,
    *,
    lead: dict[str, Any],
    queue_id: int,
    dnc: dict[str, Any],
) -> dict[str, Any]:
    webhook = (getattr(settings, "voice_webhook_url", None) or "").strip()
    if not webhook:
        return {"ok": False, "error": "VOICE_WEBHOOK_URL not set"}

    payload = build_voice_payload(settings, lead=lead, queue_id=queue_id, dnc=dnc)
    headers = {"Content-Type": "application/json"}
    secret = (getattr(settings, "voice_webhook_secret", None) or "").strip()
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
        headers["X-Sol-Right-Voice-Secret"] = secret

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(webhook, json=payload, headers=headers)
        body_text = r.text[:2000]
        if r.status_code >= 400:
            return {"ok": False, "provider": "webhook", "http_status": r.status_code, "error": body_text}
        voice_call_id = None
        try:
            data = r.json()
            voice_call_id = (
                data.get("call_id")
                or data.get("id")
                or data.get("callId")
                or (data.get("data") or {}).get("id")
            )
        except Exception:
            data = {"raw": body_text}
        return {
            "ok": True,
            "provider": "webhook",
            "mode": "webhook",
            "http_status": r.status_code,
            "voice_call_id": voice_call_id,
            "response": data,
            "queue_id": queue_id,
            "lead_id": lead.get("id"),
        }


async def dispatch_outbound_call(
    settings: Settings,
    *,
    lead_id: int,
    queue_id: int,
    dnc: dict[str, Any],
) -> dict[str, Any]:
    """Fire Retell (preferred) or generic webhook immediately after consent + DNC clear."""
    lead = await db.get_lead(settings.db_path, lead_id)
    if not lead:
        return {"ok": False, "error": "lead not found"}

    if not dnc.get("ok_to_call"):
        await db.mark_voice_dispatch(
            settings.db_path,
            lead_id=lead_id,
            queue_id=queue_id,
            status="blocked_dnc",
            error=dnc.get("message"),
        )
        return {"ok": False, "blocked_dnc": True, "dnc": dnc}

    if not lead.get("ai_call_consent"):
        await db.mark_voice_dispatch(
            settings.db_path,
            lead_id=lead_id,
            queue_id=queue_id,
            status="blocked_no_consent",
            error="missing explicit AI call consent",
        )
        return {"ok": False, "error": "missing consent"}

    retell_key = (getattr(settings, "retell_api_key", None) or "").strip()
    webhook = (getattr(settings, "voice_webhook_url", None) or "").strip()

    try:
        if retell_key:
            result = await _dispatch_retell(
                settings, lead=lead, queue_id=queue_id, dnc=dnc
            )
        elif webhook:
            result = await _dispatch_generic_webhook(
                settings, lead=lead, queue_id=queue_id, dnc=dnc
            )
        else:
            await db.mark_voice_dispatch(
                settings.db_path,
                lead_id=lead_id,
                queue_id=queue_id,
                status="queued_local_only",
                error="RETELL_API_KEY / VOICE_WEBHOOK_URL not set; use /api/ai-calls/claim",
            )
            return {
                "ok": True,
                "mode": "local_queue",
                "message": (
                    "No Retell/voice keys configured — call left in local AI queue. "
                    "Set RETELL_API_KEY + RETELL_AGENT_ID + RETELL_FROM_NUMBER, or use /api/ai-calls/claim."
                ),
                "queue_id": queue_id,
                "lead_id": lead_id,
            }

        if result.get("ok"):
            await db.mark_voice_dispatch(
                settings.db_path,
                lead_id=lead_id,
                queue_id=queue_id,
                status="dispatched",
                voice_call_id=str(result.get("voice_call_id") or "") or None,
            )
        else:
            await db.mark_voice_dispatch(
                settings.db_path,
                lead_id=lead_id,
                queue_id=queue_id,
                status=f"failed_http_{result.get('http_status') or 'err'}",
                error=str(result.get("error") or "")[:1000],
            )
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("voice dispatch failed")
        await db.mark_voice_dispatch(
            settings.db_path,
            lead_id=lead_id,
            queue_id=queue_id,
            status="failed_exception",
            error=str(exc)[:1000],
        )
        return {"ok": False, "error": str(exc)}


async def handle_retell_webhook(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """Process Retell call webhooks (call_started / call_ended / call_analyzed)."""
    from app.tools import leads as leads_tool

    event = payload.get("event") or payload.get("event_type") or ""
    call = payload.get("call") or payload
    call_id = call.get("call_id") or call.get("id")
    meta = call.get("metadata") or {}
    lead_id = meta.get("lead_id") or (call.get("retell_llm_dynamic_variables") or {}).get("lead_id")
    queue_id = meta.get("queue_id") or (call.get("retell_llm_dynamic_variables") or {}).get("queue_id")

    try:
        lead_id_i = int(lead_id) if lead_id not in (None, "") else None
    except (TypeError, ValueError):
        lead_id_i = None
    try:
        queue_id_i = int(queue_id) if queue_id not in (None, "") else None
    except (TypeError, ValueError):
        queue_id_i = None

    # Map Retell events → CRM
    if event in ("call_started", "call.started"):
        if lead_id_i:
            await db.update_lead(
                settings.db_path,
                lead_id_i,
                {
                    "ai_call_status": "in_progress",
                    "voice_call_id": str(call_id) if call_id else None,
                    "voice_dispatch_status": "in_progress",
                },
            )
        return {"ok": True, "handled": event, "lead_id": lead_id_i}

    if event in ("call_ended", "call.ended", "call_analyzed", "call.analyzed"):
        transcript = call.get("transcript") or call.get("transcript_object")
        analysis = call.get("call_analysis") or call.get("analysis") or {}
        disconnection = call.get("disconnection_reason") or call.get("end_reason")
        custom = analysis.get("custom_analysis_data") or analysis.get("custom_data") or {}

        # Opt-out detection from analysis custom fields if agent sets them
        opt_out = bool(custom.get("opt_out") or custom.get("dnc") or custom.get("do_not_call"))

        writeback = {
            "lead_id": lead_id_i,
            "queue_id": queue_id_i,
            "phone_call_result": custom.get("result") or disconnection or "completed",
            "phone_call_notes": json.dumps(
                {
                    "event": event,
                    "call_id": call_id,
                    "disconnection_reason": disconnection,
                    "transcript_excerpt": (transcript if isinstance(transcript, str) else None),
                    "analysis": analysis,
                }
            )[:4000],
            "qual_budget": custom.get("qual_budget") or custom.get("budget"),
            "qual_authority": custom.get("qual_authority") or custom.get("authority"),
            "qual_need": custom.get("qual_need") or custom.get("need"),
            "qual_timeline": custom.get("qual_timeline") or custom.get("timeline"),
            "qual_decision_process": custom.get("qual_decision_process"),
            "meeting_slot": custom.get("meeting_slot"),
            "human_transfer": custom.get("human_transfer"),
            "lead_score": custom.get("lead_score"),
            "call_status": custom.get("call_status")
            or ("opted_out" if opt_out else "completed"),
            "opt_out": opt_out,
            "opt_out_reason": custom.get("opt_out_reason") or "retell_call_opt_out",
            "phone": call.get("to_number") or call.get("to"),
        }
        if lead_id_i:
            out = await leads_tool.phone_writeback(settings.db_path, writeback)
            if call_id:
                await db.update_lead(
                    settings.db_path,
                    lead_id_i,
                    {"voice_call_id": str(call_id), "voice_dispatch_status": "completed"},
                )
            return {"ok": True, "handled": event, "writeback": out}
        return {"ok": True, "handled": event, "note": "no lead_id in metadata"}

    return {"ok": True, "handled": False, "event": event, "call_id": call_id}
