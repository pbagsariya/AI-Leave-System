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
            CREATE TABLE IF NOT EXISTS password_resets (
                token TEXT PRIMARY KEY, employee_id TEXT, created_at TEXT
            );
            """
        )
        # migrate an older leave.db that predates the role / email columns
        cols = [r["name"] for r in c.execute("PRAGMA table_info(employees)")]
        if "role" not in cols:
            c.execute("ALTER TABLE employees ADD COLUMN role TEXT DEFAULT 'Employee'")
        if "email" not in cols:
            c.execute("ALTER TABLE employees ADD COLUMN email TEXT")
        lr_cols = [r["name"] for r in c.execute("PRAGMA table_info(leave_requests)")]
        if "decision_comment" not in lr_cols:
            c.execute("ALTER TABLE leave_requests ADD COLUMN decision_comment TEXT")

        # idempotent: ensure every demo employee, balance, and login exists.
        # INSERT OR IGNORE adds new users to an existing leave.db without
        # touching current data (e.g. drawn-down balances stay as they are).
        for eid, name, dept, role, email in EMPLOYEES:
            c.execute("INSERT OR IGNORE INTO employees (id, name, dept, timezone, role, email) VALUES (?,?,?,?,?,?)",
                      (eid, name, dept, "Asia/Kolkata", role, email))
            # keep the seeded dept/role/email authoritative even on an older leave.db
            c.execute("UPDATE employees SET dept = ?, role = ?, email = ? WHERE id = ?",
                      (dept, role, email, eid))
            for code, days in BALANCES[eid].items():
                c.execute("INSERT OR IGNORE INTO balances VALUES (?,?,?)", (eid, code, days))
        for username, pw, eid in CREDS:
            # authoritative: keep seeded passwords/ids in sync even if they change
            c.execute("INSERT OR REPLACE INTO credentials (username, password_hash, employee_id) VALUES (?,?,?)",
                      (username, _hash(pw), eid))
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
# Leave approvals route by DEPARTMENT (the `dept` field): an employee's request
# goes to the manager(s) in the same department. Roles must be exactly "Manager"
# or "Employee". Seeded org:
#   Team 1 — pmanager1 (mgr); Meera, pemployee1
#   Team 2 — pmanager2 (mgr); pemployee2
EMPLOYEES = [
    ("e1", "pmanager1", "IT", "Manager", "prakashatinfo@gmail.com"),
    ("e2", "pmanager2", "R&D", "Manager", "prakash.bagsariya@gmail.com"),
    ("e3", "pemployee1", "IT", "Employee", "prpri2007@gmail.com"),
    ("e4", "pemployee2", "R&D", "Employee", "bagsariya.prakash@gmail.com"),
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
    ("pmanager1", "pmanager123", "e1"),
    ("pmanager2", "pmanager456", "e2"),
    ("pemployee1", "pemployee123", "e3"),
    ("pemployee2", "pemployee456", "e4"),
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
                "INSERT INTO leave_requests "
                "(id, employee_id, code, label, start_date, end_date, duration_days, comments, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
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


def get_managers_for_employee(employee_id: str) -> list[dict]:
    """Managers in the same department as the employee (their approvers)."""
    with _conn() as c:
        erow = c.execute("SELECT dept FROM employees WHERE id = ?", (employee_id,)).fetchone()
        dept = erow["dept"] if erow else None
        rows = c.execute(
            "SELECT id, name, email FROM employees "
            "WHERE role = 'Manager' AND dept = ? AND id != ?",
            (dept, employee_id),
        ).fetchall()
    return [dict(r) for r in rows]


def same_department(emp_a: str, emp_b: str) -> bool:
    with _conn() as c:
        rows = c.execute(
            "SELECT dept FROM employees WHERE id IN (?, ?)", (emp_a, emp_b)
        ).fetchall()
    depts = {r["dept"] for r in rows}
    return len(rows) == 2 and len(depts) == 1


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


# ---- password reset --------------------------------------------------------

def account_for_reset(identifier: str) -> Optional[dict]:
    """Find an account by username OR email (for the forgot-password flow)."""
    ident = identifier.strip()
    with _conn() as c:
        row = c.execute(
            "SELECT e.id, e.name, e.email FROM employees e "
            "JOIN credentials c ON c.employee_id = e.id "
            "WHERE c.username = ? OR LOWER(e.email) = LOWER(?) LIMIT 1",
            (ident.lower(), ident),
        ).fetchone()
    return dict(row) if row else None


def create_reset_token(employee_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with _conn() as c:
        c.execute("DELETE FROM password_resets WHERE employee_id = ?", (employee_id,))
        c.execute("INSERT INTO password_resets VALUES (?,?,?)",
                  (token, employee_id, dt.datetime.now().isoformat()))
    return token


def reset_token_employee(token: str, max_age_minutes: int = 60) -> Optional[str]:
    """Return the employee_id for a valid, unexpired reset token, else None."""
    if not token:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT employee_id, created_at FROM password_resets WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        return None
    try:
        created = dt.datetime.fromisoformat(row["created_at"])
    except ValueError:
        return None
    if dt.datetime.now() - created > dt.timedelta(minutes=max_age_minutes):
        return None
    return row["employee_id"]


def delete_reset_token(token: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM password_resets WHERE token = ?", (token,))


def update_password(employee_id: str, new_password: str) -> None:
    with _conn() as c:
        c.execute("UPDATE credentials SET password_hash = ? WHERE employee_id = ?",
                  (_hash(new_password), employee_id))


def get_balances(employee_id: str) -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT code, days FROM balances WHERE employee_id = ?", (employee_id,)
        ).fetchall()
    return {r["code"]: r["days"] for r in rows}


def get_history(employee_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, code, label, status, decision_comment FROM leave_requests "
            "WHERE employee_id = ? ORDER BY created_at DESC",
            (employee_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_overlapping_request(employee_id: str, start_date: str, end_date: str) -> Optional[dict]:
    """Return an existing active (Pending/Approved) leave whose dates overlap
    [start_date, end_date] for this employee, else None. Prevents double-booking
    the same day across leave types."""
    with _conn() as c:
        row = c.execute(
            "SELECT id, code, label FROM leave_requests "
            "WHERE employee_id = ? AND status IN ('Pending', 'Approved') "
            "AND start_date IS NOT NULL AND end_date IS NOT NULL "
            "AND start_date <= ? AND end_date >= ? "
            "ORDER BY created_at DESC LIMIT 1",
            (employee_id, end_date, start_date),
        ).fetchone()
    return dict(row) if row else None


def get_pending_requests(manager_id: str) -> list[dict]:
    """Pending leave requests this manager can act on: those from employees in
    the manager's own department (excluding the manager), newest first."""
    with _conn() as c:
        mrow = c.execute("SELECT dept FROM employees WHERE id = ?", (manager_id,)).fetchone()
        dept = mrow["dept"] if mrow else None
        rows = c.execute(
            "SELECT r.id, r.employee_id, e.name AS employee_name, e.dept AS dept, "
            "       r.code, r.label, r.duration_days, r.created_at "
            "FROM leave_requests r JOIN employees e ON e.id = r.employee_id "
            "WHERE r.status = 'Pending' AND e.dept = ? AND r.employee_id != ? "
            "ORDER BY r.created_at DESC",
            (dept, manager_id),
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
            "INSERT INTO leave_requests "
            "(id, employee_id, code, label, start_date, end_date, duration_days, comments, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
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


def set_decision(req_id: str, status: str, comment: str = "") -> None:
    with _conn() as c:
        c.execute(
            "UPDATE leave_requests SET status = ?, decision_comment = ? WHERE id = ?",
            (status, comment, req_id),
        )


def auto_approve_overdue_sick(max_days: float = 3.0):
    """Auto-approve Sick leaves left Pending longer than max_days (SLA rule).
    Returns (list of affected {id, employee_id}, the comment used)."""
    cutoff = (dt.datetime.now() - dt.timedelta(days=max_days)).isoformat()
    comment = f"Auto-approved - no manager action within {max_days:g} day(s)."
    with _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, employee_id FROM leave_requests "
            "WHERE code = 'SICK' AND status = 'Pending' AND created_at < ?",
            (cutoff,),
        ).fetchall()]
        for r in rows:
            c.execute(
                "UPDATE leave_requests SET status = 'Approved', decision_comment = ? WHERE id = ?",
                (comment, r["id"]),
            )
    return rows, comment


def write_audit(employee_id: str, message: str, parsed: dict, validation: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO audit (employee_id, message, parsed_json, validation_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (employee_id, message, json.dumps(parsed), json.dumps(validation),
             dt.datetime.now().isoformat()),
        )
