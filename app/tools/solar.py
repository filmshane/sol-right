from __future__ import annotations

import math
import re
from typing import Any

import httpx

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
SOLAR_URL = "https://solar.googleapis.com/v1/buildingInsights:findClosest"
DATA_LAYERS_URL = "https://solar.googleapis.com/v1/dataLayers:get"

# Server-side cache of raw buildingInsights for georeferenced overlays
_BI_CACHE: dict[str, dict[str, Any]] = {}


def bi_cache_key(lat: float, lng: float) -> str:
    return f"{float(lat):.6f},{float(lng):.6f}"


def get_cached_building_insights(lat: float, lng: float) -> dict[str, Any] | None:
    return _BI_CACHE.get(bi_cache_key(lat, lng))


def cache_building_insights(lat: float, lng: float, payload: dict[str, Any]) -> None:
    _BI_CACHE[bi_cache_key(lat, lng)] = payload

# Typical TN residential blended rate used only to size systems from a dollar bill.
# This is an estimate input, not a quote of the customer's actual utility rate.
DEFAULT_USD_PER_KWH = 0.135
TARGET_OFFSET_FRACTION = 0.90  # size system to ~90% of estimated annual usage
MAX_REASONABLE_RESIDENTIAL_PANELS = 60  # soft educational cap; roof max still reported

US_HINT = re.compile(
    r"\b(USA|U\.S\.A\.|United States|AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY|DC)\b",
    re.I,
)


def looks_like_us_address(address: str) -> bool:
    text = (address or "").strip()
    if len(text) < 8:
        return False
    if US_HINT.search(text):
        return True
    if re.search(r"\b\d{5}(-\d{4})?\b", text):
        return True
    return False


def _compass(azimuth: float | None) -> str:
    if azimuth is None:
        return "unknown"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int(((float(azimuth) % 360) / 45.0) + 0.5) % 8]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2:
        return float(s[mid])
    return float((s[mid - 1] + s[mid]) / 2.0)


async def geocode_address(api_key: str, address: str) -> dict[str, Any]:
    if not api_key:
        return {
            "ok": False,
            "error": "GOOGLE_MAPS_API_KEY not configured",
            "error_code": "NO_API_KEY",
        }
    params = {"address": address, "key": api_key, "region": "us"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(GEOCODE_URL, params=params)
        data = r.json()
    status = data.get("status")
    if status != "OK" or not data.get("results"):
        return {
            "ok": False,
            "error": data.get("error_message") or status or "geocode_failed",
            "error_code": status or "GEOCODE_FAILED",
            "raw_status": status,
        }
    result = data["results"][0]
    loc = result["geometry"]["location"]
    components = result.get("address_components") or []
    country = None
    for c in components:
        if "country" in c.get("types", []):
            country = c.get("short_name")
            break
    if country and country != "US":
        return {
            "ok": False,
            "error": "Address is outside the United States",
            "error_code": "NON_US",
            "formatted_address": result.get("formatted_address"),
            "country": country,
        }
    return {
        "ok": True,
        "formatted_address": result.get("formatted_address"),
        "lat": loc["lat"],
        "lng": loc["lng"],
        "place_id": result.get("place_id"),
    }


def _estimate_usage_kwh(
    monthly_bill_usd: float | None = None,
    usd_per_kwh: float | None = None,
    monthly_usage_kwh: float | None = None,
    target_offset_pct: float | None = None,
) -> dict[str, Any]:
    """Build usage + target production from the best available homeowner inputs."""
    rate = float(usd_per_kwh) if usd_per_kwh and usd_per_kwh > 0 else DEFAULT_USD_PER_KWH
    rate_source = "customer_rate" if usd_per_kwh and usd_per_kwh > 0 else "default_planning_rate"

    if monthly_usage_kwh is not None and float(monthly_usage_kwh) > 0:
        monthly = float(monthly_usage_kwh)
        usage_source = "customer_kwh"
    elif monthly_bill_usd is not None and float(monthly_bill_usd) > 0:
        monthly = float(monthly_bill_usd) / max(rate, 0.05)
        usage_source = "bill_divided_by_rate"
    else:
        monthly = 0.0
        usage_source = "unknown"

    offset = TARGET_OFFSET_FRACTION
    if target_offset_pct is not None:
        try:
            offset = max(0.3, min(1.2, float(target_offset_pct) / 100.0))
        except (TypeError, ValueError):
            offset = TARGET_OFFSET_FRACTION

    return {
        "assumedUsdPerKwh": rate,
        "rateSource": rate_source,
        "usageSource": usage_source,
        "estimatedMonthlyUsageKwh": round(monthly, 1),
        "estimatedAnnualUsageKwh": round(monthly * 12.0, 1),
        "targetOffsetFraction": offset,
        "targetAnnualProductionKwh": round(monthly * 12.0 * offset, 1),
    }


def _pick_panel_config(
    solar_potential: dict[str, Any],
    monthly_bill_usd: float | None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pick a usage-sized config. Prefer bill-matched financialAnalyses (Build-2 brief)."""
    from app.roof_geometry import financial_pick_config, south_segment_indices

    configs = list(solar_potential.get("solarPanelConfigs") or [])
    if not configs:
        return {"config": {}, "method": "none", "usage": usage or {}}

    usage = usage or _estimate_usage_kwh(monthly_bill_usd)
    target = float(usage.get("targetAnnualProductionKwh") or 0)
    bill = float(monthly_bill_usd or 0)

    # Cap: cannot exceed south-face panel inventory
    south_idxs = south_segment_indices(solar_potential)
    south_capacity = sum(
        1
        for p in (solar_potential.get("solarPanels") or [])
        if int(p.get("segmentIndex") if p.get("segmentIndex") is not None else -1) in south_idxs
    )
    if south_capacity <= 0:
        south_capacity = int(solar_potential.get("maxArrayPanelsCount") or 999)

    scored: list[tuple[float, dict[str, Any]]] = []
    for cfg in configs:
        yearly = float(cfg.get("yearlyEnergyDcKwh") or 0)
        panels = int(cfg.get("panelsCount") or 0)
        if panels <= 0 or yearly <= 0:
            continue
        if panels > south_capacity:
            continue
        size_penalty = 0.0
        if panels > MAX_REASONABLE_RESIDENTIAL_PANELS:
            size_penalty = (panels - MAX_REASONABLE_RESIDENTIAL_PANELS) * 80.0
        overshoot = max(0.0, yearly - target * 1.05) * 1.5
        undershoot = max(0.0, target - yearly) * 1.0
        score = abs(yearly - target) + overshoot + undershoot + size_penalty
        scored.append((score, cfg))

    if not scored:
        # fall back without south cap
        for cfg in configs:
            yearly = float(cfg.get("yearlyEnergyDcKwh") or 0)
            panels = int(cfg.get("panelsCount") or 0)
            if panels > 0 and yearly > 0:
                scored.append((abs(yearly - target) if target else -yearly, cfg))
    if not scored:
        return {"config": configs[0], "method": "first_config", "usage": usage}

    scored.sort(key=lambda x: x[0])
    best = scored[0][1]
    method = "usage_target"

    # Build-2 brief: pickClosestAnalysis by monthly bill units
    fa_cfg = financial_pick_config(solar_potential, bill if bill > 0 else None)
    if fa_cfg is not None and bill > 0:
        fa_panels = int(fa_cfg.get("panelsCount") or 0)
        fa_yearly = float(fa_cfg.get("yearlyEnergyDcKwh") or 0)
        if 0 < fa_panels <= south_capacity:
            if target <= 0 or abs(fa_yearly - target) / max(target, 1) <= 0.35:
                best = fa_cfg
                method = "financial_analysis_bill_match"

    # Clamp panels count note if still over south capacity (config energy from Google may include N)
    # We still return config but layout will place only S-face panels up to count
    return {
        "config": best,
        "method": method,
        "usage": usage,
        "southFaceCapacity": south_capacity,
    }


def _segment_summaries(solar_potential: dict[str, Any], recommended_panels: int) -> list[dict[str, Any]]:
    from app.roof_geometry import choose_south_panels, compass

    segs = solar_potential.get("roofSegmentStats") or []
    chosen = choose_south_panels(solar_potential, recommended_panels)
    counts: dict[int, int] = {}
    energy: dict[int, float] = {}
    for p in chosen:
        idx = p.get("segmentIndex")
        if idx is None:
            continue
        idx = int(idx)
        counts[idx] = counts.get(idx, 0) + 1
        energy[idx] = energy.get(idx, 0.0) + float(p.get("yearlyEnergyDcKwh") or 0)

    out: list[dict[str, Any]] = []
    for i, seg in enumerate(segs):
        stats = seg.get("stats") or {}
        quantiles = [float(x) for x in (stats.get("sunshineQuantiles") or [])]
        sun_med = _median(quantiles)
        sun_max = max(quantiles) if quantiles else None
        az = seg.get("azimuthDegrees")
        pitch = seg.get("pitchDegrees")
        area = stats.get("areaMeters2")
        out.append(
            {
                "segmentIndex": i,
                "pitchDegrees": round(float(pitch), 1) if pitch is not None else None,
                "azimuthDegrees": round(float(az), 1) if az is not None else None,
                "direction": compass(az),
                "areaMeters2": round(float(area), 1) if area is not None else None,
                "sunshineMedianHours": round(sun_med, 1) if sun_med is not None else None,
                "sunshinePeakHours": round(sun_max, 1) if sun_max is not None else None,
                "recommendedPanelsOnSegment": counts.get(i, 0),
                "recommendedSegmentYearlyKwh": round(energy.get(i, 0.0), 1),
                "quality": (
                    "excellent"
                    if (sun_med or 0) >= 1300
                    else "good"
                    if (sun_med or 0) >= 1150
                    else "fair"
                    if (sun_med or 0) >= 1000
                    else "poor"
                ),
            }
        )
    out.sort(key=lambda s: (-(s.get("recommendedPanelsOnSegment") or 0), -(s.get("sunshineMedianHours") or 0)))
    return out


def _layout_model(
    payload: dict[str, Any],
    recommended_panels: int,
) -> dict[str, Any]:
    """Project chosen SOUTH-FACE panel centers into a simple 2D roof layout for SVG rendering."""
    from app.roof_geometry import choose_south_panels, roof_measurements_m

    bbox = payload.get("boundingBox") or {}
    sw = bbox.get("sw") or {}
    ne = bbox.get("ne") or {}
    try:
        min_lat = float(sw["latitude"])
        min_lng = float(sw["longitude"])
        max_lat = float(ne["latitude"])
        max_lng = float(ne["longitude"])
    except Exception:
        center = payload.get("center") or {}
        lat = float(center.get("latitude") or 0)
        lng = float(center.get("longitude") or 0)
        min_lat, max_lat = lat - 0.0002, lat + 0.0002
        min_lng, max_lng = lng - 0.0002, lng + 0.0002

    dlat = max(max_lat - min_lat, 1e-6)
    dlng = max(max_lng - min_lng, 1e-6)
    min_lat -= dlat * 0.08
    max_lat += dlat * 0.08
    min_lng -= dlng * 0.08
    max_lng += dlng * 0.08
    dlat = max_lat - min_lat
    dlng = max_lng - min_lng

    potential = payload.get("solarPotential") or {}
    chosen = choose_south_panels(potential, recommended_panels)

    width = 640
    height = 480
    items = []
    for p in chosen:
        c = p.get("center") or {}
        try:
            plat = float(c["latitude"])
            plng = float(c["longitude"])
        except Exception:
            continue
        # x: west->east, y: north->south (image style)
        x = (plng - min_lng) / dlng * width
        y = (max_lat - plat) / dlat * height
        items.append(
            {
                "x": x,
                "y": y,
                "segmentIndex": p.get("segmentIndex"),
                "orientation": p.get("orientation"),
                "yearlyEnergyDcKwh": p.get("yearlyEnergyDcKwh"),
            }
        )

    meas = roof_measurements_m(payload)
    return {
        "width": width,
        "height": height,
        "panels": items,
        "count": len(items),
        "southFaceOnly": True,
        "measurements": meas,
    }


def render_layout_svg(layout: dict[str, Any], title: str = "Suggested panel layout") -> str:
    w = int(layout.get("width") or 640)
    h = int(layout.get("height") or 480)
    panels = layout.get("panels") or []
    # color by segment
    palette = ["#0f6e56", "#1b6ca8", "#f4a261", "#e76f51", "#2a9d8f", "#264653", "#e9c46a", "#8ab17d"]
    rects = []
    for p in panels:
        seg = int(p.get("segmentIndex") or 0)
        color = palette[seg % len(palette)]
        orient = (p.get("orientation") or "LANDSCAPE").upper()
        pw, ph = (18, 11) if orient == "LANDSCAPE" else (11, 18)
        x = float(p["x"]) - pw / 2
        y = float(p["y"]) - ph / 2
        rects.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{pw}" height="{ph}" rx="2" '
            f'fill="{color}" fill-opacity="0.88" stroke="#06281f" stroke-width="0.8"/>'
        )
    body = "\n".join(rects) if rects else ""
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#dfeffc"/>
      <stop offset="100%" stop-color="#c5dcc8"/>
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#bg)"/>
  <rect x="12" y="12" width="{w-24}" height="{h-24}" rx="16" fill="#f7faf7" stroke="#8aa194" stroke-width="2" stroke-dasharray="6 5"/>
  <text x="24" y="40" font-family="Segoe UI, Arial, sans-serif" font-size="18" font-weight="700" fill="#0b1726">{title}</text>
  <text x="24" y="62" font-family="Segoe UI, Arial, sans-serif" font-size="12" fill="#5b6b7c">{len(panels)} panels on sunniest roof positions (model)</text>
  {body}
  <text x="24" y="{h-20}" font-family="Segoe UI, Arial, sans-serif" font-size="11" fill="#5b6b7c">Illustrative layout from Google Solar panel positions — not a final engineered design</text>
</svg>
'''


async def solar_building_insights(api_key: str, lat: float, lng: float) -> dict[str, Any]:
    if not api_key:
        return {
            "ok": False,
            "error": "GOOGLE_MAPS_API_KEY not configured",
            "error_code": "NO_API_KEY",
        }
    # Prefer higher quality when available
    last_err: dict[str, Any] | None = None
    async with httpx.AsyncClient(timeout=45.0) as client:
        for quality in ("HIGH", "MEDIUM", "LOW"):
            params = {
                "location.latitude": lat,
                "location.longitude": lng,
                "requiredQuality": quality,
                "key": api_key,
            }
            r = await client.get(SOLAR_URL, params=params)
            try:
                data = r.json()
            except Exception:
                last_err = {
                    "ok": False,
                    "error": f"Solar API non-JSON response HTTP {r.status_code}",
                    "error_code": "BAD_RESPONSE",
                    "http_status": r.status_code,
                }
                continue
            if r.status_code == 404 or (
                isinstance(data, dict) and data.get("error", {}).get("status") == "NOT_FOUND"
            ):
                last_err = {
                    "ok": False,
                    "error": "No solar building insights found for this location",
                    "error_code": "NOT_FOUND",
                    "http_status": r.status_code,
                }
                continue
            if "error" in data:
                err = data["error"]
                last_err = {
                    "ok": False,
                    "error": err.get("message") or str(err),
                    "error_code": err.get("status") or "SOLAR_ERROR",
                    "http_status": r.status_code,
                }
                continue
            data["_requestedQuality"] = quality
            cache_building_insights(lat, lng, data)
            return {"ok": True, "data": data}
    return last_err or {
        "ok": False,
        "error": "Solar API failed",
        "error_code": "SOLAR_ERROR",
    }


def _customer_summary(result: dict[str, Any]) -> str:
    segs = result.get("roofSegments") or []
    best_segs = [s for s in segs if (s.get("recommendedPanelsOnSegment") or 0) > 0][:3]
    seg_lines = []
    for s in best_segs:
        seg_lines.append(
            f"- {s.get('recommendedPanelsOnSegment')} panels on a {s.get('direction')}-facing section "
            f"(pitch ~{s.get('pitchDegrees')}°, sunshine quality: {s.get('quality')})"
        )
    if not seg_lines:
        seg_lines = ["- Placement prioritizes the sunniest roof faces first."]

    sun = result.get("maxSunshineHoursPerYear")
    sun_txt = f"{sun:.0f} peak sunshine-hours/year on the best roof spots" if sun else "good local solar resource"
    offset_pct = int(round(float(result.get("targetOffsetFraction") or TARGET_OFFSET_FRACTION) * 100))
    bill = result.get("monthlyBillUsd")
    bill_txt = f"~${bill:.0f}/mo bill" if isinstance(bill, (int, float)) else "your usage inputs"
    rate_note = result.get("rateSource") or "default_planning_rate"
    usage_note = result.get("usageSource") or "bill_divided_by_rate"

    site_bits = []
    for label, key in [
        ("Ownership", "propertyOwnership"),
        ("HOA", "hoaRestrictions"),
        ("Roof", "roofMaterial"),
        ("Roof age/condition", "roofAgeCondition"),
        ("Shading notes", "shadingNotes"),
        ("Panel (amps)", "servicePanelAmps"),
        ("Large loads", "largeLoads"),
        ("Battery interest", "batteryInterest"),
        ("Financing preference", "financingPreference"),
        ("Timeline", "timeline"),
    ]:
        val = result.get(key)
        if val:
            site_bits.append(f"- {label}: {val}")

    site_block = ("\nHomeowner site details captured:\n" + "\n".join(site_bits) + "\n") if site_bits else "\n"

    return (
        f"Usage-sized estimate for {result.get('address')}:\n"
        f"• {bill_txt} → ~{result.get('estimatedMonthlyUsageKwh')} kWh/month "
        f"(usage source: {usage_note}; rate ${result.get('assumedUsdPerKwh')}/kWh via {rate_note}).\n"
        f"• Recommended starter system: {result.get('recommendedPanels')} panels "
        f"({result.get('systemSizeKw')} kW DC), about {result.get('yearlyEnergyDcKwh')} kWh/year "
        f"(~{result.get('monthlyEnergyKwh')} kWh/month).\n"
        f"• Sized near ~{offset_pct}% of estimated usage — NOT filling the whole roof.\n"
        f"• Roof could physically hold up to {result.get('maxPanels')} panels (capacity only).\n"
        f"• Sun access: {sun_txt}.\n"
        f"Suggested placement:\n"
        + "\n".join(seg_lines)
        + site_block
        + "\n*** BILL SAVINGS (planning estimate) ***\n"
        f"• Est. monthly bill offset: ~${result.get('estimatedMonthlySavingsUsd')}/mo "
        f"(~{result.get('estimatedBillReductionPct')}% of current bill)\n"
        f"• Est. new utility bill ballpark: ~${result.get('estimatedNewMonthlyBillUsd')}/mo "
        f"(before fixed fees)\n"
        f"• Est. yearly savings: ~${result.get('estimatedYearlySavingsUsd')}\n"
        f"• 10-year savings ballpark: ~${result.get('estimated10YearSavingsUsd')} · "
        f"20-year ~${result.get('estimated20YearSavingsUsd')}\n"
        f"• Assumptions: {result.get('savingsAssumptions')}\n"
        "How energy lowers the bill: panels make power your home uses first → less kWh bought from the utility "
        "→ lower energy charges on the bill. Exact $ depends on utility rates, fixed fees, incentives, and financing.\n"
        "Close with a strong callback CTA: name + phone so a SOL-RIGHT specialist can confirm install pricing ASAP."
    )


async def solar_estimate(
    api_key: str,
    address: str,
    monthly_bill_usd: float | None = None,
    monthly_usage_kwh: float | None = None,
    usd_per_kwh: float | None = None,
    target_offset_pct: float | None = None,
    property_ownership: str | None = None,
    hoa_restrictions: str | None = None,
    roof_material: str | None = None,
    roof_age_condition: str | None = None,
    shading_notes: str | None = None,
    service_panel_amps: str | None = None,
    large_loads: str | None = None,
    battery_interest: str | None = None,
    financing_preference: str | None = None,
    timeline: str | None = None,
    future_usage_plans: str | None = None,
) -> dict[str, Any]:
    """Geocode + Solar API + homeowner inputs → accurate-as-possible planning estimate.

    Google Solar itself only receives lat/lng (roof geometry / sun / panel layouts).
    Bill, kWh, rate, and offset % improve which panel config we select from Solar's configs.
    Other site answers are returned for explanation + CRM (not sent to Google).
    """
    address = (address or "").strip()
    bill = None
    if monthly_bill_usd is not None and str(monthly_bill_usd) != "":
        try:
            bill = float(monthly_bill_usd)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "monthly_bill_usd must be a number",
                "error_code": "BAD_BILL",
            }
        if bill <= 0 or bill > 5000:
            return {
                "ok": False,
                "error": "monthly_bill_usd out of expected range (0–5000)",
                "error_code": "BAD_BILL",
            }

    kwh = None
    if monthly_usage_kwh is not None and str(monthly_usage_kwh) != "":
        try:
            kwh = float(monthly_usage_kwh)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "monthly_usage_kwh must be a number",
                "error_code": "BAD_KWH",
            }
        if kwh <= 0 or kwh > 20000:
            return {
                "ok": False,
                "error": "monthly_usage_kwh out of expected range",
                "error_code": "BAD_KWH",
            }

    rate = None
    if usd_per_kwh is not None and str(usd_per_kwh) != "":
        try:
            rate = float(usd_per_kwh)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "usd_per_kwh must be a number",
                "error_code": "BAD_RATE",
            }
        if rate <= 0 or rate > 2:
            return {
                "ok": False,
                "error": "usd_per_kwh out of expected range",
                "error_code": "BAD_RATE",
            }

    if bill is None and kwh is None:
        return {
            "ok": False,
            "error": "Need monthly_bill_usd and/or monthly_usage_kwh",
            "error_code": "NEED_USAGE",
        }

    if not looks_like_us_address(address):
        return {
            "ok": False,
            "error": "Please provide a full US service address (street, city, state, ZIP).",
            "error_code": "NEED_US_ADDRESS",
        }

    usage = _estimate_usage_kwh(
        monthly_bill_usd=bill,
        usd_per_kwh=rate,
        monthly_usage_kwh=kwh,
        target_offset_pct=target_offset_pct,
    )

    geo = await geocode_address(api_key, address)
    if not geo.get("ok"):
        return geo

    solar = await solar_building_insights(api_key, geo["lat"], geo["lng"])
    if not solar.get("ok"):
        return {
            **solar,
            "address": geo.get("formatted_address"),
            "lat": geo.get("lat"),
            "lng": geo.get("lng"),
        }

    payload = solar["data"]
    potential = payload.get("solarPotential") or {}
    picked = _pick_panel_config(potential, bill, usage=usage)
    cfg = picked.get("config") or {}
    usage = picked.get("usage") or usage

    yearly = float(cfg.get("yearlyEnergyDcKwh") or 0)
    monthly_prod = round(yearly / 12.0, 1) if yearly else None
    max_panels = int(potential.get("maxArrayPanelsCount") or 0)
    recommended = int(cfg.get("panelsCount") or 0)
    panel_w = float(potential.get("panelCapacityWatts") or 400)
    system_kw = round((recommended * panel_w) / 1000.0, 2) if recommended else None

    segments = _segment_summaries(potential, recommended)
    layout = _layout_model(payload, recommended)

    img_date = payload.get("imageryDate") or {}
    imagery_date = None
    if img_date:
        imagery_date = f"{img_date.get('year')}-{img_date.get('month')}-{img_date.get('day')}"

    # Confidence score for the quote (not Google's score — our intake completeness)
    confidence = 45  # base: address + google roof model
    if usage.get("usageSource") == "customer_kwh":
        confidence += 25
    elif usage.get("usageSource") == "bill_divided_by_rate":
        confidence += 15
    if usage.get("rateSource") == "customer_rate":
        confidence += 10
    if target_offset_pct is not None:
        confidence += 5
    for v in (roof_material, roof_age_condition, shading_notes, service_panel_amps, property_ownership):
        if v:
            confidence += 3
    confidence = min(95, confidence)

    rate = float(usage.get("assumedUsdPerKwh") or 0.135)
    bill_m = float(bill or 0)
    use_m = float(usage.get("estimatedMonthlyUsageKwh") or 0)
    prod_m = float(monthly_prod or 0)
    # Energy that actually offsets the bill ≈ min(production, usage)
    offset_kwh_m = min(prod_m, use_m) if (prod_m and use_m) else prod_m
    monthly_savings = round(offset_kwh_m * rate, 2) if offset_kwh_m else None
    # Don't claim more savings than the bill
    if monthly_savings is not None and bill_m > 0:
        monthly_savings = round(min(monthly_savings, bill_m * 0.95), 2)
    yearly_savings = round(monthly_savings * 12, 0) if monthly_savings is not None else None
    ten_year = round(yearly_savings * 10, 0) if yearly_savings is not None else None
    twenty_year = round(yearly_savings * 20, 0) if yearly_savings is not None else None
    new_bill_est = None
    bill_cut_pct = None
    if bill_m > 0 and monthly_savings is not None:
        new_bill_est = round(max(bill_m - monthly_savings, bill_m * 0.05), 2)
        bill_cut_pct = int(round((monthly_savings / bill_m) * 100))

    result = {
        "ok": True,
        "address": geo.get("formatted_address"),
        "lat": geo["lat"],
        "lng": geo["lng"],
        "monthlyBillUsd": bill,
        "monthlyUsageKwhInput": kwh,
        "assumedUsdPerKwh": usage.get("assumedUsdPerKwh"),
        "rateSource": usage.get("rateSource"),
        "usageSource": usage.get("usageSource"),
        "estimatedMonthlyUsageKwh": usage.get("estimatedMonthlyUsageKwh"),
        "estimatedAnnualUsageKwh": usage.get("estimatedAnnualUsageKwh"),
        "targetOffsetFraction": usage.get("targetOffsetFraction"),
        "targetOffsetPct": int(round(float(usage.get("targetOffsetFraction") or 0.9) * 100)),
        "targetAnnualProductionKwh": usage.get("targetAnnualProductionKwh"),
        "sizingMethod": picked.get("method"),
        "recommendedPanels": recommended,
        "southFaceOnly": True,
        "southFaceCapacity": picked.get("southFaceCapacity"),
        "roofMeasurements": __import__("app.roof_geometry", fromlist=["roof_measurements_m"]).roof_measurements_m(payload),
        "systemSizeKw": system_kw,
        "panelWattsAssumed": panel_w,
        "yearlyEnergyDcKwh": round(yearly, 1) if yearly else None,
        "monthlyEnergyKwh": monthly_prod,
        "maxPanels": max_panels,
        "maxSunshineHoursPerYear": potential.get("maxSunshineHoursPerYear"),
        "imageryQuality": payload.get("imageryQuality"),
        "imageryDate": imagery_date,
        "roofSegments": segments,
        "layout": layout,
        "quoteConfidence": confidence,
        "estimatedMonthlySavingsUsd": monthly_savings,
        "estimatedYearlySavingsUsd": yearly_savings,
        "estimated10YearSavingsUsd": ten_year,
        "estimated20YearSavingsUsd": twenty_year,
        "estimatedNewMonthlyBillUsd": new_bill_est,
        "estimatedBillReductionPct": bill_cut_pct,
        "savingsAssumptions": (
            f"Planning rate ${rate:.3f}/kWh; monthly savings ≈ min(production, usage) × rate, "
            f"capped near current bill. Excludes fixed utility fees, incentives, financing. Not a guarantee."
        ),
        "googleSolarInputs": {
            "location.latitude": geo["lat"],
            "location.longitude": geo["lng"],
            "note": "Google Solar API accepts location only; other fields refine sizing/CRM.",
        },
        "propertyOwnership": property_ownership,
        "hoaRestrictions": hoa_restrictions,
        "roofMaterial": roof_material,
        "roofAgeCondition": roof_age_condition,
        "shadingNotes": shading_notes,
        "servicePanelAmps": service_panel_amps,
        "largeLoads": large_loads,
        "batteryInterest": battery_interest,
        "financingPreference": financing_preference,
        "timeline": timeline,
        "futureUsagePlans": future_usage_plans,
        "media": {},
        "notes": (
            f"Sized to ~{int(round(float(usage.get('targetOffsetFraction') or 0.9)*100))}% of estimated usage "
            f"using {usage.get('usageSource')}. Lead with dollar savings and callback CTA."
        ),
    }
    result["customerSummary"] = _customer_summary(result)
    return result
