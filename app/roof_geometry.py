"""Roof geometry helpers: S-face panel selection, ridge, N/S frames, measurements."""
from __future__ import annotations

import math
from typing import Any


def is_south_facing(azimuth: float | None, loose: bool = True) -> bool:
    """True for south-facing roof planes (SE–SW or strict S)."""
    if azimuth is None:
        return False
    a = float(azimuth) % 360.0
    if loose:
        return 112.5 <= a <= 247.5  # SE through SW
    return 157.5 <= a <= 202.5  # pure S


def is_north_facing(azimuth: float | None, loose: bool = True) -> bool:
    if azimuth is None:
        return False
    a = float(azimuth) % 360.0
    if loose:
        return a >= 292.5 or a <= 67.5  # NW through NE
    return a >= 337.5 or a <= 22.5


def compass(azimuth: float | None) -> str:
    if azimuth is None:
        return "?"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int(((float(azimuth) % 360) / 45.0) + 0.5) % 8]


def segment_azimuth_map(solar_potential: dict[str, Any]) -> dict[int, float]:
    out: dict[int, float] = {}
    for i, seg in enumerate(solar_potential.get("roofSegmentStats") or []):
        az = seg.get("azimuthDegrees")
        if az is not None:
            out[i] = float(az)
    return out


def south_segment_indices(solar_potential: dict[str, Any]) -> set[int]:
    idxs: set[int] = set()
    for i, seg in enumerate(solar_potential.get("roofSegmentStats") or []):
        pitch = float(seg.get("pitchDegrees") or 0)
        if pitch > 50:  # wall-like
            continue
        if is_south_facing(seg.get("azimuthDegrees")):
            idxs.add(i)
    return idxs


def north_segment_indices(solar_potential: dict[str, Any]) -> set[int]:
    idxs: set[int] = set()
    for i, seg in enumerate(solar_potential.get("roofSegmentStats") or []):
        pitch = float(seg.get("pitchDegrees") or 0)
        if pitch > 50:
            continue
        if is_north_facing(seg.get("azimuthDegrees")):
            idxs.add(i)
    return idxs


def choose_south_panels(
    solar_potential: dict[str, Any],
    recommended_panels: int,
) -> list[dict[str, Any]]:
    """Sunniest panels that sit on south-facing roof segments only."""
    south = south_segment_indices(solar_potential)
    panels = list(solar_potential.get("solarPanels") or [])
    candidates = [
        p
        for p in panels
        if int(p.get("segmentIndex") if p.get("segmentIndex") is not None else -1) in south
    ]
    candidates.sort(key=lambda p: float(p.get("yearlyEnergyDcKwh") or 0), reverse=True)
    n = max(0, int(recommended_panels or 0))
    return candidates[:n]


def roof_measurements_m(building_insights: dict[str, Any]) -> dict[str, float | None]:
    """Rough roof/building footprint size in meters from bounding box."""
    bbox = building_insights.get("boundingBox") or {}
    sw = bbox.get("sw") or {}
    ne = bbox.get("ne") or {}
    try:
        sw_lat, sw_lng = float(sw["latitude"]), float(sw["longitude"])
        ne_lat, ne_lng = float(ne["latitude"]), float(ne["longitude"])
    except Exception:
        return {"widthM": None, "depthM": None, "areaM2": None}

    # meters per degree approx at latitude
    mid_lat = (sw_lat + ne_lat) / 2.0
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(mid_lat))
    depth = abs(ne_lat - sw_lat) * m_per_deg_lat  # N-S
    width = abs(ne_lng - sw_lng) * m_per_deg_lng  # E-W
    # sum of segment areas if available
    area = 0.0
    for seg in (building_insights.get("solarPotential") or {}).get("roofSegmentStats") or []:
        a = (seg.get("stats") or {}).get("areaMeters2")
        if a is not None:
            area += float(a)
    return {
        "widthM": round(width, 1),
        "depthM": round(depth, 1),
        "areaM2": round(area, 1) if area else round(width * depth, 1),
    }


def primary_face_pair(solar_potential: dict[str, Any]) -> dict[str, Any]:
    """Largest south + largest north roof faces (for peak / dual frames)."""
    segs = list(solar_potential.get("roofSegmentStats") or [])
    south = []
    north = []
    for i, seg in enumerate(segs):
        pitch = float(seg.get("pitchDegrees") or 0)
        if pitch > 50:
            continue
        area = float((seg.get("stats") or {}).get("areaMeters2") or 0)
        az = seg.get("azimuthDegrees")
        item = {"index": i, "seg": seg, "area": area, "az": az, "pitch": pitch}
        if is_south_facing(az):
            south.append(item)
        if is_north_facing(az):
            north.append(item)
    south.sort(key=lambda x: -x["area"])
    north.sort(key=lambda x: -x["area"])
    return {
        "south": south[0] if south else None,
        "north": north[0] if north else None,
        "all_south": south,
        "all_north": north,
    }


def pick_panel_orientation(
    face_azimuth: float,
    panel_w_m: float,
    panel_h_m: float,
    face_width_m: float | None,
) -> str:
    """Choose LANDSCAPE vs PORTRAIT for better fit along eaves."""
    # eaves run perpendicular to face azimuth
    # Prefer landscape (long edge along eaves) unless face is narrow
    long_m, short_m = max(panel_w_m, panel_h_m), min(panel_w_m, panel_h_m)
    if face_width_m and face_width_m > 0 and face_width_m < long_m * 3.5:
        return "PORTRAIT"
    return "LANDSCAPE"


def financial_pick_config(
    solar_potential: dict[str, Any],
    monthly_bill_usd: float | None,
) -> dict[str, Any] | None:
    """Match Build-2 brief JS: closest financialAnalyses monthlyBill.units."""
    configs = list(solar_potential.get("solarPanelConfigs") or [])
    analyses = list(solar_potential.get("financialAnalyses") or [])
    if not configs or not analyses:
        return None
    bill = float(monthly_bill_usd or 0)
    best = None
    best_diff = None
    if bill > 0:
        for a in analyses:
            units = (a.get("monthlyBill") or {}).get("units")
            try:
                u = float(units)
            except (TypeError, ValueError):
                continue
            idx = a.get("panelConfigIndex")
            if not isinstance(idx, int) or idx < 0 or idx >= len(configs):
                # brief allows -1; skip invalid
                continue
            diff = abs(u - bill)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best = configs[idx]
    if best is None:
        # defaultBill fallback
        for a in analyses:
            if (a.get("monthlyBill") or {}).get("defaultBill"):
                idx = a.get("panelConfigIndex")
                if isinstance(idx, int) and 0 <= idx < len(configs):
                    return configs[idx]
        return configs[-1] if configs else None
    return best
