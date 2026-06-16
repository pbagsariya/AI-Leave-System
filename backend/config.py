"""Tiny .env loader (no dependency). Shared by the app and the email tester."""

from __future__ import annotations

import os


def load_dotenv() -> None:
    """Load KEY=VALUE pairs from a .env file at the repo root into the
    environment (without overriding values already set in the real env)."""
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as exc:
        print(f"[config] could not read .env: {exc}")
