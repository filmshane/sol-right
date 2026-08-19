from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT,
    address TEXT,
    intent TEXT,
    monthly_bill_usd REAL,
    monthly_usage_kwh REAL,
    usd_per_kwh REAL,
    target_offset_pct REAL,
    lat REAL,
    lng REAL,
    estimated_annual_kwh REAL,
    estimated_monthly_kwh REAL,
    max_panels INTEGER,
    recommended_panels INTEGER,
    system_size_kw REAL,
    quote_confidence INTEGER,
    property_ownership TEXT,
    hoa_restrictions TEXT,
    roof_material TEXT,
    roof_age_condition TEXT,
    shading_notes TEXT,
    service_panel_amps TEXT,
    large_loads TEXT,
    battery_interest TEXT,
    financing_preference TEXT,
    timeline TEXT,
    future_usage_plans TEXT,
    notes TEXT,
    chat_session_id TEXT NOT NULL,
    source TEXT DEFAULT 'website-chat',
    estimated_monthly_savings_usd REAL,
    estimated_yearly_savings_usd REAL,
    estimated_10yr_savings_usd REAL,
    ai_call_consent INTEGER DEFAULT 0,
    ai_call_status TEXT DEFAULT 'none',
    ai_call_requested_at TEXT,
    ai_call_window TEXT,
    callback_priority INTEGER DEFAULT 0,
    consent_text TEXT,
    consent_verbatim TEXT,
    consent_recorded_at TEXT,
    chat_transcript_json TEXT,
    chat_metadata_json TEXT,
    dnc_status TEXT DEFAULT 'unknown',
    dnc_checked_at TEXT,
    dnc_lists TEXT,
    voice_dispatch_status TEXT DEFAULT 'none',
    voice_dispatched_at TEXT,
    voice_call_id TEXT,
    voice_dispatch_error TEXT,
    lead_score INTEGER,
    qual_budget TEXT,
    qual_authority TEXT,
    qual_need TEXT,
    qual_timeline TEXT,
    qual_decision_process TEXT,
    qual_metrics TEXT,
    meeting_booked_at TEXT,
    meeting_slot TEXT,
    human_transfer INTEGER DEFAULT 0,
    phone_call_result TEXT,
    phone_call_notes TEXT,
    phone_call_results_json TEXT
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    web_id TEXT NOT NULL,
    visitor_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    messages_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ai_call_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    phone TEXT NOT NULL,
    name TEXT NOT NULL,
    address TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 10,
    call_window TEXT DEFAULT 'immediate',
    purpose TEXT DEFAULT 'savings + install cost + qualification',
    context_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    claimed_at TEXT,
    completed_at TEXT,
    result_notes TEXT,
    voice_call_id TEXT,
    voice_dispatch_status TEXT DEFAULT 'pending',
    FOREIGN KEY(lead_id) REFERENCES leads(id)
);

CREATE TABLE IF NOT EXISTS dnc_numbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_e164 TEXT NOT NULL UNIQUE,
    phone_display TEXT,
    source TEXT NOT NULL,
    list_name TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    lead_id INTEGER,
    title TEXT NOT NULL,
    description TEXT,
    customer_name TEXT,
    customer_phone TEXT,
    customer_address TEXT,
    customer_email TEXT,
    location TEXT,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    duration_minutes INTEGER DEFAULT 30,
    status TEXT NOT NULL DEFAULT 'scheduled',
    source TEXT DEFAULT 'retell_writeback',
    owner_email TEXT,
    retell_call_id TEXT,
    metadata_json TEXT,
    FOREIGN KEY(lead_id) REFERENCES leads(id)
);
"""

LEAD_EXTRA_COLS = {
    "monthly_usage_kwh": "REAL",
    "usd_per_kwh": "REAL",
    "target_offset_pct": "REAL",
    "system_size_kw": "REAL",
    "quote_confidence": "INTEGER",
    "property_ownership": "TEXT",
    "hoa_restrictions": "TEXT",
    "roof_material": "TEXT",
    "roof_age_condition": "TEXT",
    "shading_notes": "TEXT",
    "service_panel_amps": "TEXT",
    "large_loads": "TEXT",
    "battery_interest": "TEXT",
    "financing_preference": "TEXT",
    "timeline": "TEXT",
    "future_usage_plans": "TEXT",
    "estimated_monthly_savings_usd": "REAL",
    "estimated_yearly_savings_usd": "REAL",
    "estimated_10yr_savings_usd": "REAL",
    "ai_call_consent": "INTEGER DEFAULT 0",
    "ai_call_status": "TEXT DEFAULT 'none'",
    "ai_call_requested_at": "TEXT",
    "ai_call_window": "TEXT",
    "callback_priority": "INTEGER DEFAULT 0",
    "intent": "TEXT",
    "consent_text": "TEXT",
    "consent_verbatim": "TEXT",
    "consent_recorded_at": "TEXT",
    "chat_transcript_json": "TEXT",
    "chat_metadata_json": "TEXT",
    "dnc_status": "TEXT DEFAULT 'unknown'",
    "dnc_checked_at": "TEXT",
    "dnc_lists": "TEXT",
    "voice_dispatch_status": "TEXT DEFAULT 'none'",
    "voice_dispatched_at": "TEXT",
    "voice_call_id": "TEXT",
    "voice_dispatch_error": "TEXT",
    "lead_score": "INTEGER",
    "qual_budget": "TEXT",
    "qual_authority": "TEXT",
    "qual_need": "TEXT",
    "qual_timeline": "TEXT",
    "qual_decision_process": "TEXT",
    "qual_metrics": "TEXT",
    "meeting_booked_at": "TEXT",
    "meeting_slot": "TEXT",
    "human_transfer": "INTEGER DEFAULT 0",
    "phone_call_result": "TEXT",
    "phone_call_notes": "TEXT",
    "phone_call_results_json": "TEXT",
}

SESSION_EXTRA_COLS = {
    "metadata_json": "TEXT DEFAULT '{}'",
}

QUEUE_EXTRA_COLS = {
    "voice_call_id": "TEXT",
    "voice_dispatch_status": "TEXT DEFAULT 'pending'",
}


async def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        await conn.executescript(SCHEMA)
        for table, cols in (
            ("leads", LEAD_EXTRA_COLS),
            ("chat_sessions", SESSION_EXTRA_COLS),
            ("ai_call_queue", QUEUE_EXTRA_COLS),
        ):
            cur = await conn.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in await cur.fetchall()}
            for col, typ in cols.items():
                if col not in existing:
                    await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ai_call_queue_status ON ai_call_queue(status, priority DESC, id ASC)"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone)")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_ai_call ON leads(ai_call_status, ai_call_consent)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dnc_phone ON dnc_numbers(phone_e164, active)"
        )
        await conn.commit()


async def upsert_session(
    db_path: str,
    session_id: str,
    web_id: str,
    visitor_name: str | None,
    messages_json: str,
    metadata_json: str | None = None,
) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO chat_sessions (session_id, web_id, visitor_name, messages_json, metadata_json)
            VALUES (?, ?, ?, ?, COALESCE(?, '{}'))
            ON CONFLICT(session_id) DO UPDATE SET
                visitor_name = COALESCE(excluded.visitor_name, chat_sessions.visitor_name),
                messages_json = excluded.messages_json,
                metadata_json = COALESCE(excluded.metadata_json, chat_sessions.metadata_json),
                updated_at = datetime('now')
            """,
            (session_id, web_id, visitor_name, messages_json, metadata_json),
        )
        await conn.commit()


async def get_session(db_path: str, session_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def create_lead(db_path: str, fields: dict[str, Any]) -> int:
    cols = [
        "name",
        "phone",
        "email",
        "address",
        "intent",
        "monthly_bill_usd",
        "monthly_usage_kwh",
        "usd_per_kwh",
        "target_offset_pct",
        "lat",
        "lng",
        "estimated_annual_kwh",
        "estimated_monthly_kwh",
        "max_panels",
        "recommended_panels",
        "system_size_kw",
        "quote_confidence",
        "property_ownership",
        "hoa_restrictions",
        "roof_material",
        "roof_age_condition",
        "shading_notes",
        "service_panel_amps",
        "large_loads",
        "battery_interest",
        "financing_preference",
        "timeline",
        "future_usage_plans",
        "notes",
        "chat_session_id",
        "source",
        "estimated_monthly_savings_usd",
        "estimated_yearly_savings_usd",
        "estimated_10yr_savings_usd",
        "ai_call_consent",
        "ai_call_status",
        "ai_call_requested_at",
        "ai_call_window",
        "callback_priority",
        "consent_text",
        "consent_verbatim",
        "consent_recorded_at",
        "chat_transcript_json",
        "chat_metadata_json",
        "dnc_status",
        "dnc_checked_at",
        "dnc_lists",
        "voice_dispatch_status",
        "voice_dispatched_at",
        "voice_call_id",
        "voice_dispatch_error",
        "lead_score",
        "qual_budget",
        "qual_authority",
        "qual_need",
        "qual_timeline",
        "qual_decision_process",
        "qual_metrics",
        "meeting_booked_at",
        "meeting_slot",
        "human_transfer",
        "phone_call_result",
        "phone_call_notes",
        "phone_call_results_json",
    ]
    values = [fields.get(c) for c in cols]
    placeholders = ",".join("?" for _ in cols)
    col_sql = ",".join(cols)
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            f"INSERT INTO leads ({col_sql}) VALUES ({placeholders})",
            values,
        )
        await conn.commit()
        return int(cur.lastrowid)


async def update_lead(db_path: str, lead_id: int, fields: dict[str, Any]) -> bool:
    if not fields:
        return False
    cols = []
    vals = []
    for k, v in fields.items():
        cols.append(f"{k} = ?")
        vals.append(v)
    vals.append(lead_id)
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            f"UPDATE leads SET {', '.join(cols)} WHERE id = ?",
            vals,
        )
        await conn.commit()
        return cur.rowcount > 0


async def get_lead(db_path: str, lead_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def enqueue_ai_call(
    db_path: str,
    *,
    lead_id: int,
    phone: str,
    name: str,
    address: str | None,
    context_json: str | None,
    priority: int = 50,
    call_window: str = "immediate",
    purpose: str = "savings + install cost + qualification",
) -> int:
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            """
            INSERT INTO ai_call_queue
              (lead_id, phone, name, address, status, priority, call_window, purpose, context_json, voice_dispatch_status)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, 'pending')
            """,
            (lead_id, phone, name, address, priority, call_window, purpose, context_json),
        )
        await conn.execute(
            """
            UPDATE leads
               SET ai_call_consent = 1,
                   ai_call_status = 'queued',
                   ai_call_requested_at = datetime('now'),
                   ai_call_window = ?,
                   callback_priority = ?
             WHERE id = ?
            """,
            (call_window, priority, lead_id),
        )
        await conn.commit()
        return int(cur.lastrowid)


async def mark_voice_dispatch(
    db_path: str,
    *,
    lead_id: int,
    queue_id: int | None,
    status: str,
    voice_call_id: str | None = None,
    error: str | None = None,
) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            UPDATE leads
               SET voice_dispatch_status = ?,
                   voice_dispatched_at = datetime('now'),
                   voice_call_id = COALESCE(?, voice_call_id),
                   voice_dispatch_error = ?,
                   ai_call_status = CASE
                       WHEN ? IN ('dispatched','ringing','in_progress') THEN 'dispatched'
                       WHEN ? = 'blocked_dnc' THEN 'blocked_dnc'
                       WHEN ? LIKE 'failed%' THEN 'dispatch_failed'
                       ELSE ai_call_status
                   END
             WHERE id = ?
            """,
            (status, voice_call_id, error, status, status, status, lead_id),
        )
        if queue_id is not None:
            await conn.execute(
                """
                UPDATE ai_call_queue
                   SET voice_dispatch_status = ?,
                       voice_call_id = COALESCE(?, voice_call_id),
                       status = CASE
                           WHEN ? IN ('dispatched','ringing','in_progress') THEN 'dispatched'
                           WHEN ? = 'blocked_dnc' THEN 'blocked_dnc'
                           WHEN ? LIKE 'failed%' THEN 'dispatch_failed'
                           ELSE status
                       END
                 WHERE id = ?
                """,
                (status, voice_call_id, status, status, status, queue_id),
            )
        await conn.commit()


async def list_leads(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]


async def list_pending_ai_calls(db_path: str, limit: int = 25) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT q.*,
                   l.monthly_bill_usd,
                   l.recommended_panels,
                   l.system_size_kw,
                   l.estimated_annual_kwh,
                   l.estimated_monthly_savings_usd,
                   l.estimated_yearly_savings_usd,
                   l.chat_session_id,
                   l.intent,
                   l.consent_verbatim,
                   l.dnc_status,
                   l.chat_transcript_json,
                   l.lead_score
              FROM ai_call_queue q
              JOIN leads l ON l.id = q.lead_id
             WHERE q.status IN ('pending', 'dispatched')
             ORDER BY q.priority DESC, q.id ASC
             LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def claim_next_ai_call(db_path: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute(
            """
            SELECT q.*,
                   l.monthly_bill_usd,
                   l.recommended_panels,
                   l.system_size_kw,
                   l.estimated_annual_kwh,
                   l.estimated_monthly_savings_usd,
                   l.estimated_yearly_savings_usd,
                   l.estimated_10yr_savings_usd,
                   l.address AS lead_address,
                   l.notes AS lead_notes,
                   l.chat_session_id,
                   l.intent,
                   l.consent_text,
                   l.consent_verbatim,
                   l.chat_transcript_json,
                   l.chat_metadata_json,
                   l.dnc_status
              FROM ai_call_queue q
              JOIN leads l ON l.id = q.lead_id
             WHERE q.status IN ('pending', 'dispatched')
               AND COALESCE(l.dnc_status, 'unknown') != 'listed'
             ORDER BY q.priority DESC, q.id ASC
             LIMIT 1
            """
        )
        row = await cur.fetchone()
        if not row:
            await conn.execute("COMMIT")
            return None
        qid = int(row["id"])
        lead_id = int(row["lead_id"])
        await conn.execute(
            """
            UPDATE ai_call_queue
               SET status = 'in_progress', claimed_at = datetime('now')
             WHERE id = ?
            """,
            (qid,),
        )
        await conn.execute(
            "UPDATE leads SET ai_call_status = 'in_progress' WHERE id = ?",
            (lead_id,),
        )
        await conn.commit()
        return dict(row)


async def complete_ai_call(
    db_path: str,
    queue_id: int,
    *,
    status: str = "completed",
    result_notes: str | None = None,
) -> bool:
    if status not in ("completed", "failed", "no_answer", "cancelled", "transferred", "meeting_booked"):
        status = "completed"
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT lead_id FROM ai_call_queue WHERE id = ?", (queue_id,)
        )
        row = await cur.fetchone()
        if not row:
            return False
        lead_id = int(row["lead_id"])
        await conn.execute(
            """
            UPDATE ai_call_queue
               SET status = ?, completed_at = datetime('now'), result_notes = ?
             WHERE id = ?
            """,
            (status, result_notes, queue_id),
        )
        await conn.execute(
            "UPDATE leads SET ai_call_status = ? WHERE id = ?",
            (status, lead_id),
        )
        await conn.commit()
        return True


async def dnc_lookup(db_path: str, phone_e164: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT * FROM dnc_numbers
             WHERE phone_e164 = ? AND active = 1
            """,
            (phone_e164,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def dnc_add(
    db_path: str,
    *,
    phone_e164: str,
    phone_display: str | None,
    source: str,
    list_name: str,
    reason: str | None = None,
) -> int:
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            """
            INSERT INTO dnc_numbers (phone_e164, phone_display, source, list_name, reason, active)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(phone_e164) DO UPDATE SET
                active = 1,
                source = excluded.source,
                list_name = excluded.list_name,
                reason = excluded.reason,
                phone_display = COALESCE(excluded.phone_display, dnc_numbers.phone_display)
            """,
            (phone_e164, phone_display, source, list_name, reason),
        )
        await conn.commit()
        return int(cur.lastrowid or 0)


async def create_appointment(db_path: str, fields: dict[str, Any]) -> int:
    cols = [
        "uid",
        "lead_id",
        "title",
        "description",
        "customer_name",
        "customer_phone",
        "customer_address",
        "customer_email",
        "location",
        "starts_at",
        "ends_at",
        "duration_minutes",
        "status",
        "source",
        "owner_email",
        "retell_call_id",
        "metadata_json",
    ]
    values = [fields.get(c) for c in cols]
    placeholders = ",".join("?" for _ in cols)
    col_sql = ",".join(cols)
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            f"INSERT INTO appointments ({col_sql}) VALUES ({placeholders})",
            values,
        )
        await conn.commit()
        return int(cur.lastrowid)


async def get_appointment(db_path: str, appt_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_appointments(db_path: str, days: int = 90, include_cancelled: bool = False) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        if include_cancelled:
            cur = await conn.execute(
                """
                SELECT * FROM appointments
                 WHERE datetime(starts_at) >= datetime('now', '-1 day')
                   AND datetime(starts_at) <= datetime('now', ?)
                 ORDER BY starts_at ASC
                """,
                (f"+{int(days)} day",),
            )
        else:
            cur = await conn.execute(
                """
                SELECT * FROM appointments
                 WHERE status != 'cancelled'
                   AND datetime(starts_at) >= datetime('now', '-1 day')
                   AND datetime(starts_at) <= datetime('now', ?)
                 ORDER BY starts_at ASC
                """,
                (f"+{int(days)} day",),
            )
        return [dict(r) for r in await cur.fetchall()]


async def list_all_appointments_for_feed(db_path: str, days_back: int = 7, days_forward: int = 180) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT * FROM appointments
             WHERE datetime(starts_at) >= datetime('now', ?)
               AND datetime(starts_at) <= datetime('now', ?)
             ORDER BY starts_at ASC
            """,
            (f"-{int(days_back)} day", f"+{int(days_forward)} day"),
        )
        return [dict(r) for r in await cur.fetchall()]


async def cancel_appointment(db_path: str, appt_id: int) -> bool:
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            """
            UPDATE appointments
               SET status = 'cancelled', updated_at = datetime('now')
             WHERE id = ?
            """,
            (appt_id,),
        )
        await conn.commit()
        return cur.rowcount > 0
