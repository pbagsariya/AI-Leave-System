"""
Email notifications.

Works with zero setup: if no SMTP server is configured it logs the email to the
server console and records it in the email_outbox table (a demo mailbox). If you
set the SMTP_* environment variables it sends real email instead.

Enable real email (example for Gmail with an App Password):
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=you@gmail.com
    SMTP_PASS=your-16-char-app-password
    SMTP_FROM=you@gmail.com        # optional; defaults to SMTP_USER
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from . import db


def _console(to_email: str, subject: str, body: str) -> None:
    print(
        "\n===== EMAIL (demo) =====\n"
        f"To: {to_email}\nSubject: {subject}\n\n{body}\n"
        "========================\n"
    )


def send_email(to_email: str | None, subject: str, body: str) -> None:
    """Send (or log) one email. Never raises — notifications must not break the
    leave flow."""
    if not to_email:
        return

    host = os.getenv("SMTP_HOST")
    if not host:
        # demo mode: log + store, no real send
        _console(to_email, subject, body)
        db.log_email(to_email, subject, body, "logged")
        return

    try:
        msg = EmailMessage()
        msg["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "leave-assistant@example.com"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)

        port = int(os.getenv("SMTP_PORT", "587"))
        user = os.getenv("SMTP_USER")
        pw = os.getenv("SMTP_PASS")
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls(context=ssl.create_default_context())
            if user:
                s.login(user, pw)
            s.send_message(msg)
        db.log_email(to_email, subject, body, "sent")
    except Exception as exc:  # never let a mail failure break the request
        print(f"[notify] SMTP send to {to_email} failed: {exc}; logged instead")
        db.log_email(to_email, subject, body, "failed")
        _console(to_email, subject, body)


# ---- message builders ------------------------------------------------------

def notify_leave_submitted(employee: dict, managers: list[dict], req_id: str,
                           label: str, date_range: str) -> None:
    """Email the requester (confirmation) and each manager (action needed)."""
    send_email(
        employee.get("email"),
        f"Leave request {req_id} submitted",
        f"Hi {employee['name']},\n\n"
        f"Your leave request has been submitted and is pending approval.\n\n"
        f"  Type : {label}\n  When : {date_range}\n  Ref  : {req_id}\n\n"
        f"You'll be notified once your manager approves or rejects it.\n",
    )
    for mgr in managers:
        if mgr["id"] == employee["id"]:
            continue
        send_email(
            mgr.get("email"),
            f"Approval needed: {employee['name']} applied for leave",
            f"Hi {mgr['name']},\n\n"
            f"{employee['name']} ({employee.get('dept', '')}) has applied for leave.\n\n"
            f"  Type : {label}\n  When : {date_range}\n  Ref  : {req_id}\n\n"
            f"Open the Pending approvals panel to approve or reject this request.\n",
        )


def notify_decision(employee: dict, req_id: str, decision: str) -> None:
    """Email the requester that their leave was approved/rejected."""
    send_email(
        employee.get("email"),
        f"Leave request {req_id} {decision.lower()}",
        f"Hi {employee['name']},\n\n"
        f"Your leave request {req_id} has been {decision.lower()} by your manager.\n"
        + ("The days have been returned to your balance.\n" if decision == "Rejected" else ""),
    )
