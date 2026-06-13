# Phase 0 Spike — AI Leave Application Assistant

A runnable proof of the core idea from `AI_Leave_System_Design.md`: turn a
plain-English leave message into the **structured leave request** (design doc
section 5), run a **deterministic validation layer**, and print the
**confirmation card** the Teams bot would show.

This is intentionally just the brain — no Teams, no HRMS. It de-risks the
hardest part (natural-language understanding) and produces test cases for the
bot.

## What it demonstrates

- Natural language in → structured JSON out (dates, duration, leave type,
  reason, attachment flag).
- Relative dates ("tomorrow", "next Monday", "Friday afternoon") resolved
  against the current date and the employee time zone.
- The LLM **only fills a draft** — it never decides policy or submits.
- A separate Python layer validates: valid leave code, balance available,
  document-required policy (e.g. sick leave over 2 days), weekend check.
- Missing or ambiguous details produce a **clarifying question** instead of a
  guess.

## Setup

```bash
pip install -r requirements.txt
```

To use the real model, set your key (the offline mode below needs no key):

```bash
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Run it

```bash
# Single message via the model (needs ANTHROPIC_API_KEY)
python leave_parser.py -m "need 2 days sick leave from tomorrow, medical note attached"

# Offline rule-based fallback — no API key required
python leave_parser.py --mock -m "wfh tomorrow, kid is unwell"

# Built-in example suite (add --mock for offline)
python leave_parser.py --demo --mock

# Pin the date for repeatable demos
python leave_parser.py --demo --mock --today 2026-06-08
```

## How it maps to the full design

| This spike | Production (design doc) |
|------------|--------------------------|
| `parse_with_llm` (Claude, structured output) | The AI extraction layer |
| `ParsedLeave` / `LeaveRequest` models | The structured contract (section 5) |
| `validate()` | The deterministic rules layer |
| `confirmation_card()` | The Teams Adaptive Card |
| `ABSENCE_CODES`, `MOCK_BALANCES` | Live HRMS lookups |
| `--mock` rule parser | Not shipped — only the LLM path is real |

## Notes

- Model: `claude-opus-4-8` via the Anthropic SDK, using structured outputs so
  the response always matches the schema.
- The offline `--mock` parser is a crude keyword/date matcher used only so the
  spike runs without a key. It is not part of the real design — the LLM handles
  messy phrasing far better.
- If the model call fails (no key, no network), the script prints a note and
  falls back to the offline parser so the demo never dead-ends.
