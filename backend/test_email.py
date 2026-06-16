"""
Email diagnostic. Tells you whether SMTP is configured and tries a real send,
printing the exact error if it fails.

Run from the repo root:
    python -m backend.test_email you@example.com

If you omit the address it sends to SMTP_USER.
"""

from __future__ import annotations

import os
import sys

from . import config, db, notify


def run() -> None:
    config.load_dotenv()
    db.init_db()

    to = sys.argv[1] if len(sys.argv) > 1 else os.getenv("SMTP_USER")
    host = os.getenv("SMTP_HOST")

    print("-" * 60)
    if host:
        print("SMTP configured:")
        print(f"  host = {host}:{os.getenv('SMTP_PORT', '587')}")
        print(f"  user = {os.getenv('SMTP_USER')}")
        print(f"  from = {os.getenv('SMTP_FROM') or os.getenv('SMTP_USER')}")
    else:
        print("NO SMTP configured -> DEMO mode: emails are only logged, never sent.")
        print("Create a .env (copy .env.example) with SMTP_HOST/PORT/USER/PASS, then re-run.")
        print("-" * 60)
        return

    if not to:
        print("No recipient. Usage: python -m backend.test_email you@example.com")
        print("-" * 60)
        return

    print(f"\nSending a test email to {to} ...\n")
    notify.send_email(to, "Leave Assistant - SMTP test",
                      "If you can read this in your inbox, SMTP is working correctly.")

    # report what was recorded
    import sqlite3
    c = sqlite3.connect(db.DB_PATH)
    row = c.execute("SELECT status FROM email_outbox ORDER BY id DESC LIMIT 1").fetchone()
    status = row[0] if row else "?"
    print("-" * 60)
    if status == "sent":
        print("RESULT: sent ✓  — check your inbox (and Spam the first time).")
    elif status == "failed":
        print("RESULT: failed ✗ — see the '[notify] SMTP send ... failed: <reason>' line above.")
        print("Common causes: wrong App Password, 2-Step Verification not enabled,")
        print("or your network blocks outbound port 587.")
    else:
        print(f"RESULT: {status}")
    print("-" * 60)


if __name__ == "__main__":
    run()
