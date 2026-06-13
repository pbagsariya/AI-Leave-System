# AI Leave Assistant — Demo

Apply for leave in plain English. Type a message like *"2 days sick leave from
tomorrow, medical note attached"* and the assistant extracts the dates, leave
type, and reason, validates against policy, shows a confirmation card, and
submits on your approval.

This is the simplified, single-laptop demo of the system in
[`AI_Leave_System_Design.md`](AI_Leave_System_Design.md). See
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the phased plan.

## Stack (kept intentionally minimal)

| Layer        | Choice                                                            |
|--------------|------------------------------------------------------------------|
| Frontend     | Static HTML + Tailwind (CDN) + vanilla JS — no build step        |
| Orchestrator | Python **FastAPI** (also serves the frontend, same origin)       |
| AI           | **Claude** via your subscription (OAuth, **no API key**)         |
| Store        | **SQLite** (`leave.db`) — replaces Redis + Postgres + mock HRMS  |
| Identity     | **None** (a "logged in as" employee dropdown replaces SSO)       |

## Run

```bash
pip install -r backend/requirements.txt
claude                       # one-time: log in with your Claude subscription
                             # (or: ant auth login). Do NOT set ANTHROPIC_API_KEY.
uvicorn backend.main:app --reload     # run from the repo root
# open http://localhost:8000
```

No subscription / offline? It still runs — the assistant falls back to a
deterministic rule parser. Force that mode with `LEAVE_OFFLINE=1`.

## What you can do

- **Apply leave** — "wfh tomorrow", "2 days sick from Monday", "half day casual Friday".
- **Check balance** — "what's my leave balance?"
- **View history** — "show my history", or filter: "show approved leaves".
- **Cancel** — "cancel #AB-10692" (restores the days to your balance).

Guardrails (deterministic, never the model): closed leave-code list, balance
checks, the medical-certificate rule for sick leave > 2 days, and a
confirmation gate — **nothing is submitted until you press Confirm**.

## Layout

```
backend/
  main.py          FastAPI app: 5 endpoints + static mount
  db.py            SQLite schema, seed, and queries
  leave_logic.py   Pydantic contract, Claude extraction, validation
  requirements.txt
frontend/
  index.html       app shell
  app.js           chat state + API client (USE_MOCK flag for offline UI dev)
  styles.css
leave.db           created on first run, seeded with 3 employees
```

## API

| Method | Path             | Purpose                                  |
|--------|------------------|------------------------------------------|
| GET    | `/api/employees` | employee list (for the dropdown)         |
| GET    | `/api/balances`  | `?employee_id` → balances                |
| GET    | `/api/history`   | `?employee_id` → recent requests         |
| POST   | `/api/chat`      | message → extracted draft / card / reply |
| POST   | `/api/confirm`   | submit the pending draft                 |

`/api/chat` returns a `reply_type` of `confirmation`, `clarification`,
`policy_block`, `balance`, `history`, or `cancelled`.
