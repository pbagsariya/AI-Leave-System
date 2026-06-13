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
import time
from zoneinfo import ZoneInfo

from fastapi import FastAPI
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


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


# ---- request models --------------------------------------------------------

class ChatIn(BaseModel):
    employee_id: str
    message: str
    session_id: str | None = None
    has_attachment: bool = False


class ConfirmIn(BaseModel):
    session_id: str


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

@app.get("/api/employees")
def employees():
    return db.get_employees()


@app.get("/api/balances")
def balances(employee_id: str):
    return db.get_balances(employee_id)


@app.get("/api/history")
def history(employee_id: str):
    return db.get_history(employee_id)


# ---- API: chat (extract -> validate -> reply) ------------------------------

@app.post("/api/chat")
def chat(body: ChatIn):
    emp = body.employee_id
    bal = db.get_balances(emp)
    text = body.message.lower()

    # quick deterministic intent routing (works even with the offline parser)
    if any(w in text for w in ("balance", "how many", "leaves left", "remaining", "left")):
        if "leave" in text or "balance" in text or "remaining" in text or "left" in text:
            return {"reply_type": "balance", "balances": bal}
    status_word = next((s for s in ("approved", "pending", "cancelled") if s in text), None)
    if (any(w in text for w in ("history", "past leave", "past request", "previous", "recent request"))
            or (status_word and ("leave" in text or "request" in text))):
        items = db.get_history(emp)
        if status_word:
            items = [h for h in items if h["status"].lower() == status_word]
        return {"reply_type": "history", "history": items}
    if "cancel" in text:
        return _cancel(emp, text, bal)

    # apply_leave path
    parsed = L.parse_message(body.message, _today(emp))

    # honour the attach toggle from the UI (we send metadata only, not the file)
    if body.has_attachment:
        parsed.leave_request.has_attachment = True

    # if the model itself classified a non-apply intent, route accordingly
    if parsed.intent == "check_balance":
        return {"reply_type": "balance", "balances": bal}
    if parsed.intent == "view_history":
        return {"reply_type": "history", "history": db.get_history(emp)}

    result = L.validate(parsed, bal)
    db.write_audit(emp, body.message, parsed.model_dump(), result.model_dump())

    if parsed.missing_or_ambiguous:
        return {"reply_type": "clarification",
                "question": parsed.clarifying_question or "Could you give me a bit more detail?"}

    if not result.ok:
        return {"reply_type": "policy_block", "message": result.errors[0]}

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
def confirm(body: ConfirmIn):
    draft = db.get_draft(body.session_id)
    if not draft:
        return {"error": "draft expired"}

    emp = draft["employee_id"]
    code = draft["code"]
    duration = draft["duration"] or 0
    bal = db.get_balances(emp)
    if code in bal:
        db.decrement_balance(emp, code, duration)

    req_id = f"#AB-{random.randint(10400, 10999)}"
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
