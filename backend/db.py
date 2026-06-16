"""
SQLite data layer for the Leave Assistant demo.

One file (leave.db) replaces Redis (session drafts) + PostgreSQL (audit/history)
+ the mocked HRMS (employees/balances). Uses the stdlib sqlite3 only — no ORM.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
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
                id TEXT PRIMARY KEY, name TEXT, dept TEXT, timezone TEXT,
                role TEXT DEFAULT 'Employee', email TEXT
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
            CREATE TABLE IF NOT EXISTS credentials (
                username TEXT PRIMARY KEY, password_hash TEXT, employee_id TEXT
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token TEXT PRIMARY KEY, employee_id TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS email_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, to_email TEXT, subject TEXT,
                body TEXT, status TEXT, created_at TEXT
            );
            """
        )
        # migrate an older leave.db that predates the role / email columns
        cols = [r["name"] for r in c.execute("PRAGMA table_info(employees)")]
        if "role" not in cols:
            c.execute("ALTER TABLE employees ADD COLUMN role TEXT DEFAULT 'Employee'")
        if "email" not in cols:
            c.execute("ALTER TABLE employees ADD COLUMN email TEXT")

        # idempotent: ensure every demo employee, balance, and login exists.
        # INSERT OR IGNORE adds new users to an existing leave.db without
        # touching current data (e.g. drawn-down balances stay as they are).
        for eid, name, dept, role, email in EMPLOYEES:
            c.execute("INSERT OR IGNORE INTO employees (id, name, dept, timezone, role, email) VALUES (?,?,?,?,?,?)",
                      (eid, name, dept, "Asia/Kolkata", role, email))
            # keep the seeded role/email authoritative even on an older leave.db
            c.execute("UPDATE employees SET role = ?, email = ? WHERE id = ?", (role, email, eid))
            for code, days in BALANCES[eid].items():
                c.execute("INSERT OR IGNORE INTO balances VALUES (?,?,?)", (eid, code, days))
        for username, pw, eid in CREDS:
            c.execute("INSERT OR IGNORE INTO credentials VALUES (?,?,?)", (username, _hash(pw), eid))
        # sample request history only on a brand-new database
        if c.execute("SELECT COUNT(*) FROM leave_requests").fetchone()[0] == 0:
            _seed_history(c)

        # managers can no longer apply, so cancel any pending requests they
        # authored before that rule — keeps them out of approval queues
        c.execute(
            "UPDATE leave_requests SET status = 'Cancelled' "
            "WHERE status = 'Pending' AND employee_id IN "
            "(SELECT id FROM employees WHERE role = 'Manager')"
        )


def _hash(password: str) -> str:
    return hashlib.sha256(("leave-demo::" + password).encode()).hexdigest()


# Demo roster (id, name, dept, role, email). Usernames are stored lowercase;
# verify_login() lowercases input. Emails are placeholders — change them (or
# sign up with a real address) to receive real notifications via SMTP.
EMPLOYEES = [
    ("e1", "Asha Menon", "Engineering", "Manager", "asha@example.com"),
    ("e2", "Ravi Kapoor", "Sales", "Employee", "ravi@example.com"),
    ("e3", "Meera Iyer", "Design", "Employee", "meera@example.com"),
    ("e4", "Prakash Bagsariya", "Developer", "Employee", "prakash@example.com"),
    ("e5", "Krupal Tasare", "Engineer", "Employee", "krupal@example.com"),
]

# Starting allotment granted to a freshly signed-up account.
DEFAULT_NEW_BALANCES = {"SICK": 10, "CASUAL": 8, "EARNED": 15, "COMP_OFF": 4}
BALANCES = {
    "e1": {"SICK": 8, "CASUAL": 5, "EARNED": 12, "COMP_OFF": 2},
    "e2": {"SICK": 6, "CASUAL": 7, "EARNED": 9, "COMP_OFF": 1},
    "e3": {"SICK": 10, "CASUAL": 4, "EARNED": 14, "COMP_OFF": 3},
    "e4": {"SICK": 8, "CASUAL": 6, "EARNED": 12, "COMP_OFF": 2},
    "e5": {"SICK": 9, "CASUAL": 5, "EARNED": 11, "COMP_OFF": 3},
}
CREDS = [
    ("asha", "asha123", "e1"),
    ("ravi", "ravi123", "e2"),
    ("meera", "meera123", "e3"),
    ("prakash", "prakash123", "e4"),
    ("krupal", "krupal123", "e5"),
]


def _seed_history(c: sqlite3.Connection) -> None:
    history = {
        "e1": [
            ("#AB-10391", "SICK", "Sick · 02 Jun", "Approved"),
            ("#AB-10355", "WFH", "WFH · 28 May", "Approved"),
            ("#AB-10310", "CASUAL", "Casual · 19 May", "Cancelled"),
        ],
        "e2": [("#AB-10288", "EARNED", "Earned · 10 May", "Approved")],
    }
    base = dt.datetime(2026, 6, 1, 9, 0, 0)
    for eid, rows in history.items():
        for i, (rid, code, label, status) in enumerate(rows):
            ts = (base - dt.timedelta(days=i)).isoformat()
            c.execute(
                "INSERT INTO leave_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, eid, code, label, None, None, None, "", status, ts),
            )


# ---- reads -----------------------------------------------------------------

def get_employees() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id, name, dept, role, email FROM employees ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_employee(employee_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT id, name, dept, role, email FROM employees WHERE id = ?", (employee_id,)).fetchone()
    return dict(row) if row else None


def get_managers() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id, name, email FROM employees WHERE role = 'Manager'").fetchall()
    return [dict(r) for r in rows]


def log_email(to_email: str, subject: str, body: str, status: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO email_outbox (to_email, subject, body, status, created_at) VALUES (?,?,?,?,?)",
            (to_email, subject, body, status, dt.datetime.now().isoformat()),
        )


# ---- auth -------------------------------------------------------------------

def username_exists(username: str) -> bool:
    with _conn() as c:
        return c.execute(
            "SELECT 1 FROM credentials WHERE username = ?", (username.strip().lower(),)
        ).fetchone() is not None


def create_account(username: str, password: str, name: str, dept: str, role: str, email: str = "") -> str:
    """Create an employee + balances + login. Returns the new employee_id."""
    username = username.strip().lower()
    eid = "u" + secrets.token_hex(4)
    with _conn() as c:
        c.execute(
            "INSERT INTO employees (id, name, dept, timezone, role, email) VALUES (?,?,?,?,?,?)",
            (eid, name.strip(), dept.strip() or "—", "Asia/Kolkata", role, email.strip()),
        )
        for code, days in DEFAULT_NEW_BALANCES.items():
            c.execute("INSERT INTO balances VALUES (?,?,?)", (eid, code, days))
        c.execute("INSERT INTO credentials VALUES (?,?,?)", (username, _hash(password), eid))
    return eid


def verify_login(username: str, password: str) -> Optional[str]:
    """Return the employee_id for valid credentials, else None."""
    with _conn() as c:
        row = c.execute(
            "SELECT employee_id, password_hash FROM credentials WHERE username = ?",
            (username.strip().lower(),),
        ).fetchone()
    if row and row["password_hash"] == _hash(password):
        return row["employee_id"]
    return None


def create_session(token: str, employee_id: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO auth_sessions VALUES (?,?,?)",
            (token, employee_id, dt.datetime.now().isoformat()),
        )


def session_employee(token: str) -> Optional[str]:
    with _conn() as c:
        row = c.execute(
            "SELECT employee_id FROM auth_sessions WHERE token = ?", (token,)
        ).fetchone()
    return row["employee_id"] if row else None


def delete_session(token: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))


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


def get_pending_requests(exclude_employee: Optional[str] = None) -> list[dict]:
    """All pending leave requests joined with the requester, newest first.
    Optionally excludes one employee (so a manager doesn't approve their own)."""
    q = (
        "SELECT r.id, r.employee_id, e.name AS employee_name, e.dept AS dept, "
        "       r.code, r.label, r.duration_days, r.created_at "
        "FROM leave_requests r JOIN employees e ON e.id = r.employee_id "
        "WHERE r.status = 'Pending'"
    )
    params: list = []
    if exclude_employee:
        q += " AND r.employee_id != ?"
        params.append(exclude_employee)
    q += " ORDER BY r.created_at DESC"
    with _conn() as c:
        rows = c.execute(q, params).fetchall()
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
