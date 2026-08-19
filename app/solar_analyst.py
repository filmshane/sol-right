"""Solar Analyst — second parallel AI agent.

Lead-gen focused: turn estimate data into a savings story that drives a callback.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings


ANALYST_SYSTEM = """You are the SOL-RIGHT Solar Analyst for a LEAD GENERATION agent.

Write a tight homeowner-facing sales summary from the JSON. Goal: make them want a
callback for an install consult.

Structure (plain text, short bullets):
1) Opening hook — what we can do for THIS home (address + panel count + kW).
2) Production — yearly/monthly kWh in plain English.
3) HOW ENERGY LOWERS THE ELECTRIC BILL — explain simply:
   solar power used first → buy fewer kWh from the utility → lower energy portion of bill.
4) MONEY wrap-up (REQUIRED — use the savings fields; do not invent other $ amounts):
   - estimated monthly savings
   - estimated new monthly bill ballpark
   - % bill reduction
   - yearly / 10-year / 20-year savings ballparks
5) One-line confidence/assumption note (planning estimate, not a guarantee).
6) Hard close sequence after savings totals:
   - Ask if they have any more questions
   - Ask for best phone number
   - Ask approval for an AI representative to call in 2–5 minutes about more savings + estimated install cost
   Remind the chat agent to create_lead with ai_call_consent + queue_ai_call when approved.

Rules:
- Lead with savings and bill impact, not roof geometry.
- Use recommendedPanels (not maxPanels) as the plan.
- Do not invent numbers missing from JSON.
- No image/media talk. No personality labels.
- Professional, confident, local-installer tone — helpful but clearly lead-gen.
- Under 220 words.
"""


async def analyze_estimate(settings: Settings, estimate: dict[str, Any]) -> str | None:
    if not estimate.get("ok"):
        return None

    payload = {
        "address": estimate.get("address"),
        "recommendedPanels": estimate.get("recommendedPanels"),
        "maxPanels": estimate.get("maxPanels"),
        "systemSizeKw": estimate.get("systemSizeKw"),
        "yearlyEnergyDcKwh": estimate.get("yearlyEnergyDcKwh"),
        "monthlyEnergyKwh": estimate.get("monthlyEnergyKwh"),
        "estimatedMonthlyUsageKwh": estimate.get("estimatedMonthlyUsageKwh"),
        "usageSource": estimate.get("usageSource"),
        "assumedUsdPerKwh": estimate.get("assumedUsdPerKwh"),
        "monthlyBillUsd": estimate.get("monthlyBillUsd"),
        "targetOffsetPct": estimate.get("targetOffsetPct"),
        "quoteConfidence": estimate.get("quoteConfidence"),
        "estimatedMonthlySavingsUsd": estimate.get("estimatedMonthlySavingsUsd"),
        "estimatedYearlySavingsUsd": estimate.get("estimatedYearlySavingsUsd"),
        "estimated10YearSavingsUsd": estimate.get("estimated10YearSavingsUsd"),
        "estimated20YearSavingsUsd": estimate.get("estimated20YearSavingsUsd"),
        "estimatedNewMonthlyBillUsd": estimate.get("estimatedNewMonthlyBillUsd"),
        "estimatedBillReductionPct": estimate.get("estimatedBillReductionPct"),
        "savingsAssumptions": estimate.get("savingsAssumptions"),
        "sizingMethod": estimate.get("sizingMethod"),
        "roofSegments": [
            {
                "direction": s.get("direction"),
                "recommendedPanelsOnSegment": s.get("recommendedPanelsOnSegment"),
                "quality": s.get("quality"),
            }
            for s in (estimate.get("roofSegments") or [])
            if (s.get("recommendedPanelsOnSegment") or 0) > 0
        ][:4],
    }

    model = getattr(settings, "solar_analyst_model", None) or settings.llm_model
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": ANALYST_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Write the homeowner LEAD-GEN savings summary from this package.\n\n"
                    + json.dumps(payload, indent=2)
                ),
            },
        ],
        "temperature": 0.35,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
            r = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=body,
            )
            if r.status_code >= 400:
                return None
            data = r.json()
            return (data.get("choices") or [{}])[0].get("message", {}).get("content") or None
    except Exception:
        return None
