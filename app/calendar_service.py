"""Appointments + ICS calendar feed for Outlook (live.com) / Google Calendar.

Primary sync path (no OAuth):
  Subscribe once to  GET /api/calendar/feed.ics
  Outlook live.com and Google Calendar both support ICS subscription.

Optional later: Google Calendar API when google_token is configured.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any
from zoneinfo import ZoneInfo

from app import db

TN_TZ = ZoneInfo("America/New_York")  # Cleveland / east TN


def _dt_parse(value: str | None) -> datetime | None:
    if not value or not str(value).strip():
        return None
    s = str(value).strip()
    # common Retell / free-text patterns
    # ISO first
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TN_TZ)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TN_TZ)
        return dt.astimezone(TN_TZ)
    except ValueError:
        pass

    # "Tuesday 3pm", "tomorrow at 2:30 PM", "Aug 12 3:00pm"
    s2 = s.lower().replace(".", "")
    now = datetime.now(TN_TZ)

    m = re.search(
        r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(?P<year>\d{4}))?"
        r".*?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?",
        s2,
    )
    if m:
        mon_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        month = mon_map[m.group("mon")]
        day = int(m.group("day"))
        year = int(m.group("year") or now.year)
        hour = int(m.group("hour"))
        minute = int(m.group("minute") or 0)
        ampm = m.group("ampm")
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        try:
            return datetime(year, month, day, hour, minute, tzinfo=TN_TZ)
        except ValueError:
            return None

    m = re.search(r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)", s2)
    if m:
        hour = int(m.group("hour"))
        minute = int(m.group("minute") or 0)
        ampm = m.group("ampm")
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        day = now
        if "tomorrow" in s2:
            day = now + timedelta(days=1)
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    return None


def _ics_escape(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fmt_ics_dt(dt: datetime) -> str:
    """Floating local time with TZID=America/New_York."""
    local = dt.astimezone(TN_TZ)
    return local.strftime("%Y%m%dT%H%M%S")


def _fmt_ics_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics_event(appt: dict[str, Any], *, calendar_name: str = "SOL-RIGHT Appointments") -> str:
    uid = appt.get("uid") or f"{appt.get('id')}@sol-right.local"
    start = _dt_parse(appt.get("starts_at")) or datetime.now(TN_TZ) + timedelta(days=1)
    end = _dt_parse(appt.get("ends_at")) or (start + timedelta(minutes=int(appt.get("duration_minutes") or 30)))
    summary = appt.get("title") or "SOL-RIGHT consultation"
    desc_parts = [
        appt.get("description") or "",
        f"Customer: {appt.get('customer_name') or ''}",
        f"Phone: {appt.get('customer_phone') or ''}",
        f"Address: {appt.get('customer_address') or ''}",
        f"Lead ID: {appt.get('lead_id') or ''}",
        f"Source: {appt.get('source') or 'retell'}",
    ]
    description = "\\n".join(_ics_escape(p) for p in desc_parts if p)
    location = _ics_escape(appt.get("location") or appt.get("customer_address") or "Phone / video consult")
    status = "CONFIRMED" if (appt.get("status") or "scheduled") == "scheduled" else "CANCELLED"
    now = datetime.now(timezone.utc)

    return "\r\n".join(
        [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{_fmt_ics_utc(now)}",
            f"DTSTART;TZID=America/New_York:{_fmt_ics_dt(start)}",
            f"DTEND;TZID=America/New_York:{_fmt_ics_dt(end)}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{description}",
            f"LOCATION:{location}",
            f"STATUS:{status}",
            "END:VEVENT",
        ]
    )


def build_ics_calendar(appointments: list[dict[str, Any]], *, cal_name: str = "SOL-RIGHT Appointments") -> str:
    events = "\r\n".join(build_ics_event(a) for a in appointments)
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//SOL-RIGHT Solar//Lead Calendar//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            f"X-WR-CALNAME:{_ics_escape(cal_name)}",
            "X-WR-TIMEZONE:America/New_York",
            "BEGIN:VTIMEZONE",
            "TZID:America/New_York",
            "BEGIN:STANDARD",
            "DTSTART:20071104T020000",
            "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
            "TZOFFSETFROM:-0400",
            "TZOFFSETTO:-0500",
            "TZNAME:EST",
            "END:STANDARD",
            "BEGIN:DAYLIGHT",
            "DTSTART:20070311T020000",
            "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
            "TZOFFSETFROM:-0500",
            "TZOFFSETTO:-0400",
            "TZNAME:EDT",
            "END:DAYLIGHT",
            "END:VTIMEZONE",
            events,
            "END:VCALENDAR",
            "",
        ]
    )


async def create_appointment_from_writeback(
    db_path: str,
    *,
    lead: dict[str, Any],
    payload: dict[str, Any],
    owner_email: str,
    duration_minutes: int = 30,
) -> dict[str, Any] | None:
    """Create calendar appointment when Retell/phone agent books a meeting."""
    slot = payload.get("meeting_slot") or payload.get("meeting_time") or payload.get("preferred_time")
    if not slot and payload.get("call_status") != "meeting_booked":
        # still allow if qualified + notes mention a time
        notes = str(payload.get("phone_call_notes") or "")
        if "meeting" not in notes.lower() and "consult" not in notes.lower():
            return None
        slot = _dt_parse(notes)
        if not slot:
            return None
        slot = slot.isoformat()

    starts = _dt_parse(str(slot) if slot is not None else None)
    if not starts:
        # default: next business day 10:00 local if they said meeting booked without time
        if str(payload.get("call_status") or "").lower() in {"meeting_booked", "qualified"} or payload.get(
            "meeting_slot"
        ):
            now = datetime.now(TN_TZ)
            starts = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
            while starts.weekday() >= 5:
                starts += timedelta(days=1)
        else:
            return None

    ends = starts + timedelta(minutes=duration_minutes)
    customer = lead.get("name") or "Homeowner"
    title = f"SOL-RIGHT consult — {customer}"
    description = (
        f"Booked from AI phone call / website lead.\n"
        f"Intent: {lead.get('intent') or ''}\n"
        f"Bill: ${lead.get('monthly_bill_usd') or ''} · panels: {lead.get('recommended_panels') or ''}\n"
        f"Est. savings $/mo: {lead.get('estimated_monthly_savings_usd') or ''}\n"
        f"Score: {payload.get('lead_score') or lead.get('lead_score') or ''}\n"
        f"Notes: {payload.get('phone_call_notes') or payload.get('notes') or ''}\n"
        f"Owner notify: {owner_email}"
    )
    uid = f"lead-{lead.get('id')}-{uuid.uuid4().hex[:10]}@sol-right"

    appt_id = await db.create_appointment(
        db_path,
        {
            "uid": uid,
            "lead_id": lead.get("id"),
            "title": title,
            "description": description,
            "customer_name": customer,
            "customer_phone": lead.get("phone"),
            "customer_address": lead.get("address"),
            "customer_email": lead.get("email"),
            "location": "Phone / video consultation",
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            "duration_minutes": duration_minutes,
            "status": "scheduled",
            "source": payload.get("source") or "retell_writeback",
            "owner_email": owner_email,
            "retell_call_id": payload.get("call_id") or payload.get("voice_call_id"),
            "metadata_json": __import__("json").dumps(
                {
                    "qual_budget": payload.get("qual_budget"),
                    "qual_authority": payload.get("qual_authority"),
                    "qual_need": payload.get("qual_need"),
                    "qual_timeline": payload.get("qual_timeline"),
                    "lead_score": payload.get("lead_score"),
                    "raw_meeting_slot": payload.get("meeting_slot"),
                }
            ),
        },
    )
    appt = await db.get_appointment(db_path, appt_id)
    return appt


async def list_upcoming(db_path: str, days: int = 60) -> list[dict[str, Any]]:
    return await db.list_appointments(db_path, days=days)
