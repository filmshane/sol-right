from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app import db
from app import imagery
from app import solar_analyst
from app.agent import LeadGenAgent
from app.config import get_settings
from app.tools import solar as solar_tool

settings = get_settings()
agent = LeadGenAgent(settings)

app = FastAPI(title="SOL-RIGHT Lead Gen Agent", version="1.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_LAYOUT_CACHE: dict[str, dict[str, Any]] = {}
_OVERLAY_CACHE: dict[str, bytes] = {}
_ROOF_PNG_CACHE: dict[str, bytes] = {}


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None
    web_id: str | None = None
    visitor_name: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    web_id: str
    reply: str
    visitor_name: str | None = None
    media: list[dict[str, Any]] = Field(default_factory=list)
    estimate: dict[str, Any] | None = None


@app.on_event("startup")
async def startup() -> None:
    await db.init_db(settings.db_path)
    try:
        from app import vector_store

        vector_store.ensure_index(force=True)
    except Exception as exc:  # noqa: BLE001
        # Vector DB is required for FAQs; log via print to journal
        print(f"vector_store init warning: {exc}")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    from app import vector_store

    vs = vector_store.status()
    return {
        "ok": True,
        "service": "sol-right-agent",
        "company": settings.company_name,
        "model": settings.llm_model,
        "solar_analyst_model": settings.solar_analyst_model,
        "agents": {
            "dave_conversation": settings.llm_model,
            "solar_analyst": settings.solar_analyst_model,
            "architecture": "dual-agent: Dave (chat+tools) + Solar Analyst (quote interpretation)",
        },
        "vector_db": vs,
        "llm_base_url": settings.llm_base_url,
        "google_maps_key_configured": bool(settings.maps_key),
        "db_path": settings.db_path,
        "public_base_url": settings.public_base_url,
        "version": "1.4.0",
        "overlay": "georeferenced-utm",
        "crm": "sqlite",
        "stack": "nginx + widget + dual agents (no n8n, no Airtable)",
        "voice": {
            "retell_api_key_configured": bool(settings.retell_api_key),
            "retell_agent_id": settings.retell_agent_id or None,
            "retell_from_number_configured": bool(settings.retell_from_number or settings.voice_from_number),
            "voice_webhook_configured": bool(settings.voice_webhook_url),
            "retell_webhook_path": "/api/voice/retell-webhook",
            "dispatch_path": "/api/voice/dispatch/{lead_id}",
            "claim_path": "/api/ai-calls/claim",
            "docs": "/opt/sol-right/docs/RETELL-SETUP.md",
        },
    }


@app.get("/api/welcome")
async def welcome() -> dict[str, str]:
    return {
        "reply": "Hello, Welcome to Sol-Right, how can I help you today?",
        "agent": "Dave",
        "company": settings.company_name,
        "tagline": settings.company_tagline,
    }


@app.get("/api/knowledge/status")
async def knowledge_status() -> dict[str, Any]:
    from app import vector_store

    return vector_store.status()


@app.post("/api/knowledge/reindex")
async def knowledge_reindex() -> dict[str, Any]:
    from app import vector_store

    return vector_store.ensure_index(force=True)


@app.post("/api/knowledge/retrieve")
async def knowledge_retrieve(payload: dict[str, Any]) -> dict[str, Any]:
    from app import vector_store

    return vector_store.retrieve(str(payload.get("query") or ""), n_results=int(payload.get("n_results") or 4))


def _media_from_tool_trace(tool_trace: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Lead-gen mode: no visuals. Return empty media + savings-rich estimate card data."""
    media: list[dict[str, Any]] = []
    estimate = None
    for item in tool_trace:
        if item.get("tool") != "solar_estimate":
            continue
        result = item.get("result") or {}
        if not result.get("ok"):
            continue
        estimate = {
            "address": result.get("address"),
            "recommendedPanels": result.get("recommendedPanels"),
            "maxPanels": result.get("maxPanels"),
            "systemSizeKw": result.get("systemSizeKw"),
            "yearlyEnergyDcKwh": result.get("yearlyEnergyDcKwh"),
            "monthlyEnergyKwh": result.get("monthlyEnergyKwh"),
            "estimatedMonthlyUsageKwh": result.get("estimatedMonthlyUsageKwh"),
            "assumedUsdPerKwh": result.get("assumedUsdPerKwh"),
            "monthlyBillUsd": result.get("monthlyBillUsd"),
            "monthlyUsageKwhInput": result.get("monthlyUsageKwhInput"),
            "usageSource": result.get("usageSource"),
            "rateSource": result.get("rateSource"),
            "targetOffsetPct": result.get("targetOffsetPct"),
            "quoteConfidence": result.get("quoteConfidence"),
            "customerSummary": result.get("customerSummary"),
            "analystSummary": result.get("analystSummary"),
            "panelWattsAssumed": result.get("panelWattsAssumed"),
            "estimatedMonthlySavingsUsd": result.get("estimatedMonthlySavingsUsd"),
            "estimatedYearlySavingsUsd": result.get("estimatedYearlySavingsUsd"),
            "estimated10YearSavingsUsd": result.get("estimated10YearSavingsUsd"),
            "estimated20YearSavingsUsd": result.get("estimated20YearSavingsUsd"),
            "estimatedNewMonthlyBillUsd": result.get("estimatedNewMonthlyBillUsd"),
            "estimatedBillReductionPct": result.get("estimatedBillReductionPct"),
            "savingsAssumptions": result.get("savingsAssumptions"),
            "agents": result.get("agents"),
        }
        # intentionally no media[] — text savings pitch only
    return media, estimate


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or str(uuid.uuid4())
    web_id = req.web_id or f"web_{uuid.uuid4().hex[:12]}"

    existing = await db.get_session(settings.db_path, session_id)
    history: list[dict[str, Any]] = []
    visitor_name = req.visitor_name
    if existing:
        try:
            history = json.loads(existing.get("messages_json") or "[]")
        except json.JSONDecodeError:
            history = []
        web_id = existing.get("web_id") or web_id
        visitor_name = visitor_name or existing.get("visitor_name")

    try:
        result = await agent.handle(
            user_message=req.message.strip(),
            history=history,
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Agent failure: {exc}") from exc

    reply = result["reply"]
    hist = result["history"]
    media, estimate = _media_from_tool_trace(result.get("tool_trace") or [])

    await db.upsert_session(
        settings.db_path,
        session_id=session_id,
        web_id=web_id,
        visitor_name=visitor_name,
        messages_json=json.dumps(hist),
        metadata_json=json.dumps(
            {
                "web_id": web_id,
                "last_user_message_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                "estimate_present": bool(estimate),
            }
        ),
    )
    return ChatResponse(
        session_id=session_id,
        web_id=web_id,
        reply=reply,
        visitor_name=visitor_name,
        media=media,
        estimate=estimate,
    )


@app.post("/api/tools/solar_estimate")
async def tool_solar_estimate(payload: dict[str, Any]) -> JSONResponse:
    out = await solar_tool.solar_estimate(
        api_key=settings.maps_key,
        address=str(payload.get("address") or ""),
        monthly_bill_usd=payload.get("monthly_bill_usd"),
        monthly_usage_kwh=payload.get("monthly_usage_kwh"),
        usd_per_kwh=payload.get("usd_per_kwh"),
        target_offset_pct=payload.get("target_offset_pct"),
        property_ownership=payload.get("property_ownership"),
        hoa_restrictions=payload.get("hoa_restrictions"),
        roof_material=payload.get("roof_material"),
        roof_age_condition=payload.get("roof_age_condition"),
        shading_notes=payload.get("shading_notes"),
        service_panel_amps=payload.get("service_panel_amps"),
        large_loads=payload.get("large_loads"),
        battery_interest=payload.get("battery_interest"),
        financing_preference=payload.get("financing_preference"),
        timeline=payload.get("timeline"),
        future_usage_plans=payload.get("future_usage_plans"),
    )
    if out.get("ok"):
        analysis = await solar_analyst.analyze_estimate(settings, out)
        if analysis:
            out["analystSummary"] = analysis
            out["customerSummary"] = analysis
            out["agents"] = {
                "conversation": settings.llm_model,
                "solar_analyst": settings.solar_analyst_model,
                "mode": "dual-agent",
            }
        if out.get("layout") is not None:
            key = f"{out['lat']:.6f},{out['lng']:.6f},{out.get('recommendedPanels')}"
            _LAYOUT_CACHE[key] = out["layout"]
    # Lead-gen mode: no chat visuals / image URLs
    out["media"] = {}
    status = 200 if out.get("ok") else 400
    return JSONResponse(out, status_code=status)


@app.get("/api/solar/satellite")
async def solar_satellite(
    lat: float = Query(...),
    lng: float = Query(...),
    zoom: int = Query(20, ge=16, le=21),
) -> Response:
    cache_key = f"{lat:.6f},{lng:.6f}"
    if cache_key in _ROOF_PNG_CACHE:
        png = _ROOF_PNG_CACHE[cache_key]
    else:
        try:
            png = await imagery.build_plain_roof_png(settings.maps_key, lat, lng)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Roof image failed: {exc}") from exc
        _ROOF_PNG_CACHE[cache_key] = png
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/solar/layout.svg")
async def solar_layout_svg(
    lat: float = Query(...),
    lng: float = Query(...),
    panels: int = Query(..., ge=1, le=300),
) -> Response:
    key = f"{lat:.6f},{lng:.6f},{panels}"
    layout = _LAYOUT_CACHE.get(key)
    if layout is None:
        solar = await solar_tool.solar_building_insights(settings.maps_key, lat, lng)
        if not solar.get("ok"):
            raise HTTPException(status_code=404, detail="No solar layout available")
        layout = solar_tool._layout_model(solar["data"], panels)
        _LAYOUT_CACHE[key] = layout
    svg = solar_tool.render_layout_svg(layout, title="Suggested panel layout (roof faces)")
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/solar/overlay.png")
async def solar_overlay(
    lat: float = Query(...),
    lng: float = Query(...),
    panels: int = Query(..., ge=1, le=300),
    mode: str = Query("aerial", pattern="^(schematic|masked|aerial)$"),
) -> Response:
    """Roof layout overlay.

    mode=aerial     photo background + N/S frames + ridge + S-face panels (default)
    mode=masked     aerial on building mask only
    mode=schematic  tree-free technical
    """
    cache_key = f"{lat:.6f},{lng:.6f},{panels}:{mode}:v2"
    if cache_key in _OVERLAY_CACHE:
        return Response(
            content=_OVERLAY_CACHE[cache_key],
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    bi = solar_tool.get_cached_building_insights(lat, lng)
    if bi is None:
        solar = await solar_tool.solar_building_insights(settings.maps_key, lat, lng)
        if not solar.get("ok"):
            raise HTTPException(status_code=404, detail="No solar building insights for overlay")
        bi = solar["data"]

    try:
        png = await imagery.build_georef_overlay_png(
            api_key=settings.maps_key,
            lat=lat,
            lng=lng,
            building_insights=bi,
            recommended_panels=panels,
            mode=mode,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Georef overlay failed: {exc}") from exc

    _OVERLAY_CACHE[cache_key] = png
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/leads")
async def get_leads(limit: int = 25) -> dict[str, Any]:
    rows = await db.list_leads(settings.db_path, limit=min(limit, 200))
    return {"count": len(rows), "leads": rows}


@app.get("/api/leads/{lead_id}")
async def get_lead(lead_id: int) -> dict[str, Any]:
    row = await db.get_lead(settings.db_path, lead_id)
    if not row:
        raise HTTPException(status_code=404, detail="lead not found")
    return {"ok": True, "lead": row}


@app.post("/api/leads")
async def post_lead(payload: dict[str, Any]) -> JSONResponse:
    """Direct CRM create. On consent: DNC check + immediate voice webhook."""
    from app.tools import leads as leads_tool

    out = await leads_tool.create_lead_record(settings.db_path, payload)
    status = 200 if out.get("ok") else 400
    return JSONResponse(out, status_code=status)


@app.get("/api/ai-calls/pending")
async def ai_calls_pending(limit: int = 25) -> dict[str, Any]:
    rows = await db.list_pending_ai_calls(settings.db_path, limit=min(limit, 100))
    return {"count": len(rows), "calls": rows}


@app.post("/api/ai-calls/claim")
async def ai_calls_claim() -> JSONResponse:
    """Phone AI agent claims next callable lead (skips DNC)."""
    row = await db.claim_next_ai_call(settings.db_path)
    if not row:
        return JSONResponse({"ok": True, "call": None, "message": "queue empty"})
    # Attach phone opening + qual scripts for the voice agent
    from app.voice_dispatch import PHONE_OPENING_SCRIPT, PHONE_QUAL_SCRIPT

    name = (row.get("name") or "there").split()[0]
    opening = PHONE_OPENING_SCRIPT.format(
        name=name,
        company=settings.company_name,
        tagline=settings.company_tagline,
        address_or_home=row.get("lead_address") or row.get("address") or "your home",
    )
    return JSONResponse(
        {
            "ok": True,
            "call": row,
            "scripts": {
                "opening": opening,
                "qualification": PHONE_QUAL_SCRIPT,
                "rules": [
                    "Identify SOL-RIGHT Solar and that you are an AI at call start",
                    "Offer opt-out; end call and log DNC if requested",
                    "Respect National + Tennessee DNC (already pre-checked)",
                    "Qualify with BANT/MEDDIC-style questions; score lead; book meeting or transfer",
                    "POST results to /api/voice/writeback",
                ],
            },
        }
    )


@app.post("/api/ai-calls/{queue_id}/complete")
async def ai_calls_complete(queue_id: int, payload: dict[str, Any] | None = None) -> JSONResponse:
    payload = payload or {}
    ok = await db.complete_ai_call(
        settings.db_path,
        queue_id,
        status=str(payload.get("status") or "completed"),
        result_notes=payload.get("result_notes"),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="queue item not found")
    return JSONResponse({"ok": True, "queue_id": queue_id})


@app.post("/api/voice/writeback")
async def voice_writeback(payload: dict[str, Any]) -> JSONResponse:
    """Phone AI agent writes qualification score, meeting, transfer, notes to CRM."""
    from app.tools import leads as leads_tool

    out = await leads_tool.phone_writeback(settings.db_path, payload)
    status = 200 if out.get("ok") else 400
    return JSONResponse(out, status_code=status)


@app.post("/api/voice/opt-out")
async def voice_opt_out(payload: dict[str, Any]) -> JSONResponse:
    """Add number to company DNC (National/TN respect + internal opt-out)."""
    from app import dnc as dnc_mod

    phone = str(payload.get("phone") or "")
    out = await dnc_mod.add_opt_out(
        settings.db_path,
        phone,
        reason=str(payload.get("reason") or "voice_opt_out"),
        list_name=str(payload.get("list_name") or "company_internal_dnc"),
    )
    lead_id = payload.get("lead_id")
    if out.get("ok") and lead_id:
        await db.update_lead(
            settings.db_path,
            int(lead_id),
            {
                "dnc_status": "listed",
                "dnc_lists": __import__("json").dumps(["company_internal_dnc"]),
                "ai_call_status": "opted_out",
            },
        )
    status = 200 if out.get("ok") else 400
    return JSONResponse(out, status_code=status)


@app.post("/api/voice/retell-webhook")
async def retell_webhook(payload: dict[str, Any]) -> JSONResponse:
    """Retell → SOL-RIGHT: call_started / call_ended / call_analyzed."""
    from app import voice_dispatch

    out = await voice_dispatch.handle_retell_webhook(settings, payload)
    return JSONResponse(out)


@app.post("/api/voice/dispatch/{lead_id}")
async def voice_dispatch_now(lead_id: int) -> JSONResponse:
    """Manual re-dispatch of outbound call for a consented lead (Retell test helper)."""
    from app import dnc as dnc_mod
    from app import voice_dispatch

    lead = await db.get_lead(settings.db_path, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="lead not found")
    if not lead.get("ai_call_consent"):
        raise HTTPException(status_code=400, detail="lead missing AI call consent")
    dnc = await dnc_mod.check_dnc(settings.db_path, str(lead.get("phone") or ""))
    # ensure queue row
    pending = await db.list_pending_ai_calls(settings.db_path, limit=50)
    qid = None
    for c in pending:
        if int(c.get("lead_id") or 0) == lead_id:
            qid = int(c["id"])
            break
    if qid is None:
        qid = await db.enqueue_ai_call(
            settings.db_path,
            lead_id=lead_id,
            phone=str(lead.get("phone")),
            name=str(lead.get("name") or "Homeowner"),
            address=lead.get("address"),
            context_json=None,
            priority=60,
            call_window="immediate",
        )
    out = await voice_dispatch.dispatch_outbound_call(
        settings, lead_id=lead_id, queue_id=qid, dnc=dnc
    )
    return JSONResponse(out)


@app.post("/api/dnc/check")
async def dnc_check(payload: dict[str, Any]) -> JSONResponse:
    from app import dnc as dnc_mod

    out = await dnc_mod.check_dnc(settings.db_path, str(payload.get("phone") or ""))
    return JSONResponse(out)


@app.get("/api/calendar/appointments")
async def calendar_list(days: int = 60) -> dict[str, Any]:
    rows = await db.list_appointments(settings.db_path, days=min(days, 180))
    return {
        "count": len(rows),
        "owner_email": settings.owner_email,
        "timezone": settings.calendar_timezone,
        "feed_url": f"{settings.public_base_url.rstrip('/')}/api/calendar/feed.ics",
        "appointments": rows,
    }


@app.post("/api/calendar/appointments")
async def calendar_create(payload: dict[str, Any]) -> JSONResponse:
    """Manual appointment create (admin / testing)."""
    from app import calendar_service

    lead = None
    lead_id = payload.get("lead_id")
    if lead_id:
        lead = await db.get_lead(settings.db_path, int(lead_id))
    if not lead:
        lead = {
            "id": lead_id,
            "name": payload.get("customer_name") or payload.get("name") or "Homeowner",
            "phone": payload.get("customer_phone") or payload.get("phone"),
            "address": payload.get("customer_address") or payload.get("address"),
            "email": payload.get("customer_email") or payload.get("email"),
            "intent": payload.get("intent"),
            "monthly_bill_usd": payload.get("monthly_bill_usd"),
            "recommended_panels": payload.get("recommended_panels"),
            "estimated_monthly_savings_usd": payload.get("estimated_monthly_savings_usd"),
        }
    wb = {
        "meeting_slot": payload.get("meeting_slot") or payload.get("starts_at"),
        "phone_call_notes": payload.get("notes") or payload.get("description"),
        "call_status": "meeting_booked",
        "lead_score": payload.get("lead_score"),
        "source": payload.get("source") or "manual",
        "call_id": payload.get("retell_call_id"),
        "qual_budget": payload.get("qual_budget"),
        "qual_authority": payload.get("qual_authority"),
        "qual_need": payload.get("qual_need"),
        "qual_timeline": payload.get("qual_timeline"),
    }
    appt = await calendar_service.create_appointment_from_writeback(
        settings.db_path,
        lead=lead,
        payload=wb,
        owner_email=settings.owner_email,
        duration_minutes=int(
            payload.get("duration_minutes") or settings.calendar_default_duration_minutes or 30
        ),
    )
    if not appt:
        return JSONResponse({"ok": False, "error": "could not parse meeting time"}, status_code=400)
    return JSONResponse({"ok": True, "appointment": appt})


@app.get("/api/calendar/appointments/{appt_id}.ics")
async def calendar_appointment_ics(appt_id: int) -> Response:
    from app import calendar_service

    appt = await db.get_appointment(settings.db_path, appt_id)
    if not appt:
        raise HTTPException(status_code=404, detail="appointment not found")
    ics = calendar_service.build_ics_calendar([appt])
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="sol-right-{appt_id}.ics"',
            "Cache-Control": "no-cache",
        },
    )


@app.get("/api/calendar/feed.ics")
async def calendar_feed() -> Response:
    """Subscribe this URL in Outlook (live.com) or Google Calendar."""
    from app import calendar_service

    rows = await db.list_all_appointments_for_feed(settings.db_path)
    ics = calendar_service.build_ics_calendar(rows, cal_name="SOL-RIGHT Appointments")
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'inline; filename="sol-right-appointments.ics"',
            "Cache-Control": "no-cache, max-age=60",
        },
    )


@app.post("/api/calendar/appointments/{appt_id}/cancel")
async def calendar_cancel(appt_id: int) -> JSONResponse:
    ok = await db.cancel_appointment(settings.db_path, appt_id)
    if not ok:
        raise HTTPException(status_code=404, detail="appointment not found")
    return JSONResponse({"ok": True, "id": appt_id, "status": "cancelled"})