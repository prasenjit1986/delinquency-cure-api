"""Collections policy: pure, deterministic functions.

This is the point of the whole service. The agent never decides what it is
allowed to offer. It asks this module (through the API) and can only offer
what comes back as eligible. Every write is re-checked here server-side, so
a prompt-injected or hallucinated offer is rejected even if the agent tries it.

The rules below are synthetic and illustrative, not any real institution's policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

IN_SCOPE_MIN_DPD = 1
IN_SCOPE_MAX_DPD = 29

SUPPRESSION_FLAGS = {"HARDSHIP_REVIEW", "BANKRUPTCY", "CEASE_CONTACT", "DISPUTE", "ATTORNEY"}

SKIP_PAY_PRODUCTS = {"AUTO", "PERSONAL"}
SKIP_PAY_FEE = 35.00
SKIP_PAY_COOLDOWN_DAYS = 365
SKIP_PAY_MIN_ACCOUNT_AGE_DAYS = 180
BROKEN_PROMISE_LOOKBACK_DAYS = 90

# Weekly autopay plan: split the amount to cure into weekly debits from the member's
# own checking account at the credit union. The last debit must land before the
# account reaches 30 DPD (typically when it is reported to the credit bureaus).
PLAN_MAX_PAYMENTS = 4
PLAN_MIN_PAYMENTS = 2
PLAN_INTERVAL_DAYS = 7
PLAN_FIRST_DEBIT_OFFSET_DAYS = 1      # first debit is the next day


@dataclass
class Account:
    account_id: str
    member_id: str
    product_type: str
    opened_date: date
    monthly_payment: float
    past_due_amount: float
    late_fee: float
    days_past_due: int
    next_due_date: date
    last_skip_pay_date: date | None
    last_broken_promise_date: date | None
    flags: set[str] = field(default_factory=set)
    funding_account: str = ""   # masked CU checking on file, e.g. "Checking ••4821"; "" = none

    @property
    def amount_to_cure(self) -> float:
        return round(self.past_due_amount + self.late_fee, 2)


@dataclass
class Option:
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
    max_payments: int = 0
    funding_account: str = ""


@dataclass
class Decision:
    accepted: bool
    reason_code: str = ""
    reason: str = ""


def in_scope(acct: Account) -> bool:
    return IN_SCOPE_MIN_DPD <= acct.days_past_due <= IN_SCOPE_MAX_DPD


def suppressed(acct: Account) -> bool:
    return bool(acct.flags & SUPPRESSION_FLAGS)


def promise_window_days(dpd: int) -> int:
    """Latest promise date: aim to cure before the account rolls to 30 DPD,
    but never give fewer than 3 days."""
    return max(3, min(14, 30 - dpd))


def latest_promise_date(acct: Account, today: date) -> date:
    return today + timedelta(days=promise_window_days(acct.days_past_due))


def plan_max_payments(dpd: int) -> int:
    """How many weekly debits fit before the account reaches 30 DPD.
    Debit k (0-based) lands at day dpd + 1 + 7k, which must be <= 29."""
    room = (IN_SCOPE_MAX_DPD - dpd - PLAN_FIRST_DEBIT_OFFSET_DAYS)
    if room < 0:
        return 0
    return min(PLAN_MAX_PAYMENTS, room // PLAN_INTERVAL_DAYS + 1)


def plan_schedule(acct: Account, today: date, payments: int) -> list[tuple[date, float]]:
    """Equal debits rounded to cents; the last one absorbs the rounding difference
    so the total is exactly the amount to cure."""
    total = acct.amount_to_cure
    each = round(total / payments, 2)
    amounts = [each] * (payments - 1) + [round(total - each * (payments - 1), 2)]
    first = today + timedelta(days=PLAN_FIRST_DEBIT_OFFSET_DAYS)
    return [(first + timedelta(days=PLAN_INTERVAL_DAYS * k), amounts[k]) for k in range(payments)]


def gate(acct: Account) -> Decision:
    """Checks that apply before any self-serve option."""
    if suppressed(acct):
        return Decision(False, "ACCOUNT_SUPPRESSED",
                        "Account is under review. Route to a human; do not discuss options.")
    if acct.days_past_due == 0:
        return Decision(False, "NOT_DELINQUENT", "Account is current.")
    if acct.days_past_due > IN_SCOPE_MAX_DPD:
        return Decision(False, "DPD_30_PLUS", "Account is 30+ days past due; handled by collectors.")
    return Decision(True)


def evaluate_options(acct: Account, today: date, has_active_promise: bool,
                     has_active_plan: bool = False) -> list[Option]:
    g = gate(acct)
    human = Option("HARDSHIP_REVIEW", "Talk to a member service rep about hardship assistance",
                   eligible=True, requires_human=True)
    if not g.accepted:
        return [human]

    opts: list[Option] = [
        Option("PAY_NOW", "Pay the past-due amount now", eligible=True, amount=acct.amount_to_cure),
    ]

    # Promise to pay
    latest = latest_promise_date(acct, today)
    if has_active_plan:
        opts.append(Option("PROMISE_TO_PAY", "Promise to pay by a date", False,
                           "PLAN_ALREADY_ACTIVE", "A weekly payment plan is already set up."))
    elif has_active_promise:
        opts.append(Option("PROMISE_TO_PAY", "Promise to pay by a date", False,
                           "PTP_ALREADY_ACTIVE", "An active promise already exists."))
    elif acct.last_broken_promise_date and (today - acct.last_broken_promise_date).days <= BROKEN_PROMISE_LOOKBACK_DAYS:
        opts.append(Option("PROMISE_TO_PAY", "Promise to pay by a date", False,
                           "PTP_BROKEN_RECENTLY",
                           f"A promise was missed in the last {BROKEN_PROMISE_LOOKBACK_DAYS} days."))
    else:
        opts.append(Option("PROMISE_TO_PAY", "Promise to pay by a date", True,
                           min_amount=acct.amount_to_cure, latest_date=latest.isoformat()))

    # Weekly autopay plan from CU checking on file
    plan_label = "Split into weekly automatic payments from your checking account"
    n_max = plan_max_payments(acct.days_past_due)
    if has_active_plan:
        opts.append(Option("WEEKLY_AUTOPAY_PLAN", plan_label, False,
                           "PLAN_ALREADY_ACTIVE", "A weekly payment plan is already set up."))
    elif has_active_promise:
        opts.append(Option("WEEKLY_AUTOPAY_PLAN", plan_label, False,
                           "PTP_ALREADY_ACTIVE", "An active promise already exists."))
    elif not acct.funding_account:
        opts.append(Option("WEEKLY_AUTOPAY_PLAN", plan_label, False,
                           "PLAN_NO_CHECKING_ON_FILE",
                           "No credit union checking account on file to debit."))
    elif n_max < PLAN_MIN_PAYMENTS:
        opts.append(Option("WEEKLY_AUTOPAY_PLAN", plan_label, False,
                           "PLAN_TOO_CLOSE_TO_30_DPD",
                           "Not enough time for two weekly payments before the account reaches 30 days past due."))
    else:
        last = plan_schedule(acct, today, n_max)[-1][0]
        opts.append(Option("WEEKLY_AUTOPAY_PLAN", plan_label, True, amount=acct.amount_to_cure,
                           max_payments=n_max, latest_date=last.isoformat(),
                           funding_account=acct.funding_account))

    # Skip-a-pay
    if acct.product_type not in SKIP_PAY_PRODUCTS:
        opts.append(Option("SKIP_A_PAY", "Skip this month's payment", False,
                           "SKIP_PRODUCT_NOT_ELIGIBLE", f"Not available for {acct.product_type}."))
    elif (today - acct.opened_date).days < SKIP_PAY_MIN_ACCOUNT_AGE_DAYS:
        opts.append(Option("SKIP_A_PAY", "Skip this month's payment", False,
                           "SKIP_ACCOUNT_TOO_NEW", "Account must be open at least 6 months."))
    elif acct.last_skip_pay_date and (today - acct.last_skip_pay_date).days < SKIP_PAY_COOLDOWN_DAYS:
        opts.append(Option("SKIP_A_PAY", "Skip this month's payment", False,
                           "SKIP_USED_LAST_12M", "Skip-a-pay was used in the last 12 months."))
    else:
        opts.append(Option("SKIP_A_PAY", "Skip this month's payment", True, fee=SKIP_PAY_FEE))

    # Never self-serve. Listed so the agent has an explicit answer when asked.
    opts.append(Option("FEE_WAIVER", "Waive late fees", False, "HUMAN_APPROVAL_REQUIRED",
                       "Fee waivers need human approval.", requires_human=True))
    opts.append(human)
    return opts


def validate_promise(acct: Account, amount: float, promise_date: date, today: date,
                     has_active_promise: bool) -> Decision:
    opts = {o.code: o for o in evaluate_options(acct, today, has_active_promise)}
    ptp = opts.get("PROMISE_TO_PAY")
    if ptp is None:
        g = gate(acct)
        return Decision(False, g.reason_code, g.reason)
    if not ptp.eligible:
        return Decision(False, ptp.reason_code, ptp.reason)
    if promise_date < today:
        return Decision(False, "PTP_DATE_IN_PAST", "Promise date cannot be in the past.")
    if promise_date > date.fromisoformat(ptp.latest_date):
        return Decision(False, "PTP_DATE_OUT_OF_WINDOW",
                        f"Latest allowed date is {ptp.latest_date}.")
    if round(amount, 2) < ptp.min_amount:
        return Decision(False, "PTP_AMOUNT_BELOW_MINIMUM",
                        f"Minimum amount is {ptp.min_amount:.2f}.")
    return Decision(True)


def validate_plan(acct: Account, today: date, payments: int, has_active_promise: bool,
                  has_active_plan: bool) -> Decision:
    opts = {o.code: o for o in evaluate_options(acct, today, has_active_promise, has_active_plan)}
    pl = opts.get("WEEKLY_AUTOPAY_PLAN")
    if pl is None:
        g = gate(acct)
        return Decision(False, g.reason_code, g.reason)
    if not pl.eligible:
        return Decision(False, pl.reason_code, pl.reason)
    if payments < PLAN_MIN_PAYMENTS or payments > pl.max_payments:
        return Decision(False, "PLAN_PAYMENTS_OUT_OF_RANGE",
                        f"Choose between {PLAN_MIN_PAYMENTS} and {pl.max_payments} weekly payments.")
    return Decision(True)


def validate_skip_pay(acct: Account, today: date, has_active_promise: bool,
                      has_active_plan: bool = False) -> Decision:
    if has_active_plan:
        return Decision(False, "PLAN_ALREADY_ACTIVE", "A weekly payment plan is already set up.")
    opts = {o.code: o for o in evaluate_options(acct, today, has_active_promise)}
    sk = opts.get("SKIP_A_PAY")
    if sk is None:
        g = gate(acct)
        return Decision(False, g.reason_code, g.reason)
    if not sk.eligible:
        return Decision(False, sk.reason_code, sk.reason)
    return Decision(True)
