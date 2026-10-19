"""Collections & Policy API (synthetic) for the delinquency-cure prototype.

Represents the credit union's collections system as an EXTERNAL system of record.
Salesforce (Agentforce actions via Named Credential + External Services) calls it.

Design rules this service enforces, regardless of what the agent says:
1. Nothing about a debt is disclosed until the member is verified for that account.
2. The agent can only offer options this service returns as eligible.
3. Every write (promise, skip-a-pay) is re-validated server-side against policy.
4. Suppression flags (hardship, bankruptcy, cease contact, dispute) stop self-serve.
5. Every decision is logged with a reason code (see /v1/audit).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pathlib import Path

from fastapi import Response
from fastapi.responses import PlainTextResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from . import config, policy, store

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.load()
    log.info(json.dumps({"event": "startup", "business_date": config.BUSINESS_DATE.isoformat(),
                         "accounts": len(store.state.accounts)}))
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Collections & Policy API (synthetic)",
    version="1.0.0",
    description=(
        "Synthetic collections system for the early-delinquency cure prototype. "
        "System of record for delinquency status, eligible options, promises to pay and "
        "account suppression flags. All data is fictional."
    ),
)
app.openapi_version = "3.0.3"   # Salesforce External Services expects OpenAPI 3.0

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_key(key: str | None = Depends(api_key_header)) -> str:
    if key == config.API_KEY:
        return "salesforce"
    if key == config.ADMIN_API_KEY:
        return "admin"
    raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


def require_admin(caller: str = Depends(require_key)) -> str:
    if caller != "admin":
        raise HTTPException(status_code=403, detail="Admin key required")
    return caller


@app.middleware("http")
async def access_log(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    response.headers["X-Request-Id"] = rid
    # No DOB, ZIP or amounts in access logs. Paths contain account ids only.
    log.info(json.dumps({"event": "http", "request_id": rid, "method": request.method,
                         "path": request.url.path, "status": response.status_code, "ms": ms}))
    return response


# ---------- models (flat, no nullable fields: keeps External Services happy) ----------

class DelinquentAccount(BaseModel):
    account_id: str
    member_id: str
    product_type: str
    days_past_due: int
    past_due_amount_snapshot: float = Field(
        description="Snapshot for segmentation only. Never quote to a member; use /summary.")
    snapshot_as_of: str


class DelinquencyList(BaseModel):
    business_date: str
    count: int
    accounts: list[DelinquentAccount]


class VerifyRequest(BaseModel):
    account_id: str = Field(examples=["L-50001"])
    date_of_birth: str = Field(description="YYYY-MM-DD", examples=["1988-04-12"])
    postal_code: str = Field(examples=["95112"])


class VerifyResponse(BaseModel):
    verified: bool
    account_id: str = Field(default="", description="The verified account. Empty unless verified; "
                            "the agent locks the conversation to this account.")
    verification_id: str = ""
    expires_at: str = ""
    attempts_remaining: int = 0
    message: str = ""


class AccountSummary(BaseModel):
    account_id: str
    product_type: str
    days_past_due: int
    past_due_amount: float
    late_fee: float
    amount_to_cure: float
    next_due_date: str
    self_serve_allowed: bool
    reason_code: str = ""


class OptionOut(BaseModel):
    code: str
    label: str
    eligible: bool
    reason_code: str = ""
    reason: str = ""
    amount: float = 0.0
    min_amount: float = 0.0
    latest_date: str = ""
    fee: float = 0.0
    requires_human: bool = False
    max_payments: int = Field(default=0, description="WEEKLY_AUTOPAY_PLAN only: most weekly debits allowed")
    funding_account: str = Field(default="", description="WEEKLY_AUTOPAY_PLAN only: masked CU checking to debit")


class PlanRequest(BaseModel):
    payments: int = Field(ge=2, le=4, description="Number of weekly debits", examples=[3])
    preview: bool = Field(default=True, description="True: return the exact schedule without creating "
                          "anything. False: create the plan (only after the member says yes).")
    member_authorized: bool = Field(default=False, description="Must be true when preview is false: "
                                    "the member explicitly agreed to the debits read back to them.")
    channel: str = Field(default="AGENT_CHAT", examples=["AGENT_CHAT"])


class OptionsResponse(BaseModel):
    account_id: str
    business_date: str
    eligible_codes: list[str]
    options: list[OptionOut]


class PromiseRequest(BaseModel):
    amount: float = Field(gt=0, examples=[597.16])
    promise_date: str = Field(description="YYYY-MM-DD", examples=["2026-10-30"])
    channel: str = Field(default="AGENT_CHAT", examples=["AGENT_CHAT"])


class WriteResult(BaseModel):
    accepted: bool
    id: str = ""
    status: str = ""
    reason_code: str = ""
    message: str = ""


class FlagRequest(BaseModel):
    flag: str = Field(description="HARDSHIP_REVIEW | CEASE_CONTACT | DISPUTE | BANKRUPTCY | ATTORNEY")
    note: str = ""


# ---------- helpers ----------

def _account(account_id: str) -> policy.Account:
    acct = store.state.accounts.get(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    return acct


def _require_verified(account_id: str, verification_id: str | None, caller: str) -> None:
    if not store.check_verification(verification_id or "", account_id):
        store.audit({"caller": caller, "action": "DISCLOSURE_BLOCKED", "account_id": account_id,
                     "reason_code": "VERIFICATION_REQUIRED"})
        raise HTTPException(status_code=403,
                            detail="VERIFICATION_REQUIRED: verify the member for this account first")


# ---------- endpoints ----------

@app.get("/health", tags=["ops"], operation_id="health")
def health() -> dict:
    return {"status": "ok", "business_date": config.BUSINESS_DATE.isoformat()}


_SFDC_SPEC = Path(__file__).resolve().parent.parent / "openapi" / "collections-api.openapi.json"


@app.get("/openapi-sfdc.json", include_in_schema=False)
def openapi_for_salesforce() -> Response:
    """OpenAPI 3.0 spec for Salesforce External Services (the built-in /openapi.json is 3.1).
    Public on purpose: it describes the API but contains no data or secrets."""
    return Response(_SFDC_SPEC.read_text(), media_type="application/json")


@app.get("/v1/delinquencies", response_model=DelinquencyList, tags=["outreach"],
         operation_id="listDelinquencies",
         summary="Daily list of early-delinquent accounts eligible for outreach (1-29 DPD, not suppressed)")
def list_delinquencies(caller: str = Depends(require_key)) -> DelinquencyList:
    rows = [
        DelinquentAccount(account_id=a.account_id, member_id=a.member_id,
                          product_type=a.product_type, days_past_due=a.days_past_due,
                          past_due_amount_snapshot=a.amount_to_cure,
                          snapshot_as_of=config.BUSINESS_DATE.isoformat())
        for a in store.state.accounts.values()
        if policy.in_scope(a) and not policy.suppressed(a)
    ]
    store.audit({"caller": caller, "action": "LIST_DELINQUENCIES", "count": len(rows)})
    return DelinquencyList(business_date=config.BUSINESS_DATE.isoformat(), count=len(rows),
                           accounts=rows)


@app.get("/v1/delinquencies.csv", response_class=PlainTextResponse, tags=["outreach"],
         include_in_schema=False)
def list_delinquencies_csv(caller: str = Depends(require_key)) -> str:
    data = list_delinquencies(caller)
    lines = ["account_id,member_id,product_type,days_past_due,past_due_amount_snapshot,snapshot_as_of"]
    lines += [f"{r.account_id},{r.member_id},{r.product_type},{r.days_past_due},"
              f"{r.past_due_amount_snapshot},{r.snapshot_as_of}" for r in data.accounts]
    return "\n".join(lines) + "\n"


@app.post("/v1/verifications", response_model=VerifyResponse, tags=["member"],
          operation_id="verifyMember",
          summary="Right-party verification. Must succeed before any debt detail is disclosed")
def verify(body: VerifyRequest, caller: str = Depends(require_key)) -> VerifyResponse:
    acct = _account(body.account_id)
    member = store.state.members[acct.member_id]
    attempts = store.state.failed_attempts.get(acct.account_id, 0)
    if attempts >= config.MAX_VERIFICATION_ATTEMPTS:
        store.audit({"caller": caller, "action": "VERIFY", "account_id": acct.account_id,
                     "result": "LOCKED"})
        return VerifyResponse(verified=False, attempts_remaining=0,
                              message="Too many attempts. Route to a human.")
    ok = (body.date_of_birth.strip() == member.date_of_birth
          and body.postal_code.strip()[:5] == member.postal_code)
    if not ok:
        store.state.failed_attempts[acct.account_id] = attempts + 1
        remaining = config.MAX_VERIFICATION_ATTEMPTS - attempts - 1
        store.audit({"caller": caller, "action": "VERIFY", "account_id": acct.account_id,
                     "result": "FAILED", "attempts_remaining": remaining})
        # Do not say which field was wrong.
        return VerifyResponse(verified=False, attempts_remaining=remaining,
                              message="Details did not match.")
    v = store.create_verification(acct.account_id)
    store.audit({"caller": caller, "action": "VERIFY", "account_id": acct.account_id,
                 "result": "VERIFIED"})
    return VerifyResponse(verified=True, account_id=acct.account_id,
                          verification_id=v.verification_id,
                          expires_at=v.expires_at.isoformat(timespec="seconds"),
                          attempts_remaining=config.MAX_VERIFICATION_ATTEMPTS)


@app.get("/v1/accounts/{account_id}/summary", response_model=AccountSummary, tags=["member"],
         operation_id="getAccountSummary",
         summary="Live amount due. The only source the agent may quote amounts from")
def account_summary(account_id: str,
                    x_verification_id: str = Header(default="", alias="X-Verification-Id"),
                    caller: str = Depends(require_key)) -> AccountSummary:
    acct = _account(account_id)
    _require_verified(account_id, x_verification_id, caller)
    g = policy.gate(acct)
    store.audit({"caller": caller, "action": "SUMMARY", "account_id": account_id,
                 "self_serve_allowed": g.accepted, "reason_code": g.reason_code})
    return AccountSummary(account_id=acct.account_id, product_type=acct.product_type,
                          days_past_due=acct.days_past_due, past_due_amount=acct.past_due_amount,
                          late_fee=acct.late_fee, amount_to_cure=acct.amount_to_cure,
                          next_due_date=acct.next_due_date.isoformat(),
                          self_serve_allowed=g.accepted, reason_code=g.reason_code)


@app.get("/v1/accounts/{account_id}/options", response_model=OptionsResponse, tags=["member"],
         operation_id="getEligibleOptions",
         summary="Options the member is eligible for. The agent may offer ONLY eligible ones")
def options(account_id: str,
            x_verification_id: str = Header(default="", alias="X-Verification-Id"),
            caller: str = Depends(require_key)) -> OptionsResponse:
    acct = _account(account_id)
    _require_verified(account_id, x_verification_id, caller)
    opts = policy.evaluate_options(acct, config.BUSINESS_DATE,
                                   store.active_promise(account_id) is not None,
                                   store.active_plan(account_id) is not None)
    eligible = [o.code for o in opts if o.eligible]
    store.audit({"caller": caller, "action": "OPTIONS", "account_id": account_id,
                 "eligible": eligible,
                 "ineligible": {o.code: o.reason_code for o in opts if not o.eligible}})
    return OptionsResponse(account_id=account_id, business_date=config.BUSINESS_DATE.isoformat(),
                           eligible_codes=eligible, options=[OptionOut(**o.__dict__) for o in opts])


@app.post("/v1/accounts/{account_id}/promises", response_model=WriteResult, tags=["member"],
          operation_id="createPromiseToPay",
          summary="Record a promise to pay. Re-validated against policy; out-of-policy promises are rejected")
def create_promise(account_id: str, body: PromiseRequest,
                   x_verification_id: str = Header(default="", alias="X-Verification-Id"),
                   idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                   caller: str = Depends(require_key)) -> WriteResult:
    acct = _account(account_id)
    _require_verified(account_id, x_verification_id, caller)
    if idempotency_key and idempotency_key in store.state.idempotency:
        return WriteResult(**store.state.idempotency[idempotency_key])
    try:
        pdate = date.fromisoformat(body.promise_date)
    except ValueError:
        return WriteResult(accepted=False, reason_code="PTP_BAD_DATE",
                           message="promise_date must be YYYY-MM-DD")
    d = policy.validate_promise(acct, body.amount, pdate, config.BUSINESS_DATE,
                                store.active_promise(account_id) is not None)
    if d.accepted and store.active_plan(account_id):
        d = policy.Decision(False, "PLAN_ALREADY_ACTIVE", "A weekly payment plan is already set up.")
    if not d.accepted:
        result = WriteResult(accepted=False, reason_code=d.reason_code, message=d.reason)
    else:
        p = store.Promise(store.new_id("PTP"), account_id, round(body.amount, 2),
                          pdate.isoformat(), body.channel,
                          store.now().isoformat(timespec="seconds"))
        store.state.promises[p.promise_id] = p
        result = WriteResult(accepted=True, id=p.promise_id, status=p.status,
                             message=f"Promise recorded for {p.amount:.2f} by {p.promise_date}.")
    store.audit({"caller": caller, "action": "CREATE_PROMISE", "account_id": account_id,
                 "requested_amount": body.amount, "requested_date": body.promise_date,
                 "accepted": result.accepted, "reason_code": result.reason_code})
    if idempotency_key:
        store.state.idempotency[idempotency_key] = result.model_dump()
    return result


@app.post("/v1/accounts/{account_id}/skip-pay-enrollments", response_model=WriteResult,
          tags=["member"], operation_id="enrollSkipAPay",
          summary="Enroll in skip-a-pay. Re-validated against policy")
def enroll_skip_pay(account_id: str,
                    x_verification_id: str = Header(default="", alias="X-Verification-Id"),
                    caller: str = Depends(require_key)) -> WriteResult:
    acct = _account(account_id)
    _require_verified(account_id, x_verification_id, caller)
    d = policy.validate_skip_pay(acct, config.BUSINESS_DATE,
                                 store.active_promise(account_id) is not None,
                                 store.active_plan(account_id) is not None)
    if d.accepted:
        eid = store.new_id("SKP")
        store.state.skip_pays[account_id] = eid
        acct.last_skip_pay_date = config.BUSINESS_DATE
        result = WriteResult(accepted=True, id=eid, status="ENROLLED",
                             message=f"Skip-a-pay enrolled. Fee {policy.SKIP_PAY_FEE:.2f}.")
    else:
        result = WriteResult(accepted=False, reason_code=d.reason_code, message=d.reason)
    store.audit({"caller": caller, "action": "SKIP_PAY", "account_id": account_id,
                 "accepted": result.accepted, "reason_code": result.reason_code})
    return result


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _plan_text(acct: policy.Account, schedule: list[tuple[date, float]]) -> str:
    debits = "; ".join(f"{_money(a)} on {d.isoformat()}" for d, a in schedule)
    return (f"{len(schedule)} weekly payments from {acct.funding_account}: {debits}. "
            f"Total {_money(sum(a for _, a in schedule))}, which brings the past-due balance to zero. "
            f"Your regular monthly payment of {_money(acct.monthly_payment)} is still due on "
            f"{acct.next_due_date.isoformat()} and is not part of this plan. "
            "If a payment can't be made, it won't be retried and no fee is charged, "
            "but the plan will end.")


@app.post("/v1/accounts/{account_id}/payment-plans", response_model=WriteResult, tags=["member"],
          operation_id="createWeeklyPaymentPlan",
          summary="Weekly autopay plan from CU checking. Call with preview=true first; "
                  "create only after the member agrees")
def create_plan(account_id: str, body: PlanRequest,
                x_verification_id: str = Header(default="", alias="X-Verification-Id"),
                caller: str = Depends(require_key)) -> WriteResult:
    acct = _account(account_id)
    _require_verified(account_id, x_verification_id, caller)
    d = policy.validate_plan(acct, config.BUSINESS_DATE, body.payments,
                             store.active_promise(account_id) is not None,
                             store.active_plan(account_id) is not None)
    if d.accepted and not body.preview and not body.member_authorized:
        d = policy.Decision(False, "PLAN_AUTHORIZATION_REQUIRED",
                            "Read the schedule to the member and get a clear yes before creating the plan.")
    if not d.accepted:
        result = WriteResult(accepted=False, reason_code=d.reason_code, message=d.reason)
    else:
        schedule = policy.plan_schedule(acct, config.BUSINESS_DATE, body.payments)
        text = _plan_text(acct, schedule)
        if body.preview:
            result = WriteResult(accepted=True, status="PREVIEW", message=text)
        else:
            plan = store.PaymentPlan(
                store.new_id("PLN"), account_id, acct.funding_account,
                [{"date": dt.isoformat(), "amount": a} for dt, a in schedule],
                {"text": text, "authorized_at": store.now().isoformat(timespec="seconds"),
                 "channel": body.channel, "caller": caller})
            store.state.plans[plan.plan_id] = plan
            result = WriteResult(accepted=True, id=plan.plan_id, status=plan.status,
                                 message=text + " A copy of this authorization will be sent to you.")
    store.audit({"caller": caller, "action": "PLAN_PREVIEW" if body.preview else "PLAN_CREATE",
                 "account_id": account_id, "payments": body.payments,
                 "accepted": result.accepted, "reason_code": result.reason_code})
    return result


@app.post("/v1/accounts/{account_id}/flags", response_model=WriteResult, tags=["member"],
          operation_id="setAccountFlag",
          summary="Suppress self-serve and outreach (hardship, cease contact, dispute, bankruptcy, attorney)")
def set_flag(account_id: str, body: FlagRequest,
             x_verification_id: str = Header(default="", alias="X-Verification-Id"),
             caller: str = Depends(require_key)) -> WriteResult:
    """Does NOT require verification: a cease-contact or bankruptcy notice must be honored
    immediately. It is recorded with verified=false and confirmed later by a human."""
    acct = _account(account_id)
    flag = body.flag.strip().upper()
    if flag not in policy.SUPPRESSION_FLAGS:
        return WriteResult(accepted=False, reason_code="UNKNOWN_FLAG",
                           message=f"flag must be one of {sorted(policy.SUPPRESSION_FLAGS)}")
    verified = store.check_verification(x_verification_id, account_id)
    acct.flags.add(flag)
    store.state.flags_log.append({"account_id": account_id, "flag": flag, "verified": verified,
                                  "note": body.note[:500]})
    store.audit({"caller": caller, "action": "SET_FLAG", "account_id": account_id,
                 "flag": flag, "verified": verified})
    return WriteResult(accepted=True, id=store.new_id("FLG"), status="SUPPRESSED",
                       message="Self-serve and outreach stopped for this account.")


@app.get("/v1/audit", tags=["ops"], operation_id="getAudit", include_in_schema=False)
def get_audit(limit: int = Query(default=50, le=500), caller: str = Depends(require_admin)) -> list:
    return list(store.state.audit)[:limit]


@app.post("/v1/admin/reset", tags=["ops"], include_in_schema=False)
def reset(caller: str = Depends(require_admin)) -> dict:
    store.load()
    return {"status": "reset", "accounts": len(store.state.accounts)}
