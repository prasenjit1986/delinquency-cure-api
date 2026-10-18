"""In-memory store seeded from CSV. Resets on restart (fine for a prototype;
a real collections system is the durable system of record)."""
from __future__ import annotations

import csv
import secrets
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config
from .policy import Account


def _d(s: str) -> date | None:
    return date.fromisoformat(s) if s else None


@dataclass
class Member:
    member_id: str
    first_name: str
    last_name: str
    date_of_birth: str
    postal_code: str


@dataclass
class Verification:
    verification_id: str
    account_id: str
    expires_at: datetime


@dataclass
class Promise:
    promise_id: str
    account_id: str
    amount: float
    promise_date: str
    channel: str
    created_at: str
    status: str = "SCHEDULED"


@dataclass
class State:
    members: dict[str, Member] = field(default_factory=dict)
    accounts: dict[str, Account] = field(default_factory=dict)
    verifications: dict[str, Verification] = field(default_factory=dict)
    failed_attempts: dict[str, int] = field(default_factory=dict)
    promises: dict[str, Promise] = field(default_factory=dict)
    skip_pays: dict[str, str] = field(default_factory=dict)   # account_id -> enrollment id
    flags_log: list[dict] = field(default_factory=list)
    idempotency: dict[str, dict] = field(default_factory=dict)
    audit: deque = field(default_factory=lambda: deque(maxlen=500))


_lock = threading.Lock()
state = State()


def load(data_dir: Path | None = None) -> None:
    data_dir = data_dir or config.DATA_DIR
    s = State()
    with open(data_dir / "core_members.csv") as f:
        for r in csv.DictReader(f):
            s.members[r["member_id"]] = Member(r["member_id"], r["first_name"], r["last_name"],
                                               r["date_of_birth"], r["postal_code"])
    with open(data_dir / "collections_accounts.csv") as f:
        for r in csv.DictReader(f):
            s.accounts[r["account_id"]] = Account(
                account_id=r["account_id"], member_id=r["member_id"],
                product_type=r["product_type"], opened_date=date.fromisoformat(r["opened_date"]),
                monthly_payment=float(r["monthly_payment"]),
                past_due_amount=float(r["past_due_amount"]), late_fee=float(r["late_fee"]),
                days_past_due=int(r["days_past_due"]),
                next_due_date=date.fromisoformat(r["next_due_date"]),
                last_skip_pay_date=_d(r["last_skip_pay_date"]),
                last_broken_promise_date=_d(r["last_broken_promise_date"]),
                flags={r["account_flag"]} if r["account_flag"] else set(),
            )
    global state
    with _lock:
        state = s


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def create_verification(account_id: str) -> Verification:
    v = Verification(new_id("VER"), account_id,
                     now() + timedelta(minutes=config.VERIFICATION_TTL_MINUTES))
    with _lock:
        state.verifications[v.verification_id] = v
        state.failed_attempts.pop(account_id, None)
    return v


def check_verification(verification_id: str, account_id: str) -> bool:
    v = state.verifications.get(verification_id or "")
    return bool(v and v.account_id == account_id and v.expires_at > now())


def active_promise(account_id: str) -> Promise | None:
    for p in state.promises.values():
        if p.account_id == account_id and p.status == "SCHEDULED":
            return p
    return None


def audit(event: dict) -> None:
    event = {"ts": now().isoformat(timespec="seconds"), **event}
    with _lock:
        state.audit.appendleft(event)
