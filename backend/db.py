"""
SQLite data layer for the Leave Assistant demo.

One file (leave.db) replaces Redis (session drafts) + PostgreSQL (audit/history)
+ the mocked HRMS (employees/balances). Uses the stdlib sqlite3 only — no ORM.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from typing import Optional

DB_PATH = os.getenv("LEAVE_DB", os.path.join(os.path.dirname(__file__), "..", "leave.db"))

CODES = ["SICK", "CASUAL", "EARNED", "COMP_OFF"]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id TEXT PRIMARY KEY, name TEXT, dept TEXT, timezone TEXT
            );
            CREATE TABLE IF NOT EXISTS balances (
                employee_id TEXT, code TEXT, days REAL,
                PRIMARY KEY (employee_id, code)
            );
            CREATE TABLE IF NOT EXISTS leave_requests (
                id TEXT PRIMARY KEY, employee_id TEXT, code TEXT, label TEXT,
                start_date TEXT, end_date TEXT, duration_days REAL,
                comments TEXT, status TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, employee_id TEXT, draft_json TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT, message TEXT,
                parsed_json TEXT, validation_json TEXT, created_at TEXT
            );
            """
        )
        # seed once
        if c.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
            _seed(c)


def _seed(c: sqlite3.Connection) -> None:
    employees = [
        ("e1", "Asha Menon", "Engineering"),
        ("e2", "Ravi Kapoor", "Sales"),
        ("e3", "Meera Iyer", "Design"),
    ]
    balances = {
        "e1": {"SICK": 8, "CASUAL": 5, "EARNED": 12, "COMP_OFF": 2},
        "e2": {"SICK": 6, "CASUAL": 7, "EARNED": 9, "COMP_OFF": 1},
        "e3": {"SICK": 10, "CASUAL": 4, "EARNED": 14, "COMP_OFF": 3},
    }
    history = {
        "e1": [
            ("#AB-10391", "SICK", "Sick · 02 Jun", "Approved"),
            ("#AB-10355", "WFH", "WFH · 28 May", "Approved"),
            ("#AB-10310", "CASUAL", "Casual · 19 May", "Cancelled"),
        ],
        "e2": [("#AB-10288", "EARNED", "Earned · 10 May", "Approved")],
        "e3": [],
    }
    for eid, name, dept in employees:
        c.execute("INSERT INTO employees VALUES (?,?,?,?)", (eid, name, dept, "Asia/Kolkata"))
        for code, days in balances[eid].items():
            c.execute("INSERT INTO balances VALUES (?,?,?)", (eid, code, days))
        # seed history with descending timestamps so order is stable
        base = dt.datetime(2026, 6, 1, 9, 0, 0)
        for i, (rid, code, label, status) in enumerate(history[eid]):
            ts = (base - dt.timedelta(days=i)).isoformat()
            c.execute(
                "INSERT INTO leave_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, eid, code, label, None, None, None, "", status, ts),
            )


# ---- reads -----------------------------------------------------------------

def get_employees() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id, name, dept FROM employees ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_balances(employee_id: str) -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT code, days FROM balances WHERE employee_id = ?", (employee_id,)
        ).fetchall()
    return {r["code"]: r["days"] for r in rows}


def get_history(employee_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, code, label, status FROM leave_requests "
            "WHERE employee_id = ? ORDER BY created_at DESC",
            (employee_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---- session drafts (replaces Redis) ---------------------------------------

def save_draft(session_id: str, employee_id: str, draft: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?)",
            (session_id, employee_id, json.dumps(draft), dt.datetime.now().isoformat()),
        )


def get_draft(session_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT employee_id, draft_json FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if not row:
        return None
    d = json.loads(row["draft_json"])
    d["employee_id"] = row["employee_id"]
    return d


def delete_draft(session_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


# ---- writes ----------------------------------------------------------------

def insert_request(req: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO leave_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                req["id"], req["employee_id"], req["code"], req["label"],
                req.get("start_date"), req.get("end_date"), req.get("duration_days"),
                req.get("comments", ""), req["status"], dt.datetime.now().isoformat(),
            ),
        )


def decrement_balance(employee_id: str, code: str, days: float) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE balances SET days = days - ? WHERE employee_id = ? AND code = ?",
            (days, employee_id, code),
        )


def credit_balance(employee_id: str, code: str, days: float) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE balances SET days = days + ? WHERE employee_id = ? AND code = ?",
            (days, employee_id, code),
        )


def get_request(req_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM leave_requests WHERE id = ?", (req_id,)).fetchone()
    return dict(row) if row else None


def set_request_status(req_id: str, status: str) -> None:
    with _conn() as c:
        c.execute("UPDATE leave_requests SET status = ? WHERE id = ?", (status, req_id))


def write_audit(employee_id: str, message: str, parsed: dict, validation: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO audit (employee_id, message, parsed_json, validation_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (employee_id, message, json.dumps(parsed), json.dumps(validation),
             dt.datetime.now().isoformat()),
        )
