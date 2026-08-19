from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.tools import leads as leads_tool
from app.tools import solar as solar_tool

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "retrieve_knowledge",
            "description": (
                "Search the SOL-RIGHT FAQ / knowledge vector database for answers about "
                "services, process, warranties, financing, net metering, batteries, roof suitability, "
                "and common solar homeowner questions. Use for FAQ-style questions BEFORE guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language question or search query",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of chunks to return (default 4)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "solar_estimate",
            "description": (
                "Run a US residential solar potential estimate using Google Solar roof/sun data at the address, "
                "then size the system from the homeowner's usage inputs. "
                "REQUIRED: address + (monthly_bill_usd OR monthly_usage_kwh). "
                "STRONGLY PREFERRED for accuracy: monthly_usage_kwh and/or usd_per_kwh, target_offset_pct, "
                "and site details (ownership, HOA, roof, shading, panel amps, loads, battery, financing). "
                "Google Solar API itself only uses lat/lng from the address; other fields improve sizing + CRM."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Full US street address including city, state, ZIP",
                    },
                    "monthly_bill_usd": {
                        "type": "number",
                        "description": "Average monthly electricity bill in USD (if kWh unknown)",
                    },
                    "monthly_usage_kwh": {
                        "type": "number",
                        "description": "Average monthly usage in kWh from utility bill (best accuracy)",
                    },
                    "usd_per_kwh": {
                        "type": "number",
                        "description": "Customer's electric rate $/kWh if known (e.g. 0.12)",
                    },
                    "target_offset_pct": {
                        "type": "number",
                        "description": "Desired bill/usage offset percent, e.g. 80, 90, 100 (default 90)",
                    },
                    "property_ownership": {
                        "type": "string",
                        "description": "own / lease / other",
                    },
                    "hoa_restrictions": {
                        "type": "string",
                        "description": "HOA or local restriction notes, or 'none' / 'unknown'",
                    },
                    "roof_material": {
                        "type": "string",
                        "description": "asphalt shingle, metal, tile, flat membrane, etc.",
                    },
                    "roof_age_condition": {
                        "type": "string",
                        "description": "Approx roof age/condition; replacement planned soon?",
                    },
                    "shading_notes": {
                        "type": "string",
                        "description": "Known shading: trees, chimneys, neighbors, seasonal",
                    },
                    "service_panel_amps": {
                        "type": "string",
                        "description": "Main panel size if known, e.g. 100A / 200A / unknown",
                    },
                    "large_loads": {
                        "type": "string",
                        "description": "EV charger, heat pump, pool, generator, etc.",
                    },
                    "battery_interest": {
                        "type": "string",
                        "description": "yes / no / maybe / backup interest notes",
                    },
                    "financing_preference": {
                        "type": "string",
                        "description": "cash / loan / lease-PPA / unsure",
                    },
                    "timeline": {
                        "type": "string",
                        "description": "Desired timeline, e.g. ASAP, 3-6 months, researching",
                    },
                    "future_usage_plans": {
                        "type": "string",
                        "description": "EV, electrification, additions that increase usage",
                    },
                },
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_lead",
            "description": (
                "Store CRM lead with full chat transcript. Requires name, phone, intent, and "
                "when starting an AI call: explicit consent. On consent, system checks DNC "
                "(National + Tennessee stores) and immediately fires the voice-platform webhook."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Visitor full name (required)"},
                    "phone": {"type": "string", "description": "Best callback phone (required)"},
                    "email": {"type": "string"},
                    "address": {"type": "string"},
                    "intent": {
                        "type": "string",
                        "description": "Primary intent, e.g. lower_electric_bill, install_quote, backup_power, researching",
                    },
                    "monthly_bill_usd": {"type": "number"},
                    "monthly_usage_kwh": {"type": "number"},
                    "usd_per_kwh": {"type": "number"},
                    "target_offset_pct": {"type": "number"},
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "estimated_annual_kwh": {"type": "number"},
                    "estimated_monthly_kwh": {"type": "number"},
                    "max_panels": {"type": "integer"},
                    "recommended_panels": {"type": "integer"},
                    "system_size_kw": {"type": "number"},
                    "quote_confidence": {"type": "integer"},
                    "estimated_monthly_savings_usd": {"type": "number"},
                    "estimated_yearly_savings_usd": {"type": "number"},
                    "estimated_10yr_savings_usd": {"type": "number"},
                    "property_ownership": {"type": "string"},
                    "hoa_restrictions": {"type": "string"},
                    "roof_material": {"type": "string"},
                    "roof_age_condition": {"type": "string"},
                    "shading_notes": {"type": "string"},
                    "service_panel_amps": {"type": "string"},
                    "large_loads": {"type": "string"},
                    "battery_interest": {"type": "string"},
                    "financing_preference": {"type": "string"},
                    "timeline": {"type": "string"},
                    "future_usage_plans": {"type": "string"},
                    "notes": {"type": "string"},
                    "consent_text": {
                        "type": "string",
                        "description": "Canonical consent prompt shown to user",
                    },
                    "consent_verbatim": {
                        "type": "string",
                        "description": "User's exact yes wording",
                    },
                    "ai_call_consent": {
                        "type": "boolean",
                        "description": "True only after explicit yes to AI call consent language",
                    },
                    "queue_ai_call": {
                        "type": "boolean",
                        "description": "True to queue + immediately dispatch outbound AI voice call",
                    },
                    "ai_call_window": {
                        "type": "string",
                        "description": "Default 'immediate' for speed-to-lead",
                    },
                },
                "required": ["name", "phone", "intent"],
            },
        },
    },
]


def build_system_prompt(settings: Settings, kb_text: str) -> str:
    return f"""You are Dave, the website lead-generation AI agent for {settings.company_name}.

Identity:
- Name: Dave
- Personality: kind, helpful, super professional, and genuinely curious
- You gather site-specific data like a good solar consultant — never interrogate all at once

Company: {settings.company_name} — {settings.company_tagline}
Service area: {settings.service_area}
Phone: {settings.contact_phone}
Email: {settings.contact_email}

## Core goals (Build 2 — LEAD GENERATION + VOICE)
1) Answer FAQs with retrieve_knowledge when needed (short answers).
2) Collect: full name, US address, monthly bill (and/or kWh), and primary intent.
3) Call solar_estimate when address + usage known.
4) Present production + how energy lowers the bill + TOTAL $ savings.
5) After savings totals, close in this order:
   a) "Do you have any more questions about this savings estimate?"
   b) Confirm full name if missing; ask best phone number.
   c) Capture intent if not clear (lower bill / install quote / backup / researching).
   d) Ask EXPLICIT consent using this wording (or extremely close):
      "Yes, you can call me with an AI agent to discuss my solar savings estimate and an estimated installation cost."
      Ask them to reply Yes / I agree if they approve.
6) On explicit Yes: immediately call create_lead with:
   name, phone, intent, estimate/savings fields,
   consent_text (canonical), consent_verbatim (their words),
   ai_call_consent=true, queue_ai_call=true, ai_call_window="immediate".
   The backend logs the full chat transcript, checks DNC (National + Tennessee stores),
   and fires the voice platform webhook for an immediate outbound AI call (speed-to-lead).
7) If they give phone but decline AI call: create_lead with ai_call_consent=false (CRM only, no call).
8) If they are on DNC or refuse: never push a call; thank them and stop outbound.
9) If solar_estimate fails: still collect name/phone/intent/consent path for human/AI follow-up.

You are a lead-gen agent. Be warm and professional, but purposeful — move toward a callback.

## Quote discovery flow (IMPORTANT)
When the user wants a quote, collect information in short waves (1–3 questions at a time):

Wave A — minimum to run Google Solar + first model:
1) Exact US service address
2) Average monthly electric bill ($) AND/OR average monthly kWh (kWh is better if they have it)
Optional if easy: $/kWh rate from the bill

You MAY call solar_estimate after Wave A so they see roof imagery quickly.

Wave B — accuracy upgrades (ask after first estimate OR before if user is patient):
3) Desired offset % (e.g. 80 / 90 / 100)
4) Own or lease the property?
5) HOA / local restrictions?
6) Roof material + approx age/condition (replacement planned soon?)
7) Known shading (trees, chimneys, neighbors, seasonal)
8) Main panel size if known (100A/200A/unknown) + large loads (EV, heat pump, pool, generator)
9) Battery/backup interest
10) Financing preference (cash/loan/lease-PPA) + timeline + future usage plans (EV, electrification)

After Wave B answers, call solar_estimate AGAIN with all known fields so sizing/confidence updates.
Then present the improved quote.

## Tool rules
- For FAQ / “how does solar work” / warranties / financing questions: call retrieve_knowledge first.
- USA addresses only for solar_estimate.
- solar_estimate requires address + (monthly_bill_usd OR monthly_usage_kwh) that the USER actually provided.
- NEVER invent, guess, or assume bill dollars, kWh, or $/kWh. If missing, ask — do not call the tool yet.
- If the user only gave an address, ask for bill and/or kWh before calling solar_estimate.
- Pass every known optional field into solar_estimate — do not drop details the user stated.
- Google Solar only uses lat/lng from the address for roof/sun/layout. Explain that honestly.
- Bill/kWh/rate/offset change which system size we pick from Google's panel configs.
- Site details (roof, HOA, panel amps, battery, financing) improve confidence + specialist follow-up notes.
- create_lead requires name + phone + intent. Include estimate + savings fields.
- On AI call approval: ai_call_consent=true, queue_ai_call=true, ai_call_window="immediate",
  consent_text + consent_verbatim required.
- Backend attaches full chat transcript automatically when available.

## Presentation rules (LEAD GEN — no visuals)
- Do NOT discuss or promise roof photos, overlays, diagrams, or images. Text-only pitch.
- Lead with recommendedPanels + system kW + production kWh.
- Always include BILL IMPACT + TOTAL savings ($/mo, new bill ballpark, yearly, 10-year).
- Savings are planning estimates only (not guarantees).
- maxPanels is capacity only.
- After TOTAL savings, close:
  1) Any more questions?
  2) Full name + best phone
  3) Explicit AI-call consent with the canonical sentence above
- On Yes → create_lead immediately (backend starts the AI outbound call right away).
- After success: "Thanks — our AI representative will call you shortly from SOL-RIGHT Solar. They will identify as AI and you can opt out anytime."
- Never invent numbers. No personality adjectives in UI titles.
- Compliance: do not call without explicit consent; respect DNC; never pressure.

Opening if user greets only:
Start with: "Hello, Welcome to Sol-Right, how can I help you today?" then briefly offer a free savings estimate (address + bill).

KNOWLEDGE BASE (company facts; still use retrieve_knowledge for FAQs):
{kb_text}
"""


class LeadGenAgent:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.kb_text = Path(settings.kb_path).read_text(encoding="utf-8")
        self.system_prompt = build_system_prompt(settings, self.kb_text)

    async def _chat_completion(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.35,
        }
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_s) as client:
            r = await client.post(
                f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=body,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"LLM error HTTP {r.status_code}: {r.text[:500]}")
            return r.json()

    async def _run_tool(self, name: str, args: dict[str, Any], session_id: str) -> dict[str, Any]:
        if name == "retrieve_knowledge":
            from app import vector_store

            n = args.get("n_results") or 4
            try:
                n = int(n)
            except (TypeError, ValueError):
                n = 4
            return vector_store.retrieve(str(args.get("query") or ""), n_results=n)
        if name == "solar_estimate":
            from app import solar_analyst

            result = await solar_tool.solar_estimate(
                api_key=self.settings.maps_key,
                address=args.get("address", ""),
                monthly_bill_usd=args.get("monthly_bill_usd"),
                monthly_usage_kwh=args.get("monthly_usage_kwh"),
                usd_per_kwh=args.get("usd_per_kwh"),
                target_offset_pct=args.get("target_offset_pct"),
                property_ownership=args.get("property_ownership"),
                hoa_restrictions=args.get("hoa_restrictions"),
                roof_material=args.get("roof_material"),
                roof_age_condition=args.get("roof_age_condition"),
                shading_notes=args.get("shading_notes"),
                service_panel_amps=args.get("service_panel_amps"),
                large_loads=args.get("large_loads"),
                battery_interest=args.get("battery_interest"),
                financing_preference=args.get("financing_preference"),
                timeline=args.get("timeline"),
                future_usage_plans=args.get("future_usage_plans"),
            )
            if result.get("ok"):
                # Parallel-ready: analyst call (Dave continues after both complete)
                analysis = await solar_analyst.analyze_estimate(self.settings, result)
                if analysis:
                    result["analystSummary"] = analysis
                    result["customerSummary"] = analysis
                    result["agents"] = {
                        "conversation": self.settings.llm_model,
                        "solar_analyst": getattr(self.settings, "solar_analyst_model", self.settings.llm_model),
                        "mode": "parallel_tool_then_analyst",
                    }
            return result
        if name == "create_lead":
            payload = dict(args)
            payload["chat_session_id"] = session_id
            # Full transcript + metadata for CRM / voice agent
            payload["chat_transcript"] = [
                {"role": m.get("role"), "content": m.get("content")}
                for m in (getattr(self, "_current_history", None) or [])
                if m.get("role") in ("user", "assistant")
            ]
            # Include the turn that triggered lead create
            payload.setdefault("chat_metadata", {})
            if isinstance(payload["chat_metadata"], dict):
                payload["chat_metadata"]["session_id"] = session_id
            mapping = {
                "yearlyEnergyDcKwh": "estimated_annual_kwh",
                "monthlyEnergyKwh": "estimated_monthly_kwh",
                "maxPanels": "max_panels",
                "recommendedPanels": "recommended_panels",
                "systemSizeKw": "system_size_kw",
                "quoteConfidence": "quote_confidence",
                "propertyOwnership": "property_ownership",
                "hoaRestrictions": "hoa_restrictions",
                "roofMaterial": "roof_material",
                "roofAgeCondition": "roof_age_condition",
                "shadingNotes": "shading_notes",
                "servicePanelAmps": "service_panel_amps",
                "largeLoads": "large_loads",
                "batteryInterest": "battery_interest",
                "financingPreference": "financing_preference",
                "futureUsagePlans": "future_usage_plans",
                "estimatedMonthlySavingsUsd": "estimated_monthly_savings_usd",
                "estimatedYearlySavingsUsd": "estimated_yearly_savings_usd",
                "estimated10YearSavingsUsd": "estimated_10yr_savings_usd",
                "aiCallConsent": "ai_call_consent",
                "queueAiCall": "queue_ai_call",
                "aiCallWindow": "ai_call_window",
                "consentText": "consent_text",
                "consentVerbatim": "consent_verbatim",
            }
            for src, dst in mapping.items():
                if src in payload and dst not in payload:
                    payload[dst] = payload.get(src)
            return await leads_tool.create_lead_record(self.settings.db_path, payload)
        return {"ok": False, "error": f"Unknown tool: {name}"}

    async def handle(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        session_id = session_id or str(uuid.uuid4())
        history = list(history or [])
        # Transcript available to create_lead (includes prior turns + this user message)
        self._current_history = [
            *history,
            {"role": "user", "content": user_message},
        ]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *history,
            {"role": "user", "content": user_message},
        ]

        tool_trace: list[dict[str, Any]] = []
        final_text = ""
        max_iters = 8

        for _ in range(max_iters):
            data = await self._chat_completion(messages)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            tool_calls = msg.get("tool_calls") or []

            if tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except json.JSONDecodeError:
                        args = {}
                    result = await self._run_tool(name, args, session_id)
                    tool_trace.append({"tool": name, "args": args, "result": result})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id") or name,
                            "content": json.dumps(result),
                        }
                    )
                continue

            final_text = (msg.get("content") or "").strip()
            break

        if not final_text:
            final_text = (
                "Sorry — I hit a snag generating a reply. "
                "Please share your name and phone and a specialist will follow up."
            )

        new_history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": final_text},
        ]
        if len(new_history) > 50:
            new_history = new_history[-50:]

        return {
            "session_id": session_id,
            "reply": final_text,
            "history": new_history,
            "tool_trace": tool_trace,
        }
