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
import re
import secrets
import sqlite3
from typing import Optional

DB_PATH = os.getenv("LEAVE_DB", os.path.join(os.path.dirname(__file__), "..", "leave.db"))
# The canonical database this source file mirrors. Signups are written back into
# db.py (REGISTERED_USERS) only when running against this DB — tests that point
# LEAVE_DB elsewhere never touch the source file.
_DEFAULT_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "leave.db"))

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
                role TEXT DEFAULT 'Employee', email TEXT, manager_id TEXT
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
        if "manager_id" not in cols:
            c.execute("ALTER TABLE employees ADD COLUMN manager_id TEXT")
        lr_cols = [r["name"] for r in c.execute("PRAGMA table_info(leave_requests)")]
        if "decision_comment" not in lr_cols:
            c.execute("ALTER TABLE leave_requests ADD COLUMN decision_comment TEXT")

        # idempotent: ensure every demo employee, balance, and login exists.
        # INSERT OR IGNORE adds new users to an existing leave.db without
        # touching current data (e.g. drawn-down balances stay as they are).
        for eid, name, dept, role, email, manager_id in EMPLOYEES:
            c.execute("INSERT OR IGNORE INTO employees (id, name, dept, timezone, role, email, manager_id) VALUES (?,?,?,?,?,?,?)",
                      (eid, name, dept, "Asia/Kolkata", role, email, manager_id))
            # keep the seeded name/dept/role/email/manager authoritative even on an older leave.db
            c.execute("UPDATE employees SET name = ?, dept = ?, role = ?, email = ?, manager_id = ? WHERE id = ?",
                      (name, dept, role, email, manager_id, eid))
            for code, days in BALANCES.get(eid, DEFAULT_NEW_BALANCES).items():
                c.execute("INSERT OR IGNORE INTO balances VALUES (?,?,?)", (eid, code, days))
        for username, pw, eid in CREDS:
            # authoritative: keep seeded passwords/ids in sync even if they change
            c.execute("INSERT OR REPLACE INTO credentials (username, password_hash, employee_id) VALUES (?,?,?)",
                      (username, _hash(pw), eid))

        # remove leftover seed accounts from earlier rosters so cruft can't
        # accumulate. Seed rows have ids like 'e1'/'e2'; signed-up users are
        # 'u<hex>' and are never matched, so they're preserved.
        keep_users = [c0 for c0, _, _ in CREDS]
        keep_ids = [e0 for e0, *_ in EMPLOYEES]
        ph_u = ",".join("?" * len(keep_users))
        ph_i = ",".join("?" * len(keep_ids))
        c.execute(f"DELETE FROM credentials WHERE employee_id GLOB 'e[0-9]*' AND username NOT IN ({ph_u})", keep_users)
        c.execute(f"DELETE FROM balances   WHERE employee_id GLOB 'e[0-9]*' AND employee_id NOT IN ({ph_i})", keep_ids)
        c.execute(f"DELETE FROM employees  WHERE id GLOB 'e[0-9]*' AND id NOT IN ({ph_i})", keep_ids)

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
# Demo roster (id, name, dept, role, email, manager_id). Leave approvals route to
# the employee's assigned manager_id (managers have manager_id = None). Accounts
# created through the signup form are appended below the markers by
# create_account() (it rewrites this file), so the roster here always mirrors
# leave.db and accounts are recreated if leave.db is ever reset.
EMPLOYEES = [
    ("e1", "pmanager1", "IT", "Manager", "prakashatinfo@gmail.com", None),
    ("e2", "pmanager2", "R&D", "Manager", "prakash.bagsariya@gmail.com", None),
    ("e3", "pemployee1", "IT", "Employee", "prpri2007@gmail.com", "e1"),
    ("e4", "pemployee2", "R&D", "Employee", "bagsariya.prakash@gmail.com", "e2"),
    ("e5", "pemployee3", "IT", "Employee", "pbagsariya@gmail.com", "e1"),
    ('e6', 'pemployee4', 'R&D', 'Employee', 'prakash.bagsariya@gmail.com', 'e2'),
    ('e7', 'pemployee7', 'IT', 'Employee', 'pbagsariya@gmail.com', 'e1'),
    # __NEW_EMPLOYEES__  signups are inserted directly above this line — keep the marker
]

# Starting allotment granted to a freshly signed-up account. Any employee id not
# listed in BALANCES below (e.g. a signup) is seeded with these defaults.
DEFAULT_NEW_BALANCES = {"SICK": 10, "CASUAL": 8, "EARNED": 15, "COMP_OFF": 4}
# __BALANCES_START__  auto-synced with leave.db on every balance change — do not edit by hand
BALANCES = {
    "e1": {"SICK": 8, "CASUAL": 5, "EARNED": 12, "COMP_OFF": 2},
    "e2": {"SICK": 6, "CASUAL": 7, "EARNED": 9, "COMP_OFF": 1},
    "e3": {"SICK": 7, "CASUAL": 4, "EARNED": 14, "COMP_OFF": 3},
    "e4": {"SICK": 8, "CASUAL": 6, "EARNED": 12, "COMP_OFF": 2},
    "e5": {"SICK": 10, "CASUAL": 8, "EARNED": 15, "COMP_OFF": 4},
    "e6": {"SICK": 10, "CASUAL": 8, "EARNED": 15, "COMP_OFF": 4},
    "e7": {"SICK": 9, "CASUAL": 8, "EARNED": 15, "COMP_OFF": 4},
}
# __BALANCES_END__
CREDS = [
    ("pmanager1", "pmanager123", "e1"),
    ("pmanager2", "pmanager456", "e2"),
    ("pemployee1", "pemployee123", "e3"),
    ("pemployee2", "pemployee456", "e4"),
    ("pemployee3", "pemployee789", "e5"),
    ('pemployee4', 'pemployee123', 'e6'),
    ('pemployee7', 'pemployee77', 'e7'),
    # __NEW_CREDS__  signups are inserted directly above this line — keep the marker
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
        rows = c.execute("SELECT id, name, dept, role, email, manager_id FROM employees ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_employee(employee_id: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT id, name, dept, role, email, manager_id FROM employees WHERE id = ?", (employee_id,)).fetchone()
    return dict(row) if row else None


def get_managers() -> list[dict]:
    """All managers — used to populate the signup 'Manager Name' dropdown."""
    with _conn() as c:
        rows = c.execute("SELECT id, name, dept, email FROM employees WHERE role = 'Manager' ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_managers_for_employee(employee_id: str) -> list[dict]:
    """The employee's approver(s): their assigned manager_id when set, otherwise
    a fall back to managers in the same department."""
    with _conn() as c:
        erow = c.execute("SELECT dept, manager_id FROM employees WHERE id = ?", (employee_id,)).fetchone()
        if not erow:
            return []
        if erow["manager_id"]:
            rows = c.execute(
                "SELECT id, name, email FROM employees WHERE id = ? AND role = 'Manager'",
                (erow["manager_id"],),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
        rows = c.execute(
            "SELECT id, name, email FROM employees "
            "WHERE role = 'Manager' AND dept = ? AND id != ?",
            (erow["dept"], employee_id),
        ).fetchall()
    return [dict(r) for r in rows]


def can_manage(manager_id: str, employee_id: str) -> bool:
    """A manager may act on an employee's request when they are that employee's
    assigned manager — or, for employees with no assigned manager, when they
    share a department (legacy fallback)."""
    with _conn() as c:
        erow = c.execute("SELECT dept, manager_id FROM employees WHERE id = ?", (employee_id,)).fetchone()
        mrow = c.execute("SELECT dept, role FROM employees WHERE id = ?", (manager_id,)).fetchone()
    if not erow or not mrow or mrow["role"] != "Manager":
        return False
    if erow["manager_id"]:
        return erow["manager_id"] == manager_id
    return erow["dept"] == mrow["dept"]


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


_EMP_MARKER = "# __NEW_EMPLOYEES__"
_CRED_MARKER = "# __NEW_CREDS__"


def _next_employee_id() -> str:
    """The next free e<N> id, based on the current EMPLOYEES roster."""
    n = 0
    for e in EMPLOYEES:
        m = re.fullmatch(r"e(\d+)", e[0])
        if m:
            n = max(n, int(m.group(1)))
    return f"e{n + 1}"


def _append_signup_to_source(eid: str, name: str, dept: str, role: str,
                             email: str, manager_id, username: str, password: str) -> None:
    """Append a new signup to the EMPLOYEES and CREDS lists in this db.py so the
    source roster mirrors leave.db (and the account is recreated if leave.db is
    reset). Only runs against the canonical leave.db (skipped when LEAVE_DB points
    elsewhere, e.g. tests). Best-effort: a failure never breaks account creation."""
    if os.path.abspath(DB_PATH) != _DEFAULT_DB_PATH:
        return
    try:
        emp_line = "    (%r, %r, %r, %r, %r, %r),\n" % (eid, name, dept, role, email, manager_id)
        cred_line = "    (%r, %r, %r),\n" % (username, password, eid)
        path = os.path.abspath(__file__)
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        for marker, line in ((_EMP_MARKER, emp_line), (_CRED_MARKER, cred_line)):
            idx = src.find(marker)
            if idx == -1:
                continue
            line_start = src.rfind("\n", 0, idx) + 1
            src = src[:line_start] + line + src[line_start:]
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        EMPLOYEES.append((eid, name, dept, role, email, manager_id))  # keep this process in sync
        CREDS.append((username, password, eid))
    except Exception as exc:  # never let source-sync break a signup
        print(f"[db] could not sync new account to db.py: {exc}")


_BAL_START = "# __BALANCES_START__"
_BAL_END = "# __BALANCES_END__"
_CODE_ORDER = ["SICK", "CASUAL", "EARNED", "COMP_OFF"]


def _emp_sort_key(eid: str):
    m = re.fullmatch(r"e(\d+)", eid)
    return (0, int(m.group(1))) if m else (1, eid)


def _num(v):
    """Render a balance as an int when whole (8) else a float (0.5)."""
    f = float(v)
    return int(f) if f.is_integer() else f


def _sync_balances_to_source() -> None:
    """Rewrite the BALANCES dict in this db.py so it mirrors the live balances in
    leave.db (called after every balance change / account creation). Only runs
    against the canonical leave.db (skipped when LEAVE_DB points elsewhere).
    Best-effort: a failure never breaks the leave flow."""
    if os.path.abspath(DB_PATH) != _DEFAULT_DB_PATH:
        return
    try:
        with _conn() as c:
            rows = c.execute("SELECT employee_id, code, days FROM balances").fetchall()
        bal: dict[str, dict] = {}
        for r in rows:
            bal.setdefault(r["employee_id"], {})[r["code"]] = r["days"]

        lines = ["BALANCES = {"]
        for eid in sorted(bal, key=_emp_sort_key):
            codes = bal[eid]
            order = [k for k in _CODE_ORDER if k in codes] + [k for k in codes if k not in _CODE_ORDER]
            inner = ", ".join('"%s": %s' % (k, _num(codes[k])) for k in order)
            lines.append('    "%s": {%s},' % (eid, inner))
        lines.append("}")
        block = "\n".join(lines) + "\n"

        path = os.path.abspath(__file__)
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        i, j = src.find(_BAL_START), src.find(_BAL_END)
        if i == -1 or j == -1:
            return
        block_start = src.find("\n", i) + 1          # just after the START marker line
        block_end = src.rfind("\n", 0, j) + 1        # start of the END marker line
        with open(path, "w", encoding="utf-8") as f:
            f.write(src[:block_start] + block + src[block_end:])

        global BALANCES
        BALANCES = {eid: dict(codes) for eid, codes in bal.items()}
    except Exception as exc:  # never let source-sync break the leave flow
        print(f"[db] could not sync BALANCES to db.py: {exc}")


def create_account(username: str, password: str, name: str, dept: str, role: str,
                   email: str = "", manager_id: Optional[str] = None) -> str:
    """Create an employee + balances + login. Returns the new employee_id.

    Employees carry a manager_id (their approver). The account is written to
    leave.db AND appended to db.py's EMPLOYEES + CREDS lists so the two stay in
    sync."""
    username = username.strip().lower()
    name = name.strip()
    dept = dept.strip() or "—"
    email = email.strip()
    manager_id = (manager_id or None) if role != "Manager" else None
    eid = _next_employee_id()
    with _conn() as c:
        c.execute(
            "INSERT INTO employees (id, name, dept, timezone, role, email, manager_id) VALUES (?,?,?,?,?,?,?)",
            (eid, name, dept, "Asia/Kolkata", role, email, manager_id),
        )
        for code, days in DEFAULT_NEW_BALANCES.items():
            c.execute("INSERT INTO balances VALUES (?,?,?)", (eid, code, days))
        c.execute("INSERT INTO credentials VALUES (?,?,?)", (username, _hash(password), eid))
    _append_signup_to_source(eid, name, dept, role, email, manager_id, username, password)
    _sync_balances_to_source()   # add the new employee's balances to BALANCES in db.py
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
    """Pending leave requests this manager can act on: those from employees who
    report to them (manager_id), plus any unassigned employees in the manager's
    own department (legacy fallback). Newest first."""
    with _conn() as c:
        mrow = c.execute("SELECT dept FROM employees WHERE id = ?", (manager_id,)).fetchone()
        dept = mrow["dept"] if mrow else None
        rows = c.execute(
            "SELECT r.id, r.employee_id, e.name AS employee_name, e.dept AS dept, "
            "       r.code, r.label, r.duration_days, r.created_at "
            "FROM leave_requests r JOIN employees e ON e.id = r.employee_id "
            "WHERE r.status = 'Pending' AND r.employee_id != ? AND ("
            "      e.manager_id = ? OR (e.manager_id IS NULL AND e.dept = ?)) "
            "ORDER BY r.created_at DESC",
            (manager_id, manager_id, dept),
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
    _sync_balances_to_source()


def credit_balance(employee_id: str, code: str, days: float) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE balances SET days = days + ? WHERE employee_id = ? AND code = ?",
            (days, employee_id, code),
        )
    _sync_balances_to_source()


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
