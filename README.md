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
| Auth         | **Demo username/password** + server session cookie (no SSO)     |

## Run

```bash
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload     # run from the repo root
# open http://localhost:8000
```

### Logging in
Opening the app shows a **login screen**. Demo accounts (seeded into `leave.db`):

| Username  | Password     | Role · Department              |
|-----------|--------------|--------------------------------|
| `asha`    | `asha123`    | **Manager** · Engineering      |
| `ravi`    | `ravi123`    | **Manager** · Sales            |
| `meera`   | `meera123`   | Employee · Engineering         |
| `prakash` | `prakash123` | Employee · Sales               |
| `krupal`  | `krupal123`  | Employee · Engineering         |

Approvals route **by department**: a leave request goes to the manager(s) in the
requester's department. So Krupal/Meera (Engineering) → **Asha**, and Prakash
(Sales) → **Ravi**. A manager only sees and can act on their own department's
requests (acting on another department's request is rejected). New signups pick
a department on the form, which determines their approver.

Sign-in creates a server session (HTTP-only cookie); the backend derives your
identity from it, so you only see and act on your own leave. **Logout** clears
it. Passwords are SHA-256 hashed in the `credentials` table — demo-grade auth,
not production-hardened; change the seeded logins there for anything real.

**Sign up** — the login screen has a *Create a new account* link. You can
register as an **Employee** or **Manager** (pick the role on the form); the
account is created with a starting balance and you're signed straight in.

**Forgot / reset password** — the login screen also has a *Forgot password?*
link. Enter a username or email and the app emails a **single-use, 1-hour**
reset link; opening it (`/?reset=<token>`) shows a page to set a new password.
With email configured the link is delivered; otherwise it's logged to the
server console / outbox.

**Manager approvals** — a Manager sees a **Pending approvals** panel listing
every *other* employee's pending request, with **Approve** / **Reject** buttons.
Approving marks the request Approved; rejecting marks it Rejected and returns the
days to that employee's balance. The endpoints (`/api/approvals`, `/api/approve`,
`/api/reject`) require the Manager role (403 otherwise). Asha is a seeded Manager;
or sign up a new one. A manager comment can be added when approving/rejecting;
it's stored, emailed to the employee, and shown in their history.

**Sick-leave auto-approval (SLA)** — a **Sick** leave left pending longer than
**3 days** with no manager action is auto-approved (with an explanatory comment +
email). The check runs lazily whenever approvals/history are read. To demo it
without waiting, lower the threshold: set `AUTO_APPROVE_SICK_DAYS=0` (in `.env`
or the environment) and any pending sick leave is approved on the next read.

**Email notifications** — signup includes an **Email** field. When a leave is
submitted, the requester gets a confirmation email and every Manager gets an
"approval needed" email; on approve/reject the requester gets a decision email.
By default (no SMTP configured) emails are **logged to the server console** and
stored in the `email_outbox` table — the demo works with zero setup. To send
**real** email, set these env vars before running (Gmail example, using an
[App Password](https://support.google.com/accounts/answer/185833)):

**Easiest — a `.env` file** (loaded automatically on startup, no need to export
each session): copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env     # then edit .env   (Windows: copy .env.example .env)
```

Or set them as environment variables instead:

```bash
export SMTP_HOST=smtp.gmail.com        # PowerShell: $env:SMTP_HOST="smtp.gmail.com"
export SMTP_PORT=587
export SMTP_USER=you@gmail.com
export SMTP_PASS=your-16-char-app-password
export SMTP_FROM=you@gmail.com         # optional
```

`.env` is gitignored, so credentials are never committed. On signup the new user
gets a confirmation email; on apply/approve/reject the relevant people are
notified.

**Not receiving email? Diagnose it:**

```bash
python -m backend.test_email you@example.com
```

This prints whether SMTP is configured and tries a real send, showing the exact
error if it fails. The running server also logs its mode on startup:
`[email] REAL send enabled via …` (good) or `[email] DEMO mode …` (no SMTP, so
nothing is delivered — set up `.env`). For Gmail the usual gotchas are: using
your normal password instead of a 16-char **App Password**, **2-Step
Verification** not enabled, or a network that blocks outbound port 587.

To actually receive the manager emails, the Manager account needs a real address
— change the seeded ones in `backend/db.py` (`EMPLOYEES`) or sign up a Manager
with your own email.

**AI is automatic — nothing to configure.** On startup the app tries Claude; if
it's reachable it uses the model, otherwise it switches to a built-in
deterministic parser for the session (and stops retrying). So:

- **To use real Claude:** log in once with your subscription — `claude`
  (or `ant auth login`). Do **not** set `ANTHROPIC_API_KEY`.
- **Restricted / offline machine:** just run `uvicorn …` — it auto-falls back to
  the offline parser. Set `LEAVE_OFFLINE=1` to skip the Claude probe entirely.

Offline mode matches date *patterns* rather than free text — use clear formats
like `11 August 2026`, `11/August/2026`, `11-Aug-2026`, `20/08/2026`, or a range
`11 to 15 August 2026`. Vague phrases ("next month") are answered with a
clarifying question. Real Claude handles all free-form phrasing.

## What you can do

- **Apply leave** — "wfh tomorrow", "2 days sick from Monday", "half day casual Friday".
- **Check balance** — "what's my leave balance?"
- **View history** — "show my history", or filter: "show approved leaves".
- **Cancel** — "cancel #AB-10692" (restores the days to your balance).
- **Approve / reject** (Managers) — review and act on the team's pending leave.

Guardrails (deterministic, never the model): closed leave-code list, balance
checks, the medical-certificate rule for sick leave > 2 days, an **overlap check**
(you can't apply for leave on dates that overlap an existing Pending/Approved
request), and a confirmation gate — **nothing is submitted until you press Confirm**.

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
