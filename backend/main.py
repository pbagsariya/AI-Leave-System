"""
Leave Assistant — FastAPI orchestrator.

Implements the 5-endpoint contract the frontend's realApi calls, backed by
SQLite (db.py) and the ported extraction/validation (leave_logic.py). Claude is
reached via your subscription (no API key). Submission happens ONLY in
/api/confirm — the model never submits (the design's confirmation gate).

Run:
    pip install -r backend/requirements.txt
    claude            # or: ant auth login   (one-time subscription login)
    uvicorn backend.main:app --reload
Then open http://localhost:8000
"""

from __future__ import annotations

import datetime as dt
import os
import random
import re
import secrets
import time
from zoneinfo import ZoneInfo

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from . import leave_logic as L

app = FastAPI(title="Leave Assistant")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

BAL_LABEL = {"SICK": "Sick", "CASUAL": "Casual", "EARNED": "Earned",
             "COMP_OFF": "Comp-off", "WFH": "WFH", "LOP": "LOP"}
CODE_LABEL = {"SICK": "Sick Leave", "CASUAL": "Casual Leave", "EARNED": "Earned Leave",
              "COMP_OFF": "Comp-off", "WFH": "Work From Home", "LOP": "Loss of Pay"}

# Per-employee buffer of unresolved apply-leave messages, so a clarifying answer
# combines with what was said earlier (demo stand-in for conversation history).
PENDING: dict[str, list[str]] = {}


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


# ---- request models --------------------------------------------------------

class LoginIn(BaseModel):
    username: str
    password: str


class SignupIn(BaseModel):
    name: str
    dept: str = ""
    username: str
    password: str
    role: str = "Employee"


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None
    has_attachment: bool = False


class ConfirmIn(BaseModel):
    session_id: str


class ReqIdIn(BaseModel):
    request_id: str


# ---- auth ------------------------------------------------------------------

def current_emp(session: str | None = Cookie(default=None)) -> str:
    """Resolve the logged-in employee from the session cookie, or 401."""
    emp = db.session_employee(session) if session else None
    if not emp:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return emp


def _start_session(emp: str, response: Response) -> dict:
    token = secrets.token_urlsafe(32)
    db.create_session(token, emp)
    response.set_cookie("session", token, httponly=True, samesite="lax", max_age=86400)
    return db.get_employee(emp)


@app.post("/api/signup")
def signup(body: SignupIn, response: Response):
    name = body.name.strip()
    username = body.username.strip()
    if not name or not username or not body.password:
        raise HTTPException(status_code=400, detail="Name, username and password are required")
    if len(body.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    if " " in username:
        raise HTTPException(status_code=400, detail="Username cannot contain spaces")
    if db.username_exists(username):
        raise HTTPException(status_code=409, detail="That username is already taken")
    role = "Manager" if body.role == "Manager" else "Employee"
    emp = db.create_account(username, body.password, name, body.dept, role)
    return _start_session(emp, response)  # auto-login after signup


@app.post("/api/login")
def login(body: LoginIn, response: Response):
    emp = db.verify_login(body.username, body.password)
    if not emp:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return _start_session(emp, response)


@app.post("/api/logout")
def logout(response: Response, session: str | None = Cookie(default=None)):
    if session:
        db.delete_session(session)
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
def me(emp: str = Depends(current_emp)):
    return db.get_employee(emp)


def current_manager(emp: str = Depends(current_emp)) -> str:
    """Like current_emp, but requires the Manager role (else 403)."""
    e = db.get_employee(emp)
    if not e or e.get("role") != "Manager":
        raise HTTPException(status_code=403, detail="Manager access required")
    return emp


# ---- API: manager approvals ------------------------------------------------

@app.get("/api/approvals")
def approvals(mgr: str = Depends(current_manager)):
    return db.get_pending_requests(exclude_employee=mgr)


@app.post("/api/approve")
def approve(body: ReqIdIn, mgr: str = Depends(current_manager)):
    req = db.get_request(body.request_id)
    if not req or req["status"] != "Pending":
        return {"error": "Request is no longer pending"}
    db.set_request_status(body.request_id, "Approved")
    return {"ok": True, "request_id": body.request_id, "status": "Approved"}


@app.post("/api/reject")
def reject(body: ReqIdIn, mgr: str = Depends(current_manager)):
    req = db.get_request(body.request_id)
    if not req or req["status"] != "Pending":
        return {"error": "Request is no longer pending"}
    db.set_request_status(body.request_id, "Rejected")
    # give the days back to the requester (submission had deducted them)
    if req["code"] and req["duration_days"]:
        db.credit_balance(req["employee_id"], req["code"], req["duration_days"])
    return {"ok": True, "request_id": body.request_id, "status": "Rejected"}


# ---- helpers ---------------------------------------------------------------

def _today(employee_id: str) -> dt.date:
    # employee timezone from the DB; default Asia/Kolkata
    tz = L.EMPLOYEE_TZ
    return dt.datetime.now(ZoneInfo(tz)).date()


def _fmt(iso: str | None) -> str:
    if not iso:
        return ""
    return dt.date.fromisoformat(iso).strftime("%d %b %Y")


def _fmt_short(iso: str | None) -> str:
    if not iso:
        return ""
    return dt.date.fromisoformat(iso).strftime("%d %b")


def _new_request_id() -> str:
    """A unique #AB-##### id (avoids PK collisions as the table grows)."""
    for _ in range(100):
        rid = f"#AB-{random.randint(10000, 99999)}"
        if not db.get_request(rid):
            return rid
    return "#AB-" + secrets.token_hex(3)


def _cancel(emp: str, text: str, bal: dict) -> dict:
    m = re.search(r"#?\s*ab[-\s]?(\d+)", text, re.I)
    if not m:
        return {"reply_type": "clarification",
                "question": "Sure — which request should I cancel? Give me the ID (e.g. #AB-10391)."}
    req_id = f"#AB-{m.group(1)}"
    req = db.get_request(req_id)
    if not req or req["employee_id"] != emp:
        return {"reply_type": "policy_block", "message": f"I couldn't find request {req_id} under your name."}
    if req["status"] != "Pending":
        return {"reply_type": "policy_block",
                "message": f"{req_id} is {req['status']} — only pending requests can be cancelled here."}
    db.set_request_status(req_id, "Cancelled")
    restored = ""
    if req["code"] in bal and req["duration_days"]:
        db.credit_balance(emp, req["code"], req["duration_days"])
        restored = f" {req['duration_days']} day(s) returned to your {BAL_LABEL.get(req['code'], req['code'])} balance."
    return {"reply_type": "cancelled",
            "message": f"Done — {req_id} has been cancelled.{restored}",
            "refresh": True}


# ---- API: reads ------------------------------------------------------------

@app.get("/api/balances")
def balances(emp: str = Depends(current_emp)):
    return db.get_balances(emp)


@app.get("/api/history")
def history(emp: str = Depends(current_emp)):
    return db.get_history(emp)


# ---- API: chat (extract -> validate -> reply) ------------------------------

@app.post("/api/chat")
def chat(body: ChatIn, emp: str = Depends(current_emp)):
    bal = db.get_balances(emp)
    text = body.message.lower()

    # quick deterministic intent routing (works even with the offline parser).
    # These switch intent, so any half-finished apply-leave context is dropped.
    if any(w in text for w in ("balance", "how many", "leaves left", "remaining", "left")):
        if "leave" in text or "balance" in text or "remaining" in text or "left" in text:
            PENDING.pop(emp, None)
            return {"reply_type": "balance", "balances": bal}
    status_word = next((s for s in ("approved", "pending", "cancelled") if s in text), None)
    if (any(w in text for w in ("history", "past leave", "past request", "previous", "recent request"))
            or (status_word and ("leave" in text or "request" in text))):
        PENDING.pop(emp, None)
        items = db.get_history(emp)
        if status_word:
            items = [h for h in items if h["status"].lower() == status_word]
        return {"reply_type": "history", "history": items}
    if "cancel" in text:
        PENDING.pop(emp, None)
        return _cancel(emp, text, bal)
    if any(w in text for w in ("start over", "reset", "never mind", "nevermind")):
        PENDING.pop(emp, None)
        return {"reply_type": "clarification",
                "question": "Sure, let's start fresh. What leave would you like to apply for?"}

    # apply_leave path — accumulate turns so a clarifying answer combines with
    # everything said earlier in this request.
    PENDING.setdefault(emp, []).append(body.message)
    combined = " ".join(PENDING[emp])
    parsed = L.parse_message(combined, _today(emp))

    # honour the attach toggle from the UI (we send metadata only, not the file)
    if body.has_attachment:
        parsed.leave_request.has_attachment = True

    # if the model itself classified a non-apply intent, route accordingly
    if parsed.intent == "check_balance":
        PENDING.pop(emp, None)
        return {"reply_type": "balance", "balances": bal}
    if parsed.intent == "view_history":
        PENDING.pop(emp, None)
        return {"reply_type": "history", "history": db.get_history(emp)}

    result = L.validate(parsed, bal)
    db.write_audit(emp, combined, parsed.model_dump(), result.model_dump())

    if parsed.missing_or_ambiguous:
        # keep the buffer so the next message adds to it
        return {"reply_type": "clarification",
                "question": parsed.clarifying_question or "Could you give me a bit more detail?"}

    if not result.ok:
        # keep the buffer (e.g. user attaches the certificate and re-sends)
        return {"reply_type": "policy_block", "message": result.errors[0]}

    # fully resolved — clear the buffer; the draft now lives in `sessions`
    PENDING.pop(emp, None)

    lr = parsed.leave_request
    same_day = lr.start_date == lr.end_date
    session_id = f"s{int(time.time()*1000)}"
    db.save_draft(session_id, emp, {
        "code": lr.absence_code,
        "duration": lr.duration_days,
        "start_date": lr.start_date,
        "end_date": lr.end_date,
    })
    card = {
        "code": lr.absence_code,
        "label": CODE_LABEL.get(lr.absence_code, lr.absence_code),
        "start": _fmt(lr.start_date),
        "end": _fmt(lr.end_date),
        "sameDay": same_day,
        "duration": lr.duration_days,
        "comment": lr.comments[:57] + "…" if len(lr.comments) > 60 else lr.comments,
        "attachment": "document.pdf" if lr.has_attachment else None,
        "balanceAfter": result.balance_after,
    }
    return {"reply_type": "confirmation", "session_id": session_id, "card": card}


# ---- API: confirm (the only place a request is submitted) ------------------

@app.post("/api/confirm")
def confirm(body: ConfirmIn, emp: str = Depends(current_emp)):
    draft = db.get_draft(body.session_id)
    if not draft or draft["employee_id"] != emp:
        return {"error": "draft expired"}

    PENDING.pop(emp, None)
    code = draft["code"]
    duration = draft["duration"] or 0
    bal = db.get_balances(emp)
    if code in bal:
        db.decrement_balance(emp, code, duration)

    req_id = _new_request_id()
    if draft.get("end_date") and draft["start_date"] != draft["end_date"]:
        rng = f"{_fmt_short(draft['start_date'])}–{_fmt_short(draft['end_date'])}"
    else:
        rng = _fmt_short(draft["start_date"])
    db.insert_request({
        "id": req_id, "employee_id": emp, "code": code,
        "label": f"{BAL_LABEL.get(code, code)} · {rng}",
        "start_date": draft["start_date"], "end_date": draft["end_date"],
        "duration_days": duration, "comments": "", "status": "Pending",
    })
    db.delete_draft(body.session_id)
    return {"request_id": req_id, "status": "Pending", "balances": db.get_balances(emp)}


# ---- serve the frontend (mounted last so /api/* wins) ----------------------

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
