"""Configuration from the environment, with saas/api/.env loaded first.

Loaded once, before anything reads os.environ (app/__init__.py imports this
module first), so a key in .env behaves exactly like an exported variable.
Real environment variables win over .env.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

API_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(API_ROOT / ".env", override=False)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def public_url() -> str:
    """Where the web app is reached from a browser -- builds OAuth redirect
    URIs and the links inside emails."""
    return env("RECOVA_PUBLIC_URL", "http://localhost:3000").rstrip("/")


def google_enabled() -> bool:
    return bool(env("GOOGLE_CLIENT_ID") and env("GOOGLE_CLIENT_SECRET"))


def smtp_enabled() -> bool:
    return bool(env("SMTP_HOST"))
