"""Georeferenced roof imagery + accurate panel overlays.

Google Solar RGB GeoTIFFs include ModelTransformationTag in a projected CRS
(typically UTM). Panel centers and roof segment geometry from buildingInsights
are WGS84 lat/lng. We project into pixel space with the same affine transform.

Improvements:
- Larger canvas (padding + 1.5x upscale for chat readability)
- Roof-segment wireframes with direction labels (peaks/faces)
- Panels rotated to match roof-face azimuth (eaves alignment)
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont
from pyproj import Transformer


@dataclass
class GeoRaster:
    arr: np.ndarray  # HxWx3 uint8
    # pixel (col,row) -> map (x,y): x = a*col + b*row + c ; y = d*col + e*row + f
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float
    crs_epsg: int

    @property
    def height(self) -> int:
        return int(self.arr.shape[0])

    @property
    def width(self) -> int:
        return int(self.arr.shape[1])

    def map_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        det = self.a * self.e - self.b * self.d
        if abs(det) < 1e-12:
            raise ValueError("non-invertible affine")
        col = (self.e * (x - self.c) - self.b * (y - self.f)) / det
        row = (-self.d * (x - self.c) + self.a * (y - self.f)) / det
        return col, row

    def latlng_to_pixel(self, lat: float, lng: float) -> tuple[float, float]:
        to_map = Transformer.from_crs("EPSG:4326", f"EPSG:{self.crs_epsg}", always_xy=True)
        x, y = to_map.transform(lng, lat)
        return self.map_to_pixel(x, y)


def _parse_geo_from_tiff(raw: bytes) -> GeoRaster:
    with tifffile.TiffFile(io.BytesIO(raw)) as tif:
        page = tif.pages[0]
        arr = page.asarray()
        if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[0] < arr.shape[-1]:
            arr = np.transpose(arr[:3], (1, 2, 0))
        elif arr.ndim == 3 and arr.shape[-1] >= 3:
            arr = arr[:, :, :3]
        else:
            raise ValueError(f"unexpected raster shape {arr.shape}")
        if arr.dtype != np.uint8:
            amin, amax = float(arr.min()), float(arr.max())
            if amax > amin:
                arr = ((arr - amin) / (amax - amin) * 255.0).astype(np.uint8)
            else:
                arr = np.zeros(arr.shape[:2] + (3,), dtype=np.uint8)

        mt = None
        crs = 32616
        for tag in page.tags.values():
            if tag.name == "ModelTransformationTag":
                mt = tag.value
            if tag.name == "GeoKeyDirectoryTag":
                keys = tag.value
                try:
                    vals = list(keys)
                    n = int(vals[3]) if len(vals) > 3 else 0
                    for i in range(4, 4 + n * 4, 4):
                        k, _t, _c, v = vals[i : i + 4]
                        if int(k) == 3072:
                            crs = int(v)
                except Exception:
                    pass
        if not mt or len(mt) < 16:
            raise ValueError("GeoTIFF missing ModelTransformationTag")
        a, b, _c0, c = float(mt[0]), float(mt[1]), float(mt[2]), float(mt[3])
        d, e, _g0, f = float(mt[4]), float(mt[5]), float(mt[6]), float(mt[7])
        return GeoRaster(arr=arr.astype(np.uint8), a=a, b=b, c=c, d=d, e=e, f=f, crs_epsg=crs)


async def fetch_rgb_georaster(
    api_key: str,
    lat: float,
    lng: float,
    radius_m: float = 40.0,
    pixel_size_m: float = 0.1,
) -> GeoRaster:
    params = {
        "location.latitude": lat,
        "location.longitude": lng,
        "radiusMeters": radius_m,
        "view": "FULL_LAYERS",
        "requiredQuality": "MEDIUM",
        "pixelSizeMeters": pixel_size_m,
        "key": api_key,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        dl = None
        for quality in ("HIGH", "MEDIUM", "LOW"):
            params["requiredQuality"] = quality
            r = await client.get("https://solar.googleapis.com/v1/dataLayers:get", params=params)
            if r.status_code >= 400:
                continue
            data = r.json()
            if data.get("rgbUrl"):
                dl = data
                break
        if not dl:
            raise RuntimeError("Solar dataLayers unavailable")
        rgb_url = dl["rgbUrl"]
        sep = "&" if "?" in rgb_url else "?"
        rr = await client.get(f"{rgb_url}{sep}key={api_key}")
        if rr.status_code >= 400:
            raise RuntimeError(f"RGB fetch failed HTTP {rr.status_code}")
        return _parse_geo_from_tiff(rr.content)


async def fetch_mask_array(
    api_key: str,
    lat: float,
    lng: float,
    radius_m: float = 40.0,
    pixel_size_m: float = 0.1,
) -> np.ndarray | None:
    params = {
        "location.latitude": lat,
        "location.longitude": lng,
        "radiusMeters": radius_m,
        "view": "FULL_LAYERS",
        "requiredQuality": "MEDIUM",
        "pixelSizeMeters": pixel_size_m,
        "key": api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            dl = None
            for quality in ("HIGH", "MEDIUM", "LOW"):
                params["requiredQuality"] = quality
                r = await client.get("https://solar.googleapis.com/v1/dataLayers:get", params=params)
                if r.status_code >= 400:
                    continue
                data = r.json()
                if data.get("maskUrl"):
                    dl = data
                    break
            if not dl:
                return None
            url = dl["maskUrl"]
            sep = "&" if "?" in url else "?"
            rr = await client.get(f"{url}{sep}key={api_key}")
            if rr.status_code >= 400:
                return None
            with tifffile.TiffFile(io.BytesIO(rr.content)) as tif:
                arr = tif.pages[0].asarray()
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            return arr > 0
    except Exception:
        return None


def _compass(azimuth: float | None) -> str:
    if azimuth is None:
        return "?"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int(((float(azimuth) % 360) / 45.0) + 0.5) % 8]


def _azimuth_to_image_angle_deg(azimuth_deg: float) -> float:
    """Compass azimuth (0=N CW) → PIL rotation degrees.

    Image: +x east, +y south. Panel long edge runs along eaves (perp. to downslope).
    Downslope compass = azimuth. Eaves compass = azimuth + 90°.
    PIL rotates CCW; we build polygons via math instead of Image.rotate.
    Returns angle of long-edge direction from +x toward +y (clockwise in image).
    """
    # long edge along eaves: compass bearing azimuth+90
    eaves = (float(azimuth_deg) + 90.0) % 360.0
    # vector in image: east=sin(bearing), south=-cos(bearing) wait:
    # north component = cos(bearing), east = sin(bearing)
    # image x = east, image y = -north = -cos(bearing)
    rad = math.radians(eaves)
    vx = math.sin(rad)   # east
    vy = -math.cos(rad)  # south-positive image y
    return math.degrees(math.atan2(vy, vx))


def _rotated_rect(
    cx: float, cy: float, width: float, height: float, angle_deg: float
) -> list[tuple[float, float]]:
    """Return 4 corners of a rectangle centered at cx,cy; width along long axis at angle."""
    ang = math.radians(angle_deg)
    # local axes: long along angle, short perpendicular
    ux, uy = math.cos(ang), math.sin(ang)
    vx, vy = -math.sin(ang), math.cos(ang)
    hw, hh = width / 2.0, height / 2.0
    corners = []
    for sx, sy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
        corners.append((cx + sx * ux + sy * vx, cy + sx * uy + sy * vy))
    return corners


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Monotone chain convex hull."""
    pts = sorted(set((round(p[0], 3), round(p[1], 3)) for p in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _expand_hull(
    hull: list[tuple[float, float]], pad_px: float
) -> list[tuple[float, float]]:
    if len(hull) < 3 or pad_px <= 0:
        return hull
    cx = sum(p[0] for p in hull) / len(hull)
    cy = sum(p[1] for p in hull) / len(hull)
    out = []
    for x, y in hull:
        dx, dy = x - cx, y - cy
        dist = math.hypot(dx, dy) or 1.0
        out.append((x + pad_px * dx / dist, y + pad_px * dy / dist))
    return out


def _bbox_pixel_window(
    geo: GeoRaster,
    sw_lat: float,
    sw_lng: float,
    ne_lat: float,
    ne_lng: float,
    pad_frac: float = 0.35,
) -> tuple[int, int, int, int]:
    corners = [
        geo.latlng_to_pixel(sw_lat, sw_lng),
        geo.latlng_to_pixel(sw_lat, ne_lng),
        geo.latlng_to_pixel(ne_lat, sw_lng),
        geo.latlng_to_pixel(ne_lat, ne_lng),
    ]
    cols = [c for c, _ in corners]
    rows = [r for _, r in corners]
    min_c, max_c = min(cols), max(cols)
    min_r, max_r = min(rows), max(rows)
    wc = max(max_c - min_c, 40)
    hr = max(max_r - min_r, 40)
    min_c -= wc * pad_frac
    max_c += wc * pad_frac
    min_r -= hr * pad_frac
    max_r += hr * pad_frac
    x0 = max(0, int(math.floor(min_c)))
    y0 = max(0, int(math.floor(min_r)))
    x1 = min(geo.width, int(math.ceil(max_c)))
    y1 = min(geo.height, int(math.ceil(max_r)))
    if x1 <= x0 + 10 or y1 <= y0 + 10:
        return 0, 0, geo.width, geo.height
    return x0, y0, x1, y1


def _segment_polygon_pixels(
    geo: GeoRaster,
    seg: dict[str, Any],
    all_panels: list[dict[str, Any]],
    seg_index: int,
    x0: int,
    y0: int,
) -> list[tuple[float, float]]:
    """Build a wireframe polygon for a roof segment in crop pixel space."""
    # Prefer hull of this segment's panel centers (best shape of the face)
    pts: list[tuple[float, float]] = []
    for p in all_panels:
        if int(p.get("segmentIndex") or -1) != seg_index:
            continue
        c = p.get("center") or {}
        try:
            col, row = geo.latlng_to_pixel(float(c["latitude"]), float(c["longitude"]))
            pts.append((col - x0, row - y0))
        except Exception:
            continue
    if len(pts) >= 3:
        hull = _convex_hull(pts)
        return _expand_hull(hull, pad_px=14)

    # Fallback: segment bounding box corners
    bb = seg.get("boundingBox") or {}
    sw = bb.get("sw") or {}
    ne = bb.get("ne") or {}
    try:
        corners_ll = [
            (float(sw["latitude"]), float(sw["longitude"])),
            (float(sw["latitude"]), float(ne["longitude"])),
            (float(ne["latitude"]), float(ne["longitude"])),
            (float(ne["latitude"]), float(sw["longitude"])),
        ]
        out = []
        for la, ln in corners_ll:
            col, row = geo.latlng_to_pixel(la, ln)
            out.append((col - x0, row - y0))
        return out
    except Exception:
        return []


def render_roof_overlay_png(
    geo: GeoRaster,
    building_insights: dict[str, Any],
    recommended_panels: int,
    mask: np.ndarray | None = None,
    mode: str = "aerial",
    dsm: np.ndarray | None = None,
) -> bytes:
    """Aerial-first overlay with N/S roof frames, ridge/peak, S-face-only panels."""
    from app.roof_geometry import (
        choose_south_panels,
        north_segment_indices,
        pick_panel_orientation,
        primary_face_pair,
        roof_measurements_m,
        south_segment_indices,
    )

    bbox = building_insights.get("boundingBox") or {}
    sw = bbox.get("sw") or {}
    ne = bbox.get("ne") or {}
    try:
        x0, y0, x1, y1 = _bbox_pixel_window(
            geo,
            float(sw["latitude"]),
            float(sw["longitude"]),
            float(ne["latitude"]),
            float(ne["longitude"]),
            pad_frac=0.42,
        )
    except Exception:
        x0, y0, x1, y1 = 0, 0, geo.width, geo.height

    crop = geo.arr[y0:y1, x0:x1].copy()
    mode = (mode or "aerial").lower().strip()
    if mode not in ("schematic", "masked", "aerial"):
        mode = "aerial"

    mask_crop = None
    if mask is not None and mask.shape[:2] == geo.arr.shape[:2]:
        mask_crop = mask[y0:y1, x0:x1]

    dsm_crop = None
    if dsm is not None and dsm.shape[:2] == geo.arr.shape[:2]:
        dsm_crop = dsm[y0:y1, x0:x1]

    if mode == "schematic":
        h, w = crop.shape[:2]
        canvas = np.zeros((h, w, 3), dtype=np.uint8)
        canvas[:] = (18, 28, 42)
        if mask_crop is not None:
            canvas[mask_crop] = (72, 82, 96)
        base = Image.fromarray(canvas).convert("RGBA")
    elif mode == "masked" and mask_crop is not None:
        out = np.zeros_like(crop)
        out[:] = (22, 32, 46)
        out[mask_crop] = crop[mask_crop]
        base = Image.fromarray(out).convert("RGBA")
    else:
        if mask_crop is not None:
            out = crop.astype(np.float32)
            outside = ~mask_crop
            out[outside] = out[outside] * 0.45 + np.array([20, 28, 40], dtype=np.float32) * 0.55
            base = Image.fromarray(out.astype(np.uint8)).convert("RGBA")
        else:
            base = Image.fromarray(crop).convert("RGBA")

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    potential = building_insights.get("solarPotential") or {}
    all_panels = list(potential.get("solarPanels") or [])
    segs = list(potential.get("roofSegmentStats") or [])
    south_idxs = south_segment_indices(potential)
    north_idxs = north_segment_indices(potential)
    pair = primary_face_pair(potential)
    meas = roof_measurements_m(building_insights)

    def face_hull(seg_set: set[int], pad: float = 16.0) -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        for p in all_panels:
            si = p.get("segmentIndex")
            if si is None or int(si) not in seg_set:
                continue
            c = p.get("center") or {}
            try:
                col, row = geo.latlng_to_pixel(float(c["latitude"]), float(c["longitude"]))
                pts.append((col - x0, row - y0))
            except Exception:
                continue
        if len(pts) < 3:
            return []
        return _expand_hull(_convex_hull(pts), pad_px=pad)

    s_hull = face_hull(south_idxs, pad=18)
    n_hull = face_hull(north_idxs, pad=18)

    if n_hull and len(n_hull) >= 3:
        draw.polygon(n_hull, fill=(80, 160, 255, 40), outline=(120, 200, 255, 240))
        for a, b in zip(n_hull, n_hull[1:] + n_hull[:1]):
            draw.line([a, b], fill=(140, 210, 255, 255), width=4)
        ncx = sum(p[0] for p in n_hull) / len(n_hull)
        ncy = sum(p[1] for p in n_hull) / len(n_hull)
        pitch_n = (pair.get("north") or {}).get("pitch")
        label_n = "NORTH face"
        if pitch_n is not None:
            label_n += f" · {float(pitch_n):.0f}° pitch"
        tw = max(90, int(7.5 * len(label_n)))
        draw.rectangle([ncx - 6, ncy - 12, ncx - 6 + tw, ncy + 12], fill=(10, 40, 80, 210))
        draw.text((ncx, ncy - 8), label_n, fill=(220, 240, 255, 255))

    if s_hull and len(s_hull) >= 3:
        draw.polygon(s_hull, fill=(40, 180, 120, 45), outline=(80, 255, 160, 250))
        for a, b in zip(s_hull, s_hull[1:] + s_hull[:1]):
            draw.line([a, b], fill=(100, 255, 170, 255), width=4)
        scx = sum(p[0] for p in s_hull) / len(s_hull)
        scy = sum(p[1] for p in s_hull) / len(s_hull)
        pitch_s = (pair.get("south") or {}).get("pitch")
        label_s = "SOUTH face (panels here)"
        if pitch_s is not None:
            label_s += f" · {float(pitch_s):.0f}°"
        tw = max(120, int(7.2 * len(label_s)))
        draw.rectangle([scx - 6, scy - 12, scx - 6 + tw, scy + 12], fill=(8, 60, 40, 220))
        draw.text((scx, scy - 8), label_s, fill=(220, 255, 230, 255))

    ridge_pts: list[tuple[float, float]] = []
    south_p = pair.get("south")
    north_p = pair.get("north")
    if south_p and north_p:
        try:
            sc = (south_p["seg"].get("center") or {})
            nc = (north_p["seg"].get("center") or {})
            scol, srow = geo.latlng_to_pixel(float(sc["latitude"]), float(sc["longitude"]))
            ncol, nrow = geo.latlng_to_pixel(float(nc["latitude"]), float(nc["longitude"]))
            scol, srow = scol - x0, srow - y0
            ncol, nrow = ncol - x0, nrow - y0
            mx, my = (scol + ncol) / 2.0, (srow + nrow) / 2.0
            s_az = float(south_p.get("az") or 180.0)
            eaves_angle = _azimuth_to_image_angle_deg(s_az)
            half_len = 80.0
            if meas.get("widthM") and geo.a:
                half_len = max(40.0, float(meas["widthM"]) / max(abs(geo.a), 0.05) * 0.45)
            rad = math.radians(eaves_angle)
            dx, dy = math.cos(rad) * half_len, math.sin(rad) * half_len
            ridge_pts = [(mx - dx, my - dy), (mx + dx, my + dy)]
            draw.line(ridge_pts, fill=(255, 230, 80, 255), width=5)
            draw.ellipse([mx - 6, my - 6, mx + 6, my + 6], fill=(255, 220, 60, 255), outline=(255, 255, 255, 255))
            draw.rectangle([mx + 10, my - 14, mx + 150, my + 10], fill=(40, 30, 0, 200))
            draw.text((mx + 14, my - 10), "ROOF PEAK / RIDGE", fill=(255, 245, 180, 255))
        except Exception:
            pass

    if dsm_crop is not None and mask_crop is not None:
        try:
            masked_dsm = np.where(mask_crop, dsm_crop.astype(np.float64), -1e9)
            peak_idx = np.unravel_index(int(np.argmax(masked_dsm)), masked_dsm.shape)
            py, px = int(peak_idx[0]), int(peak_idx[1])
            if masked_dsm[py, px] > -1e8:
                draw.ellipse([px - 5, py - 5, px + 5, py + 5], outline=(255, 255, 0, 255), width=2)
        except Exception:
            pass

    chosen = choose_south_panels(potential, recommended_panels)
    pw_m = float(potential.get("panelWidthMeters") or 1.045)
    ph_m = float(potential.get("panelHeightMeters") or 1.879)
    m_per_px = max(abs(geo.a), abs(geo.e), 0.05)
    pw_px = max(5.0, pw_m / m_per_px)
    ph_px = max(7.0, ph_m / m_per_px)

    face_width_m = None
    if south_p and south_p.get("area"):
        face_width_m = math.sqrt(max(float(south_p["area"]), 1.0)) * 1.4

    palette_s = (15, 130, 90, 230)
    placed = 0
    skipped = 0
    for p in chosen:
        c = p.get("center") or {}
        try:
            plat = float(c["latitude"])
            plng = float(c["longitude"])
            col, row = geo.latlng_to_pixel(plat, plng)
        except Exception:
            skipped += 1
            continue
        cx, cy = col - x0, row - y0
        if cx < -15 or cy < -15 or cx > (x1 - x0) + 15 or cy > (y1 - y0) + 15:
            skipped += 1
            continue
        if mask is not None:
            ic, ir = int(round(col)), int(round(row))
            if not (0 <= ir < mask.shape[0] and 0 <= ic < mask.shape[1] and bool(mask[ir, ic])):
                skipped += 1
                continue
        if s_hull and len(s_hull) >= 3 and not _point_in_poly(cx, cy, s_hull):
            skipped += 1
            continue

        si = int(p.get("segmentIndex") or 0)
        az = 180.0
        if 0 <= si < len(segs):
            az = float(segs[si].get("azimuthDegrees") or 180.0)
        angle = _azimuth_to_image_angle_deg(az)
        orient = pick_panel_orientation(az, pw_m, ph_m, face_width_m)
        g_or = (p.get("orientation") or "").upper()
        if g_or in ("LANDSCAPE", "PORTRAIT"):
            orient = g_or
        if orient == "LANDSCAPE":
            long_px, short_px = max(pw_px, ph_px), min(pw_px, ph_px)
        else:
            long_px, short_px = min(pw_px, ph_px), max(pw_px, ph_px)
        long_px *= 0.90
        short_px *= 0.90
        corners = _rotated_rect(cx, cy, long_px, short_px, angle)
        draw.polygon(corners, fill=palette_s, outline=(255, 255, 255, 245))
        placed += 1

    composed = Image.alpha_composite(base, overlay).convert("RGB")

    bar_h = max(64, int(composed.height * 0.13))
    draw2 = ImageDraw.Draw(composed)
    draw2.rectangle([0, composed.height - bar_h, composed.width, composed.height], fill=(7, 26, 51))
    w_m, d_m, a_m = meas.get("widthM"), meas.get("depthM"), meas.get("areaM2")
    meas_txt = f"Roof footprint ~{w_m}m x {d_m}m" if w_m and d_m else "Roof measured from Solar bbox"
    if a_m:
        meas_txt += f" · roof planes ~{a_m} m2"
    line1 = f"S-face only · {placed} panels on roof · skipped {skipped} · {meas_txt}"
    line2 = "Green=SOUTH frame · Blue=NORTH frame · Yellow=ridge/peak · panels real-scale"
    if mode == "schematic":
        line2 = "Tree-free schematic · " + line2
    elif mode == "masked":
        line2 = "Building-masked aerial · " + line2
    else:
        line2 = "Aerial background · " + line2
    draw2.text((12, composed.height - bar_h + 10), line1, fill=(255, 255, 255))
    draw2.text((12, composed.height - bar_h + 32), line2, fill=(180, 200, 220))

    tw = int(composed.width * 1.55)
    th = int(composed.height * 1.55)
    composed = composed.resize((tw, th), Image.Resampling.LANCZOS)
    composed.thumbnail((1400, 1400), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    composed.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


async def fetch_dsm_array(
    api_key: str,
    lat: float,
    lng: float,
    radius_m: float = 40.0,
    pixel_size_m: float = 0.1,
) -> np.ndarray | None:
    params = {
        "location.latitude": lat,
        "location.longitude": lng,
        "radiusMeters": radius_m,
        "view": "FULL_LAYERS",
        "requiredQuality": "MEDIUM",
        "pixelSizeMeters": pixel_size_m,
        "key": api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            dl = None
            for quality in ("HIGH", "MEDIUM", "LOW"):
                params["requiredQuality"] = quality
                r = await client.get("https://solar.googleapis.com/v1/dataLayers:get", params=params)
                if r.status_code >= 400:
                    continue
                data = r.json()
                if data.get("dsmUrl"):
                    dl = data
                    break
            if not dl:
                return None
            url = dl["dsmUrl"]
            sep = "&" if "?" in url else "?"
            rr = await client.get(f"{url}{sep}key={api_key}")
            if rr.status_code >= 400:
                return None
            with tifffile.TiffFile(io.BytesIO(rr.content)) as tif:
                arr = tif.pages[0].asarray()
            if arr.ndim == 3:
                arr = arr[:, :, 0]
            return arr.astype(np.float32)
    except Exception:
        return None


async def build_georef_overlay_png(
    api_key: str,
    lat: float,
    lng: float,
    building_insights: dict[str, Any],
    recommended_panels: int,
    mode: str = "aerial",
) -> bytes:
    geo = await fetch_rgb_georaster(api_key, lat, lng, radius_m=40.0, pixel_size_m=0.1)
    mask = await fetch_mask_array(api_key, lat, lng, radius_m=40.0, pixel_size_m=0.1)
    dsm = await fetch_dsm_array(api_key, lat, lng, radius_m=40.0, pixel_size_m=0.1)
    if mask is not None and mask.shape[:2] != geo.arr.shape[:2]:
        mask = None
    if dsm is not None and dsm.shape[:2] != geo.arr.shape[:2]:
        dsm = None
    return render_roof_overlay_png(
        geo,
        building_insights,
        recommended_panels,
        mask=mask,
        mode=mode,
        dsm=dsm,
    )


async def build_plain_roof_png(api_key: str, lat: float, lng: float) -> bytes:
    geo = await fetch_rgb_georaster(api_key, lat, lng, radius_m=40.0, pixel_size_m=0.1)
    mask = await fetch_mask_array(api_key, lat, lng, radius_m=40.0, pixel_size_m=0.1)
    arr = geo.arr.copy()
    if mask is not None and mask.shape[:2] == arr.shape[:2]:
        out = arr.astype(np.float32)
        outside = ~mask
        out[outside] = out[outside] * 0.5 + np.array([18, 26, 38], dtype=np.float32) * 0.5
        img = Image.fromarray(out.astype(np.uint8))
    else:
        img = Image.fromarray(arr)
    img = img.resize((int(img.width * 1.35), int(img.height * 1.35)), Image.Resampling.LANCZOS)
    img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
