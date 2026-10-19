"""Runtime configuration, all from environment variables."""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

log = logging.getLogger("config")

# Fixed business date keeps days-past-due and allowed promise dates stable
# between rehearsal and the live demo. Set to the demo day.
BUSINESS_DATE = date.fromisoformat(os.getenv("BUSINESS_DATE", "2026-10-23"))

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

# Caller key (used by the Salesforce Named Credential) and a separate admin key.
API_KEY = os.getenv("API_KEY", "dev-key-change-me")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "dev-admin-key-change-me")

VERIFICATION_TTL_MINUTES = int(os.getenv("VERIFICATION_TTL_MINUTES", "15"))
MAX_VERIFICATION_ATTEMPTS = int(os.getenv("MAX_VERIFICATION_ATTEMPTS", "3"))

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

# Synthetic: every member has a CU checking account on file except these, so the
# demo can show the "no account to debit" path. Grace Huang (L-50005).
NO_CHECKING_MEMBERS = {"M-1005"}

if API_KEY.startswith("dev-") or ADMIN_API_KEY.startswith("dev-"):
    log.warning("Using default dev API keys. Set API_KEY and ADMIN_API_KEY before deploying.")
