"""
Leave extraction + validation logic.

Ported from prototype/leave_parser.py. The LLM ONLY fills a draft; it never
decides policy and never submits. Validation lives in plain Python.

Claude is called via anthropic.Anthropic() with NO api_key — credentials are
resolved from your Claude subscription's OAuth profile (run `claude` or
`ant auth login` once). If the call fails (not logged in / offline) we fall
back to the deterministic rule parser so the demo never dead-ends. Set
LEAVE_OFFLINE=1 to force the offline parser.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# Closed set of leave codes + policy tables. (Real system reads from HRMS.)
# --------------------------------------------------------------------------

ABSENCE_CODES = {
    "SICK": "Sick leave for illness or medical reasons",
    "CASUAL": "Casual leave for short personal matters",
    "EARNED": "Earned or privilege leave for planned vacation",
    "COMP_OFF": "Compensatory off for working on a holiday or weekend",
    "WFH": "Work from home (not a leave deduction)",
    "LOP": "Loss of pay leave when no balance remains",
}

AbsenceCode = Literal["SICK", "CASUAL", "EARNED", "COMP_OFF", "WFH", "LOP"]

# Leave types that require a supporting document beyond N days.
DOC_REQUIRED_OVER_DAYS = {"SICK": 2.0}

EMPLOYEE_TZ = "Asia/Kolkata"


# --------------------------------------------------------------------------
# The structured contract the LLM must return (design doc section 5).
# --------------------------------------------------------------------------

class LeaveRequest(BaseModel):
    start_date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="ISO date; = start for a single day")
    duration_days: Optional[float] = Field(default=None, description="Days; 0.5 for a half day")
    half_day: bool = Field(default=False)
    absence_code: Optional[AbsenceCode] = Field(default=None, description="One of the allowed codes, or null")
    comments: str = Field(default="", description="Short reason summarised from the message")
    has_attachment: bool = Field(default=False)


class ParsedLeave(BaseModel):
    intent: Literal["apply_leave", "check_balance", "view_history", "cancel_leave", "other"]
    leave_request: LeaveRequest
    confidence: float = Field(description="Model self-rating 0..1")
    missing_or_ambiguous: List[str] = Field(default_factory=list)
    clarifying_question: str = Field(default="")


class ValidationResult(BaseModel):
    ok: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    balance_after: Optional[float] = None


# --------------------------------------------------------------------------
# LLM extraction (Claude via subscription — no api_key passed).
# --------------------------------------------------------------------------

def _system_prompt(today: dt.date, tz: str) -> str:
    codes = "\n".join(f"  {code}: {desc}" for code, desc in ABSENCE_CODES.items())
    return (
        "You are a leave-application assistant. Convert the employee message "
        "into a structured leave request. Extract dates, duration, leave type "
        "and reason.\n\n"
        f"Today is {today.isoformat()} ({today.strftime('%A')}). "
        f"The employee time zone is {tz}. Resolve relative phrases such as "
        "tomorrow, next Monday, or this Friday against that date.\n\n"
        "Pick absence_code ONLY from this list, matching intent to the closest code:\n"
        f"{codes}\n\n"
        "Also classify intent: apply_leave, check_balance, view_history, "
        "cancel_leave, or other.\n\n"
        "Rules:\n"
        "- If a single date is given, set end_date equal to start_date and "
        "duration_days to 1 (or 0.5 for a half day).\n"
        "- If anything required (start_date, duration, absence_code) is missing "
        "or ambiguous for an apply_leave request, do NOT guess. Set it to null, "
        "list the field name in missing_or_ambiguous, and propose one short "
        "clarifying_question.\n"
        "- You never approve, reject, or comment on leave policy. You only fill a draft.\n"
        "- Set confidence to your honest certainty from 0 to 1."
    )


def parse_with_llm(message: str, today: dt.date, tz: str) -> ParsedLeave:
    import anthropic  # lazy import so the offline path needs no package

    client = anthropic.Anthropic()  # no api_key -> uses subscription OAuth profile
    response = client.messages.parse(
        model="claude-opus-4-8",
        max_tokens=2000,
        system=_system_prompt(today, tz),
        messages=[{"role": "user", "content": message}],
        output_format=ParsedLeave,
    )
    return response.parsed_output


# --------------------------------------------------------------------------
# Offline rule-based fallback (deterministic; demo safety net).
# --------------------------------------------------------------------------

# Month name/abbrev -> number, longest names first so "march" beats "mar".
_MONTHS = {}
for _i in range(1, 13):
    _MONTHS[calendar.month_name[_i].lower()] = _i
    _MONTHS[calendar.month_abbr[_i].lower()] = _i
_MONTH_ALT = "|".join(sorted((m for m in _MONTHS if m), key=len, reverse=True))
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _extract_dates(text: str, today: dt.date):
    """Find all dates in the message and return (start, end). Handles absolute
    dates ('11th August 2026', '11-Aug-2026', '11/08/2026', ISO), ranges, and
    relative phrases ('today', 'tomorrow', weekday names)."""
    t = text.lower()
    found: list[tuple[int, dt.date]] = []

    def add(pos: int, y: int, mo: int, d: int) -> None:
        try:
            found.append((pos, dt.date(y, mo, d)))
        except ValueError:
            pass

    sep = r"[\s\-/.,]*"      # allow space, hyphen, slash, dot, comma between parts
    # ISO yyyy-mm-dd
    for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        add(m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # dd Month yyyy  (11th august 2026 / 11-aug-2026 / 11/august/2026 / 11 aug 2026)
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?" + sep + r"(" + _MONTH_ALT + r")" + sep + r"(\d{4})\b", t):
        add(m.start(), int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    # Month dd yyyy  (august 11 2026 / august/11/2026 / august 11th, 2026)
    for m in re.finditer(r"\b(" + _MONTH_ALT + r")" + sep + r"(\d{1,2})(?:st|nd|rd|th)?" + sep + r"(\d{4})\b", t):
        add(m.start(), int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    # dd/mm/yyyy or dd-mm-yyyy (day-first, common outside the US)
    for m in re.finditer(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b", t):
        add(m.start(), int(m.group(3)), int(m.group(2)), int(m.group(1)))
    # relative
    for m in re.finditer(r"\btoday\b", t):
        found.append((m.start(), today))
    for m in re.finditer(r"\btomorrow\b", t):
        found.append((m.start(), today + dt.timedelta(days=1)))
    for i, name in enumerate(_WEEKDAYS):
        for m in re.finditer(r"\b" + name + r"\b", t):
            ahead = (i - today.weekday()) % 7 or 7
            found.append((m.start(), today + dt.timedelta(days=ahead)))

    if not found:
        return None, None
    # unique by date, earliest text position wins; order by position
    by_date: dict[dt.date, int] = {}
    for pos, d in found:
        if d not in by_date or pos < by_date[d]:
            by_date[d] = pos
    ordered = sorted(by_date.items(), key=lambda kv: kv[1])
    start = ordered[0][0]
    end = ordered[-1][0] if len(ordered) > 1 else None
    if end is not None and end < start:
        start, end = end, start
    return start, end


def parse_with_rules(message: str, today: dt.date, tz: str) -> ParsedLeave:
    text = message.lower()

    code: Optional[AbsenceCode] = None
    if "wfh" in text or "work from home" in text:
        code = "WFH"
    elif any(w in text for w in ("sick", "fever", "unwell", "ill", "medical")):
        code = "SICK"
    elif "casual" in text:
        code = "CASUAL"
    elif any(w in text for w in ("earned", "privilege", "vacation", "wedding", "family function")):
        code = "EARNED"
    elif "comp" in text:
        code = "COMP_OFF"

    start, end_explicit = _extract_dates(text, today)

    half_day = "half day" in text or "half-day" in text
    duration: Optional[float] = 0.5 if half_day else None
    m = re.search(r"(\d+(?:\.\d+)?)\s*days?\b", text)
    if m:
        duration = float(m.group(1))

    if start is not None and end_explicit is not None and end_explicit > start:
        # an explicit date range wins over any "N days" count
        end = end_explicit
        duration = float((end - start).days + 1)
    elif start is not None:
        if duration is None:
            duration = 1.0
        end = start + dt.timedelta(days=int(round(duration)) - 1) if duration > 1 else start
    else:
        end = None

    has_attachment = any(w in text for w in ("attach", "note", "certificate", ".pdf", "📎"))

    missing: List[str] = []
    if start is None:
        missing.append("start_date")
    if code is None:
        missing.append("absence_code")
    if duration is None:
        missing.append("duration_days")

    question = ""
    if missing:
        if "absence_code" in missing and "start_date" in missing:
            question = "Which day(s) do you need off, and what type of leave (sick, casual, earned)?"
        elif "absence_code" in missing:
            question = "What type of leave is this — sick, casual, or earned?"
        elif "start_date" in missing:
            question = "Which day(s) should the leave start?"
        else:
            question = "How many days do you need?"

    return ParsedLeave(
        intent="apply_leave",
        leave_request=LeaveRequest(
            start_date=start.isoformat() if start else None,
            end_date=end.isoformat() if end else None,
            duration_days=duration,
            half_day=half_day,
            absence_code=code,
            comments=message.strip(),
            has_attachment=has_attachment,
        ),
        confidence=0.55 if not missing else 0.3,
        missing_or_ambiguous=missing,
        clarifying_question=question,
    )


def parse_message(message: str, today: dt.date, tz: str = EMPLOYEE_TZ) -> ParsedLeave:
    """Try Claude (subscription), fall back to the offline parser on any failure."""
    if os.getenv("LEAVE_OFFLINE") == "1":
        return parse_with_rules(message, today, tz)
    try:
        return parse_with_llm(message, today, tz)
    except Exception as exc:  # not logged in / offline / SDK error
        print(f"[leave_logic] LLM unavailable ({exc}); using offline parser")
        return parse_with_rules(message, today, tz)


# --------------------------------------------------------------------------
# Deterministic validation (plain Python, never the model).
# --------------------------------------------------------------------------

def validate(parsed: ParsedLeave, balances: dict) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []
    lr = parsed.leave_request

    if parsed.missing_or_ambiguous:
        errors.append("Need more detail: " + ", ".join(parsed.missing_or_ambiguous))

    if parsed.confidence < 0.5 and not errors:
        warnings.append("Low confidence reading; please confirm the details carefully.")

    balance_after: Optional[float] = None
    code = lr.absence_code
    days = lr.duration_days or 0

    if code and code in balances:
        available = balances[code]
        if days > available:
            errors.append(f"You only have {available} day(s) of {code} left, but this request is for {days}.")
        else:
            balance_after = round(available - days, 1)

    if code in DOC_REQUIRED_OVER_DAYS and days > DOC_REQUIRED_OVER_DAYS[code]:
        if not lr.has_attachment:
            errors.append(
                f"Sick leave over {DOC_REQUIRED_OVER_DAYS[code]} days needs a medical "
                "certificate. Please attach one to proceed."
            )

    if lr.start_date:
        try:
            d = dt.date.fromisoformat(lr.start_date)
            if d.weekday() >= 5 and code != "WFH":
                warnings.append(f"{lr.start_date} falls on a weekend.")
        except ValueError:
            errors.append(f"Could not read start_date '{lr.start_date}'.")

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, balance_after=balance_after)
