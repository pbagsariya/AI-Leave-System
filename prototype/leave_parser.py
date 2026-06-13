"""
Phase 0 spike for the AI Leave Application Assistant.

Turns a natural-language leave message into the structured leave request
described in section 5 of AI_Leave_System_Design.md, then runs a deterministic
validation layer (balance / leave-code / document checks) and prints a
confirmation card.

The LLM ONLY fills a draft. It never decides policy and never submits.
Validation and the submit decision live in plain Python, exactly as the
design intends.

Run with the real model (needs ANTHROPIC_API_KEY):
    python leave_parser.py -m "need 2 days sick leave from tomorrow, medical note attached"

Run offline with the rule-based fallback (no API key needed):
    python leave_parser.py --mock -m "wfh tomorrow, kid is unwell"

Run the built-in example suite:
    python leave_parser.py --demo            # uses the model
    python leave_parser.py --demo --mock     # offline
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from typing import List, Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------
# 1. The closed set of leave codes. In production this is fetched live from
#    the HRMS so it never drifts; here it is a static table for the spike.
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

# Mock per-employee balances (days). Real system reads these from the HRMS.
MOCK_BALANCES = {"SICK": 8.0, "CASUAL": 5.0, "EARNED": 12.0, "COMP_OFF": 2.0}

# Policy: leave types that require a supporting document beyond N days.
DOC_REQUIRED_OVER_DAYS = {"SICK": 2.0}

EMPLOYEE_TZ = "Asia/Kolkata"


# --------------------------------------------------------------------------
# 2. The structured contract the LLM must return (design doc section 5).
# --------------------------------------------------------------------------

class LeaveRequest(BaseModel):
    start_date: Optional[str] = Field(
        default=None, description="ISO date YYYY-MM-DD, resolved from the message"
    )
    end_date: Optional[str] = Field(
        default=None, description="ISO date YYYY-MM-DD; equals start_date for a single day"
    )
    duration_days: Optional[float] = Field(
        default=None, description="Number of days; 0.5 for a half day"
    )
    half_day: bool = Field(default=False, description="True if this is a half-day request")
    absence_code: Optional[AbsenceCode] = Field(
        default=None, description="One of the allowed codes, or null if unclear"
    )
    comments: str = Field(default="", description="Short reason summarised from the message")
    has_attachment: bool = Field(default=False, description="Whether a document was attached")


class ParsedLeave(BaseModel):
    intent: Literal[
        "apply_leave", "check_balance", "view_history", "cancel_leave", "other"
    ]
    leave_request: LeaveRequest
    confidence: float = Field(description="Model self-rating from 0 to 1")
    missing_or_ambiguous: List[str] = Field(
        default_factory=list, description="Required fields still missing or unclear"
    )
    clarifying_question: str = Field(
        default="", description="One short question to ask if anything is missing"
    )


# --------------------------------------------------------------------------
# 3. LLM extraction using the Anthropic SDK with structured outputs.
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
        "Pick absence_code ONLY from this list, matching intent to the closest "
        "code:\n"
        f"{codes}\n\n"
        "Rules:\n"
        "- If a single date is given, set end_date equal to start_date and "
        "duration_days to 1 (or 0.5 for a half day).\n"
        "- If anything required (start_date, duration, absence_code) is missing "
        "or ambiguous, do NOT guess. Set it to null, list the field name in "
        "missing_or_ambiguous, and propose one short clarifying_question.\n"
        "- You never approve, reject, or comment on leave policy. You only fill "
        "a draft.\n"
        "- Set confidence to your honest certainty from 0 to 1."
    )


def parse_with_llm(message: str, today: dt.date, tz: str) -> ParsedLeave:
    """Call Claude and return a validated ParsedLeave."""
    import anthropic  # imported lazily so --mock works without the package

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model="claude-opus-4-8",
        max_tokens=2000,
        system=_system_prompt(today, tz),
        messages=[{"role": "user", "content": message}],
        output_format=ParsedLeave,
    )
    return response.parsed_output


# --------------------------------------------------------------------------
# 4. Offline rule-based fallback so the spike runs without an API key.
#    Deliberately simple; the real system always uses the LLM.
# --------------------------------------------------------------------------

def parse_with_rules(message: str, today: dt.date, tz: str) -> ParsedLeave:
    text = message.lower()

    code: Optional[AbsenceCode] = None
    if "wfh" in text or "work from home" in text:
        code = "WFH"
    elif "sick" in text or "fever" in text or "unwell" in text or "ill" in text:
        code = "SICK"
    elif "casual" in text:
        code = "CASUAL"
    elif "earned" in text or "privilege" in text or "vacation" in text or "holiday" in text:
        code = "EARNED"
    elif "comp" in text:
        code = "COMP_OFF"

    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    start: Optional[dt.date] = None
    if "today" in text:
        start = today
    elif "tomorrow" in text:
        start = today + dt.timedelta(days=1)
    else:
        for i, name in enumerate(weekdays):
            if name in text:
                ahead = (i - today.weekday()) % 7
                ahead = ahead or 7  # "monday" means the next monday
                start = today + dt.timedelta(days=ahead)
                break

    half_day = "half day" in text or "half-day" in text
    duration: Optional[float] = 0.5 if half_day else None
    m = re.search(r"(\d+(?:\.\d+)?)\s*day", text)
    if m:
        duration = float(m.group(1))
    elif duration is None and start is not None:
        duration = 1.0

    end = start
    if start is not None and duration and duration > 1:
        end = start + dt.timedelta(days=int(round(duration)) - 1)

    has_attachment = "attach" in text or "note" in text or "certificate" in text

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
            question = "Which days do you need off, and what type of leave (sick, casual, earned)?"
        elif "absence_code" in missing:
            question = "What type of leave is this (sick, casual, earned)?"
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


# --------------------------------------------------------------------------
# 5. Deterministic validation layer (plain Python, never the model).
# --------------------------------------------------------------------------

class ValidationResult(BaseModel):
    ok: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    balance_after: Optional[float] = None


def validate(parsed: ParsedLeave, balances: dict) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []
    lr = parsed.leave_request

    if parsed.missing_or_ambiguous:
        errors.append(
            "Need more detail: " + ", ".join(parsed.missing_or_ambiguous)
        )

    if parsed.confidence < 0.5 and not errors:
        warnings.append("Low confidence reading; please confirm the details carefully.")

    balance_after: Optional[float] = None
    code = lr.absence_code
    days = lr.duration_days or 0

    if code and code in balances:
        available = balances[code]
        if days > available:
            errors.append(
                f"Insufficient {code} balance: need {days}, have {available}."
            )
        else:
            balance_after = round(available - days, 1)

    if code in DOC_REQUIRED_OVER_DAYS and days > DOC_REQUIRED_OVER_DAYS[code]:
        if not lr.has_attachment:
            errors.append(
                f"{code} over {DOC_REQUIRED_OVER_DAYS[code]} days requires a "
                "supporting document. Please attach one."
            )

    # Weekend sanity check on the start date.
    if lr.start_date:
        try:
            d = dt.date.fromisoformat(lr.start_date)
            if d.weekday() >= 5 and code != "WFH":
                warnings.append(f"{lr.start_date} falls on a weekend.")
        except ValueError:
            errors.append(f"Could not read start_date '{lr.start_date}'.")

    return ValidationResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        balance_after=balance_after,
    )


# --------------------------------------------------------------------------
# 6. Confirmation card (what the Teams bot would render as an Adaptive Card).
# --------------------------------------------------------------------------

def confirmation_card(parsed: ParsedLeave, result: ValidationResult) -> str:
    lr = parsed.leave_request
    lines: List[str] = []
    lines.append("+----------------------------------------------+")

    if parsed.intent != "apply_leave":
        lines.append(f"  Intent detected: {parsed.intent}")
        lines.append("+----------------------------------------------+")
        return "\n".join(lines)

    if result.errors:
        lines.append("  I need a bit more before I can submit:")
        for e in result.errors:
            lines.append(f"   - {e}")
        if parsed.clarifying_question:
            lines.append(f'  Bot asks: "{parsed.clarifying_question}"')
        lines.append("+----------------------------------------------+")
        return "\n".join(lines)

    code = lr.absence_code
    pretty = ABSENCE_CODES.get(code, code or "Unknown")
    lines.append("  Please confirm your leave request")
    lines.append(f"   Type      : {code} ({pretty})")
    if lr.start_date == lr.end_date:
        lines.append(f"   Date      : {lr.start_date} ({lr.duration_days} day)")
    else:
        lines.append(
            f"   Dates     : {lr.start_date} to {lr.end_date} "
            f"({lr.duration_days} days)"
        )
    if lr.comments:
        lines.append(f"   Comment   : {lr.comments}")
    lines.append(f"   Attachment: {'yes' if lr.has_attachment else 'none'}")
    if result.balance_after is not None:
        lines.append(f"   {code} balance after: {result.balance_after} days")
    for w in result.warnings:
        lines.append(f"   Note: {w}")
    lines.append("   [ Confirm ]   [ Edit ]   [ Cancel ]")
    lines.append("+----------------------------------------------+")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 7. CLI / orchestration
# --------------------------------------------------------------------------

DEMO_MESSAGES = [
    "Need 2 days sick leave from tomorrow, attaching the medical certificate.",
    "I'll be out next Monday and Tuesday on earned leave for a family function.",
    "wfh tomorrow, my kid is unwell",
    "take a half day casual leave on Friday afternoon",
    "I will be out next week",  # ambiguous -> should ask
    "3 days sick leave starting Monday",  # doc-required policy trips
]


def run_one(message: str, use_mock: bool, today: dt.date, tz: str) -> None:
    print("\n" + "=" * 60)
    print(f"MESSAGE: {message}")
    print("-" * 60)

    if use_mock:
        parsed = parse_with_rules(message, today, tz)
    else:
        try:
            parsed = parse_with_llm(message, today, tz)
        except Exception as exc:  # network / key / SDK problems -> graceful note
            print(f"[LLM unavailable: {exc}]")
            print("[Falling back to offline rule-based parser]")
            parsed = parse_with_rules(message, today, tz)

    print("STRUCTURED JSON (from the AI draft):")
    print(json.dumps(parsed.model_dump(), indent=2))

    result = validate(parsed, MOCK_BALANCES)
    print("\nCONFIRMATION CARD (what Teams would show):")
    print(confirmation_card(parsed, result))


def main() -> None:
    ap = argparse.ArgumentParser(description="AI leave parser - Phase 0 spike")
    ap.add_argument("-m", "--message", help="A single natural-language leave message")
    ap.add_argument("--mock", action="store_true", help="Use the offline rule-based parser")
    ap.add_argument("--demo", action="store_true", help="Run the built-in example suite")
    ap.add_argument("--today", help="Override today's date as YYYY-MM-DD (for repeatable demos)")
    args = ap.parse_args()

    tz = EMPLOYEE_TZ
    if args.today:
        today = dt.date.fromisoformat(args.today)
    else:
        today = dt.datetime.now(ZoneInfo(tz)).date()

    if args.demo:
        for msg in DEMO_MESSAGES:
            run_one(msg, args.mock, today, tz)
    elif args.message:
        run_one(args.message, args.mock, today, tz)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
