from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app import db
from app import dnc as dnc_mod
from app import voice_dispatch
from app.config import get_settings


# Canonical consent language the website agent must use (or equivalent yes)
DEFAULT_CONSENT_TEXT = (
    "Yes, you can call me with an AI agent to discuss my solar savings estimate "
    "and an estimated installation cost."
)


def _pack_notes(payload: dict[str, Any]) -> str | None:
    parts = []
    if payload.get("notes"):
        parts.append(str(payload.get("notes")))
    labeled = [
        ("Intent", "intent"),
        ("Ownership", "property_ownership"),
        ("HOA", "hoa_restrictions"),
        ("Roof material", "roof_material"),
        ("Roof age/condition", "roof_age_condition"),
        ("Shading", "shading_notes"),
        ("Service panel", "service_panel_amps"),
        ("Large loads", "large_loads"),
        ("Battery", "battery_interest"),
        ("Financing", "financing_preference"),
        ("Timeline", "timeline"),
        ("Future usage", "future_usage_plans"),
        ("Monthly kWh", "monthly_usage_kwh"),
        ("Rate $/kWh", "usd_per_kwh"),
        ("Target offset %", "target_offset_pct"),
        ("Quote confidence", "quote_confidence"),
        ("Est. monthly savings $", "estimated_monthly_savings_usd"),
        ("Est. yearly savings $", "estimated_yearly_savings_usd"),
        ("Est. 10yr savings $", "estimated_10yr_savings_usd"),
        ("AI call consent", "ai_call_consent"),
        ("Consent verbatim", "consent_verbatim"),
        ("AI call window", "ai_call_window"),
    ]
    extra = []
    for label, key in labeled:
        val = payload.get(key)
        if val is not None and str(val).strip() != "":
            extra.append(f"{label}: {val}")
    if extra:
        parts.append("Intake details:\n- " + "\n- ".join(extra))
    return "\n\n".join(parts) if parts else None


def _normalize_phone(phone: str) -> str:
    return dnc_mod.display_phone(phone)


def _truthy(val: Any) -> bool:
    if val is True or val == 1:
        return True
    if val is False or val is None or val == 0:
        return False
    s = str(val).strip().lower()
    return s in {
        "1",
        "true",
        "yes",
        "y",
        "approve",
        "approved",
        "ok",
        "okay",
        "consent",
        "i agree",
        "agreed",
        "you can call",
        "call me",
    }


def _looks_like_consent(text: str | None) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    if t in {"yes", "y", "yeah", "yep", "sure", "ok", "okay", "i agree", "approved"}:
        return True
    keys = [
        "yes, you can call me",
        "you can call me with an ai",
        "ai agent to discuss",
        "ai representative",
        "you may call",
        "i consent",
        "i agree to the call",
        "call me",
    ]
    return any(k in t for k in keys)


async def create_lead_record(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """CRM lead + transcript + DNC check + immediate voice dispatch on consent."""
    settings = get_settings()
    name = (payload.get("name") or "").strip()
    phone_raw = (payload.get("phone") or "").strip()
    intent = (payload.get("intent") or "").strip()

    if not phone_raw:
        return {"ok": False, "error": "phone is required", "error_code": "MISSING_PHONE"}
    if not name:
        return {"ok": False, "error": "name is required", "error_code": "MISSING_NAME"}
    if not intent:
        intent = "solar_savings_estimate"

    phone = _normalize_phone(phone_raw)
    consent_verbatim = (payload.get("consent_verbatim") or payload.get("consent_text") or "").strip()
    explicit = _truthy(payload.get("ai_call_consent")) or _looks_like_consent(consent_verbatim)
    queue_flag = _truthy(payload.get("queue_ai_call"))
    want_call = explicit and (queue_flag or explicit)

    consent_text = (payload.get("consent_text") or DEFAULT_CONSENT_TEXT).strip()
    if want_call and not consent_verbatim:
        consent_verbatim = consent_text

    # Transcript + metadata (full chat)
    transcript = payload.get("chat_transcript") or payload.get("chat_transcript_json")
    if isinstance(transcript, (list, dict)):
        transcript_json = json.dumps(transcript)
    elif isinstance(transcript, str) and transcript.strip():
        transcript_json = transcript
    else:
        transcript_json = None

    metadata = payload.get("chat_metadata") or payload.get("chat_metadata_json") or {}
    if isinstance(metadata, str):
        metadata_json = metadata
    else:
        metadata_json = json.dumps(
            {
                **(metadata if isinstance(metadata, dict) else {}),
                "source": payload.get("source") or "website-chat",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "user_agent": payload.get("user_agent"),
                "web_id": payload.get("web_id"),
            }
        )

    # DNC check before any outbound
    dnc = await dnc_mod.check_dnc(db_path, phone_raw)
    call_window = (payload.get("ai_call_window") or "immediate").strip()

    fields = {
        "name": name,
        "phone": phone,
        "email": (payload.get("email") or "").strip() or None,
        "address": payload.get("address"),
        "intent": intent,
        "monthly_bill_usd": payload.get("monthly_bill_usd"),
        "monthly_usage_kwh": payload.get("monthly_usage_kwh"),
        "usd_per_kwh": payload.get("usd_per_kwh"),
        "target_offset_pct": payload.get("target_offset_pct"),
        "lat": payload.get("lat"),
        "lng": payload.get("lng"),
        "estimated_annual_kwh": payload.get("estimated_annual_kwh"),
        "estimated_monthly_kwh": payload.get("estimated_monthly_kwh"),
        "max_panels": payload.get("max_panels"),
        "recommended_panels": payload.get("recommended_panels"),
        "system_size_kw": payload.get("system_size_kw"),
        "quote_confidence": payload.get("quote_confidence"),
        "property_ownership": payload.get("property_ownership"),
        "hoa_restrictions": payload.get("hoa_restrictions"),
        "roof_material": payload.get("roof_material"),
        "roof_age_condition": payload.get("roof_age_condition"),
        "shading_notes": payload.get("shading_notes"),
        "service_panel_amps": payload.get("service_panel_amps"),
        "large_loads": payload.get("large_loads"),
        "battery_interest": payload.get("battery_interest"),
        "financing_preference": payload.get("financing_preference"),
        "timeline": payload.get("timeline"),
        "future_usage_plans": payload.get("future_usage_plans"),
        "notes": _pack_notes({**payload, "intent": intent, "consent_verbatim": consent_verbatim}),
        "chat_session_id": payload.get("chat_session_id") or "unknown",
        "source": payload.get("source") or "website-chat",
        "estimated_monthly_savings_usd": payload.get("estimated_monthly_savings_usd"),
        "estimated_yearly_savings_usd": payload.get("estimated_yearly_savings_usd"),
        "estimated_10yr_savings_usd": payload.get("estimated_10yr_savings_usd"),
        "ai_call_consent": 1 if want_call else 0,
        "ai_call_status": "none",
        "ai_call_requested_at": None,
        "ai_call_window": call_window if want_call else None,
        "callback_priority": int(payload.get("callback_priority") or (50 if want_call else 0)),
        "consent_text": consent_text if want_call else None,
        "consent_verbatim": consent_verbatim if want_call else None,
        "consent_recorded_at": datetime.now(timezone.utc).isoformat() if want_call else None,
        "chat_transcript_json": transcript_json,
        "chat_metadata_json": metadata_json,
        "dnc_status": dnc.get("status"),
        "dnc_checked_at": dnc.get("checked_at"),
        "dnc_lists": json.dumps(dnc.get("lists") or []),
        "voice_dispatch_status": "none",
    }

    lead_id = await db.create_lead(db_path, fields)
    queue_id = None
    dispatch = None

    if want_call:
        if not dnc.get("ok_to_call"):
            await db.update_lead(
                db_path,
                lead_id,
                {
                    "ai_call_status": "blocked_dnc",
                    "voice_dispatch_status": "blocked_dnc",
                    "voice_dispatch_error": dnc.get("message"),
                },
            )
            return {
                "ok": True,
                "lead_id": lead_id,
                "phone": phone,
                "ai_call_queued": False,
                "blocked_dnc": True,
                "dnc": dnc,
                "message": (
                    f"Lead #{lead_id} saved. Outbound AI call blocked — number appears on DNC "
                    f"({', '.join(dnc.get('lists') or ['listed'])})."
                ),
            }

        context = {
            "lead_id": lead_id,
            "name": name,
            "phone": phone,
            "address": payload.get("address"),
            "intent": intent,
            "monthly_bill_usd": payload.get("monthly_bill_usd"),
            "recommended_panels": payload.get("recommended_panels"),
            "system_size_kw": payload.get("system_size_kw"),
            "estimated_annual_kwh": payload.get("estimated_annual_kwh"),
            "estimated_monthly_savings_usd": payload.get("estimated_monthly_savings_usd"),
            "estimated_yearly_savings_usd": payload.get("estimated_yearly_savings_usd"),
            "consent_verbatim": consent_verbatim,
            "consent_text": consent_text,
            "chat_session_id": payload.get("chat_session_id"),
            "purpose": "savings + install estimate + BANT/MEDDIC qualification",
        }
        queue_id = await db.enqueue_ai_call(
            db_path,
            lead_id=lead_id,
            phone=phone,
            name=name,
            address=payload.get("address"),
            context_json=json.dumps(context),
            priority=int(fields["callback_priority"] or 50),
            call_window=call_window,
            purpose="savings + install cost + qualification",
        )

        # Speed-to-lead: fire voice platform immediately
        dispatch = await voice_dispatch.dispatch_outbound_call(
            settings,
            lead_id=lead_id,
            queue_id=queue_id,
            dnc=dnc,
        )

        if dispatch.get("ok"):
            msg = (
                f"Lead #{lead_id} saved with consent. AI call initiated for {phone} "
                f"(queue #{queue_id}). They should hear from our AI representative shortly."
            )
        else:
            msg = (
                f"Lead #{lead_id} saved and queued (#{queue_id}), but voice dispatch reported: "
                f"{dispatch.get('error') or dispatch.get('message') or 'see voice_dispatch_status'}."
            )
    else:
        msg = f"Lead #{lead_id} saved with phone {phone}. No AI call (no explicit consent)."

    return {
        "ok": True,
        "lead_id": lead_id,
        "queue_id": queue_id,
        "phone": phone,
        "intent": intent,
        "ai_call_queued": bool(queue_id),
        "ai_call_window": call_window if queue_id else None,
        "consent_recorded": bool(want_call),
        "transcript_stored": bool(transcript_json),
        "dnc": {"status": dnc.get("status"), "ok_to_call": dnc.get("ok_to_call")},
        "voice_dispatch": dispatch,
        "message": msg,
    }


def score_qualification(payload: dict[str, Any]) -> int:
    """BANT/MEDDIC-style score 0-100 from phone-agent writeback fields."""
    score = 0
    auth = (payload.get("qual_authority") or "").lower()
    if any(x in auth for x in ("owner", "decision", "yes", "self", "spouse both")):
        score += 25
    tl = (payload.get("qual_timeline") or payload.get("timeline") or "").lower()
    if any(x in tl for x in ("week", "30", "60", "90", "asap", "month", "soon")):
        score += 20
    need = (payload.get("qual_need") or payload.get("intent") or "").lower()
    if any(x in need for x in ("bill", "save", "backup", "power", "solar", "cost")):
        score += 15
    budget = (payload.get("qual_budget") or payload.get("financing_preference") or "").lower()
    if budget.strip():
        score += 15
    if payload.get("recommended_panels") or payload.get("address"):
        score += 15
    if payload.get("meeting_slot") or payload.get("human_transfer"):
        score += 10
    return min(100, score)


async def phone_writeback(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Phone AI agent writes qualification results back to CRM."""
    lead_id = payload.get("lead_id")
    queue_id = payload.get("queue_id")
    if not lead_id:
        return {"ok": False, "error": "lead_id required"}

    lead = await db.get_lead(db_path, int(lead_id))
    if not lead:
        return {"ok": False, "error": "lead not found"}

    score = payload.get("lead_score")
    if score is None:
        score = score_qualification(payload)

    fields = {
        "lead_score": int(score),
        "qual_budget": payload.get("qual_budget"),
        "qual_authority": payload.get("qual_authority"),
        "qual_need": payload.get("qual_need"),
        "qual_timeline": payload.get("qual_timeline"),
        "qual_decision_process": payload.get("qual_decision_process"),
        "qual_metrics": payload.get("qual_metrics"),
        "meeting_slot": payload.get("meeting_slot"),
        "meeting_booked_at": payload.get("meeting_booked_at")
        or (datetime.now(timezone.utc).isoformat() if payload.get("meeting_slot") else None),
        "human_transfer": 1 if _truthy(payload.get("human_transfer")) else 0,
        "phone_call_result": payload.get("phone_call_result") or payload.get("result"),
        "phone_call_notes": payload.get("phone_call_notes") or payload.get("notes"),
        "phone_call_results_json": json.dumps(payload),
        "ai_call_status": payload.get("call_status")
        or (
            "meeting_booked"
            if payload.get("meeting_slot")
            else "transferred"
            if _truthy(payload.get("human_transfer"))
            else "completed"
        ),
    }
    # drop Nones so we don't wipe columns unintentionally
    fields = {k: v for k, v in fields.items() if v is not None}

    await db.update_lead(db_path, int(lead_id), fields)

    if queue_id:
        await db.complete_ai_call(
            db_path,
            int(queue_id),
            status=str(fields.get("ai_call_status") or "completed"),
            result_notes=fields.get("phone_call_notes"),
        )

    # Opt-out during call
    if _truthy(payload.get("opt_out")) or str(payload.get("phone_call_result") or "").lower() in {
        "opt_out",
        "dnc",
        "do_not_call",
    }:
        await dnc_mod.add_opt_out(
            db_path,
            lead.get("phone") or "",
            reason=payload.get("opt_out_reason") or "phone_call_opt_out",
        )
        await db.update_lead(
            db_path,
            int(lead_id),
            {"dnc_status": "listed", "dnc_lists": json.dumps(["company_internal_dnc"])},
        )

    appointment = None
    try:
        from app import calendar_service
        from app.config import get_settings

        settings = get_settings()
        appointment = await calendar_service.create_appointment_from_writeback(
            db_path,
            lead=lead,
            payload=payload,
            owner_email=settings.owner_email,
            duration_minutes=int(settings.calendar_default_duration_minutes or 30),
        )
    except Exception as exc:  # noqa: BLE001
        appointment = {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "lead_id": int(lead_id),
        "lead_score": int(score),
        "ai_call_status": fields.get("ai_call_status"),
        "appointment": (
            {
                "id": appointment.get("id"),
                "uid": appointment.get("uid"),
                "title": appointment.get("title"),
                "starts_at": appointment.get("starts_at"),
                "ends_at": appointment.get("ends_at"),
                "ics_url": f"/api/calendar/appointments/{appointment.get('id')}.ics",
            }
            if isinstance(appointment, dict) and appointment.get("id")
            else appointment
        ),
        "message": (
            "Phone agent results written to CRM"
            + (
                f"; calendar appointment #{appointment.get('id')} created"
                if isinstance(appointment, dict) and appointment.get("id")
                else ""
            )
        ),
    }
