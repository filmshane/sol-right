"""DNC checks — National + Tennessee internal lists.

Production: wire National DNC / TN DNC vendor APIs via env.
Until then: local SQLite dnc_numbers + format/validation rules.
Never place outbound AI calls when listed or when consent is missing.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app import db


def to_e164_us(phone: str) -> str | None:
    digits = re.sub(r"\D+", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"+1{digits}"


def display_phone(phone: str) -> str:
    e164 = to_e164_us(phone)
    if not e164:
        return (phone or "").strip()
    d = e164[2:]
    return f"({d[0:3]}) {d[3:6]}-{d[6:10]}"


async def check_dnc(db_path: str, phone: str) -> dict[str, Any]:
    """Return {ok_to_call, status, lists[], e164, checked_at}."""
    e164 = to_e164_us(phone)
    checked_at = datetime.now(timezone.utc).isoformat()
    if not e164:
        return {
            "ok_to_call": False,
            "status": "invalid_phone",
            "lists": [],
            "e164": None,
            "checked_at": checked_at,
            "message": "Phone must be a valid 10-digit US number",
        }

    hits = await db.dnc_lookup(db_path, e164)
    list_names = sorted({h.get("list_name") or h.get("source") or "local" for h in hits})

    # Placeholder hooks for external DNC providers (disabled until keys configured)
    # NATIONAL_DNC_API / TN_DNC_API would append to hits here.

    if hits:
        return {
            "ok_to_call": False,
            "status": "listed",
            "lists": list_names,
            "e164": e164,
            "checked_at": checked_at,
            "hits": hits,
            "message": f"Number is on DNC list(s): {', '.join(list_names)}. No outbound AI call.",
        }

    return {
        "ok_to_call": True,
        "status": "clear",
        "lists": [],
        "e164": e164,
        "checked_at": checked_at,
        "message": "Not found on local National/TN DNC store (vendor APIs optional).",
    }


async def add_opt_out(
    db_path: str,
    phone: str,
    *,
    reason: str = "caller_opt_out",
    list_name: str = "company_internal_dnc",
) -> dict[str, Any]:
    e164 = to_e164_us(phone)
    if not e164:
        return {"ok": False, "error": "invalid phone"}
    await db.dnc_add(
        db_path,
        phone_e164=e164,
        phone_display=display_phone(phone),
        source="opt_out",
        list_name=list_name,
        reason=reason,
    )
    return {"ok": True, "e164": e164, "list_name": list_name}
