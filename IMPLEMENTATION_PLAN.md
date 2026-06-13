# AI Leave Assistant — Phased Implementation Plan (Demo)

A simplified, demo-only build of the AI-Powered Leave Application System
(`AI_Leave_System_Design.md`). This plan deliberately strips the production
stack down to the smallest thing that runs end-to-end on one laptop.

## Decisions applied (per request)

| Concern        | Production design        | **This demo**                                            |
|----------------|--------------------------|----------------------------------------------------------|
| Channel        | Microsoft Teams bot      | **Plain web chat page** (browser)                        |
| Identity / SSO | Azure AD / Entra SSO     | **Removed.** A simple "Logged in as" employee dropdown   |
| Orchestrator   | Python FastAPI           | **Python FastAPI** (kept)                                |
| AI             | Claude via API key       | **Claude via existing subscription** (OAuth, no API key) |
| State store    | Redis + PostgreSQL       | **SQLite** (single file `leave.db`)                      |
| HRMS connector | PeopleSoft CI / RPA      | **Mocked** in SQLite (balances + requests tables)        |
| Hosting        | Azure Container Apps     | **`uvicorn` localhost**                                  |

### Claude subscription auth (constraint #3 — no separate API key)

1. One-time: log in with your Claude subscription so an OAuth profile exists on
   the machine — either `claude` (Claude Code login) or `ant auth login`.
2. The backend then calls `anthropic.Anthropic()` **with no `api_key`**. The SDK
   resolves credentials from the environment / OAuth profile automatically.
3. The existing structured-output call works as-is:
   `client.messages.parse(model="claude-opus-4-8", output_format=ParsedLeave, ...)`.
4. Fallbacks already in `leave_parser.py`: if the model call fails (not logged
   in / offline) it drops to the offline `--mock` rule parser, so the demo never
   dead-ends. Keep that behavior.

> Note: do **not** set `ANTHROPIC_API_KEY` *and* rely on the subscription
> profile at the same time — unset the key so the profile is used.

### Target project layout

```
newapp_ing/
├─ leave.db                      # SQLite (created on first run)
├─ backend/
│  ├─ main.py                    # FastAPI app + routes + static mount
│  ├─ db.py                      # sqlite3 helpers + schema + seed
│  ├─ leave_logic.py            # ported from prototype/leave_parser.py
│  └─ requirements.txt           # fastapi, uvicorn, anthropic, pydantic
└─ frontend/
   ├─ index.html                 # single chat page (Tailwind via CDN)
   ├─ app.js                     # chat state + fetch() calls
   └─ styles.css                 # small overrides
```

Tech kept intentionally minimal: **no Node build step, no React** — Tailwind
from a CDN + vanilla JS, served as static files by FastAPI.

---

## Phase 1 — UI Mockup (static, no backend)

**Goal:** a clickable, good-looking mock of the whole experience with hardcoded
data. Used to lock the UX and the data the chat needs before writing logic.

### Build
- `frontend/index.html` single page with three regions:
  1. **Header** — app title + **employee dropdown** (mock list: Asha, Ravi, Meera). This replaces SSO/login.
  2. **Chat panel** — message bubbles (user right, bot left), a (fake) file-attach chip, text input + send.
  3. **Side panel** — leave balances (SICK 8, CASUAL 5, EARNED 12, COMP_OFF 2) and a short request-history list.
- Render every important **state** with static markup so they can be reviewed:
  - Bot **confirmation card** (Type / Dates / Comment / Attachment / balance-after) with **Confirm · Edit · Cancel** buttons.
  - **Clarification** bubble ("Which days next week, and what type of leave?").
  - **Policy-block** bubble ("Sick leave over 2 days needs a medical certificate.").
  - **Submitted** bubble ("✅ Submitted. Request #AB-10427 sent to your manager.").
  - Empty / loading / error states.
- Style with Tailwind CDN; keep it clean and responsive.

### Out of scope
No JS logic, no network calls, no real data — all hardcoded.

### Acceptance
- Stakeholder can click through and see each conversation state.
- The confirmation card shows exactly the fields the AI must produce (maps 1:1 to §5 of the design doc), confirming the API contract for later phases.

---

## Phase 2 — Frontend Implementation (interactive, mocked API)

**Goal:** make the mock real in the browser — state, interactivity, and `fetch()`
calls against a defined API contract that Phase 3 will implement. Use a local
mock/stub so the UI is fully testable before the backend exists.

### API contract (frozen here, implemented in Phase 3)
| Method | Path             | Body / Query                        | Returns |
|--------|------------------|-------------------------------------|---------|
| GET    | `/api/employees` | —                                   | `[{id, name}]` |
| GET    | `/api/balances`  | `?employee_id`                      | `{SICK, CASUAL, EARNED, COMP_OFF}` |
| GET    | `/api/history`   | `?employee_id`                      | `[{id, code, start, end, status}]` |
| POST   | `/api/chat`      | `{employee_id, message, session_id}`| `{session_id, reply_type, card?, question?, parsed?}` |
| POST   | `/api/confirm`   | `{session_id}`                      | `{request_id, status, balances}` |

`reply_type` ∈ `confirmation | clarification | policy_block | error`.

### Build (`frontend/app.js`)
- Chat state: message list, current `session_id`, selected `employee_id`.
- Send flow: append user bubble → `POST /api/chat` → render the right bubble/card based on `reply_type`.
- Confirmation card buttons:
  - **Confirm** → `POST /api/confirm` → render submitted bubble, refresh balances + history.
  - **Edit** → re-open input prefilled; **Cancel** → discard draft.
- On employee change → reload balances + history.
- Loading spinners, disabled send while in flight, error toast on failure.
- A `USE_MOCK = true` switch with in-file canned responses so the UI runs with no server.

### Acceptance
- Full happy path, clarification path, and policy-block path all work against the mock.
- Switching `USE_MOCK` to `false` later hits the real backend with zero other changes.

---

## Phase 3 — Backend Implementation (FastAPI + SQLite + Claude)

**Goal:** implement the frozen API contract for real: FastAPI orchestrator,
SQLite persistence, the ported validation rules, and Claude extraction via the
subscription.

### 3a. Data layer — `backend/db.py` (stdlib `sqlite3`)
Schema + seed on startup:
- `employees(id, name, timezone)`
- `balances(employee_id, code, days)` — seed SICK/CASUAL/EARNED/COMP_OFF.
- `leave_requests(id, employee_id, code, start_date, end_date, duration_days, comments, status, created_at)`
- `sessions(id, employee_id, draft_json, created_at)` — holds the pending parsed draft between `/api/chat` and `/api/confirm` (replaces Redis).
- `audit(id, employee_id, message, parsed_json, validation_json, created_at)` — original message + AI draft + validation for traceability.

### 3b. Logic — `backend/leave_logic.py` (port from `prototype/leave_parser.py`)
- Reuse `LeaveRequest`, `ParsedLeave`, `ABSENCE_CODES`, `validate()`, and the prompt builder **unchanged**.
- `parse_with_llm()` keeps `anthropic.Anthropic()` (no key) + `messages.parse(... output_format=ParsedLeave)`.
- Read balances/`DOC_REQUIRED_OVER_DAYS` from SQLite instead of the in-file dicts.
- Keep `parse_with_rules()` as the offline fallback.

### 3c. API — `backend/main.py` (FastAPI)
- Implement the 5 endpoints from the Phase-2 contract.
- `/api/chat`:
  1. Look up employee + balances.
  2. `parse_with_llm()` (fallback to rules on failure) → `ParsedLeave`.
  3. `validate()` against SQLite balances + doc policy.
  4. If `missing_or_ambiguous` → return `clarification` with the model's question.
  5. If validation errors (e.g. doc required) → return `policy_block`.
  6. Else persist draft to `sessions`, return `confirmation` card payload.
  7. Write an `audit` row.
- `/api/confirm`: load draft from `sessions`, insert into `leave_requests` (status `SUBMITTED`), decrement the balance, return `request_id` + new balances. **Submission only happens here, never from the model** (the confirmation gate from the design doc).
- Mount `frontend/` as static files so `uvicorn backend.main:app` serves the whole app at `http://localhost:8000`.
- CORS not needed (same origin).

### 3d. Run
```
pip install -r backend/requirements.txt
claude            # or: ant auth login   (one-time subscription login)
uvicorn backend.main:app --reload
```
Flip the frontend `USE_MOCK` to `false`.

### Acceptance
- End-to-end in the browser: type "need 2 days sick leave from tomorrow, medical note attached" → confirmation card → Confirm → balance drops from 8 → 6, request appears in history.
- "I'll be out next week" → clarification. "3 days sick from Monday" (no note) → policy block.
- Restarting the server keeps requests/balances (persisted in `leave.db`).
- Works on the subscription with no `ANTHROPIC_API_KEY`; degrades to the offline parser if not logged in.

---

## Suggested order & rough effort
1. **Phase 1** — ~0.5 day. Static UI, sign-off on states.
2. **Phase 2** — ~1 day. Interactivity against the mock; freeze the API contract.
3. **Phase 3** — ~1.5 days. DB + ported logic + FastAPI + Claude wiring.

Everything reuses the existing spike, so the AI/validation core is essentially
done — the work is the web layer, SQLite, and the subscription wiring.
