"""Tests for the trust rules. These are the claims you make to the panel, so each
one has a test. Run: pytest -q"""
import os

os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin")

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

H = {"X-API-Key": config.API_KEY}
ADMIN = {"X-API-Key": config.ADMIN_API_KEY}

# Demo personas from scripts/generate_data.py
DANA = ("L-50001", "1988-04-12", "95112")     # AUTO, 12 DPD, all options
MARCUS = ("L-50002", "1979-09-30", "94538")   # skip-a-pay used 5 months ago
PRIYA = ("L-50003", "1992-01-08", "95014")    # broke a promise recently
LUIS = ("L-50004", "1984-06-21", "94086")     # credit card
GRACE = ("L-50005", "1990-11-02", "95134")    # account too new for skip
TOM = ("L-50006", "1975-03-15", "95050")      # hardship flag -> suppressed
AISHA = ("L-50007", "1987-12-27", "94040")    # 33 DPD -> out of scope
KEN = ("L-50008", "1981-07-04", "94555")      # 27 DPD -> 3-day promise window


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.post("/v1/admin/reset", headers=ADMIN)
        yield c


def verify(c, persona):
    acct, dob, zip_ = persona
    r = c.post("/v1/verifications", headers=H,
               json={"account_id": acct, "date_of_birth": dob, "postal_code": zip_})
    assert r.status_code == 200 and r.json()["verified"], r.text
    return {**H, "X-Verification-Id": r.json()["verification_id"]}


def opts(c, persona, hv):
    r = c.get(f"/v1/accounts/{persona[0]}/options", headers=hv)
    assert r.status_code == 200
    return {o["code"]: o for o in r.json()["options"]}


# --- auth ---

def test_requires_api_key(client):
    assert client.get("/v1/delinquencies").status_code == 401
    assert client.get("/v1/delinquencies", headers={"X-API-Key": "wrong"}).status_code == 401


def test_audit_requires_admin_key(client):
    assert client.get("/v1/audit", headers=H).status_code == 403
    assert client.get("/v1/audit", headers=ADMIN).status_code == 200


# --- outreach list ---

def test_delinquency_list_scope(client):
    rows = client.get("/v1/delinquencies", headers=H).json()["accounts"]
    ids = {r["account_id"] for r in rows}
    assert all(1 <= r["days_past_due"] <= 29 for r in rows)
    assert DANA[0] in ids
    assert TOM[0] not in ids, "suppressed accounts must not be in the outreach list"
    assert AISHA[0] not in ids, "30+ DPD is out of scope"


# --- right-party verification before disclosure ---

def test_no_disclosure_without_verification(client):
    for path in ("summary", "options"):
        r = client.get(f"/v1/accounts/{DANA[0]}/{path}", headers=H)
        assert r.status_code == 403
        assert "VERIFICATION_REQUIRED" in r.text


def test_verification_for_one_account_does_not_unlock_another(client):
    hv = verify(client, DANA)
    assert client.get(f"/v1/accounts/{MARCUS[0]}/summary", headers=hv).status_code == 403


def test_wrong_details_do_not_say_which_field(client):
    r = client.post("/v1/verifications", headers=H,
                    json={"account_id": DANA[0], "date_of_birth": "1900-01-01",
                          "postal_code": DANA[2]}).json()
    assert r["verified"] is False and r["message"] == "Details did not match."
    assert r["account_id"] == "", "a failed check must not echo the account back"


def test_successful_verification_returns_the_locked_account(client):
    r = client.post("/v1/verifications", headers=H,
                    json={"account_id": DANA[0], "date_of_birth": DANA[1],
                          "postal_code": DANA[2]}).json()
    assert r["verified"] is True and r["account_id"] == DANA[0]


def test_lockout_after_three_failures(client):
    for _ in range(3):
        client.post("/v1/verifications", headers=H,
                    json={"account_id": DANA[0], "date_of_birth": "1900-01-01",
                          "postal_code": "00000"})
    r = client.post("/v1/verifications", headers=H,
                    json={"account_id": DANA[0], "date_of_birth": DANA[1],
                          "postal_code": DANA[2]}).json()
    assert r["verified"] is False and r["attempts_remaining"] == 0


# --- eligibility ---

def test_happy_path_options(client):
    o = opts(client, DANA, verify(client, DANA))
    assert o["PAY_NOW"]["eligible"] and o["PROMISE_TO_PAY"]["eligible"] and o["SKIP_A_PAY"]["eligible"]
    assert o["FEE_WAIVER"]["eligible"] is False and o["FEE_WAIVER"]["requires_human"]


@pytest.mark.parametrize("persona,code,reason", [
    (MARCUS, "SKIP_A_PAY", "SKIP_USED_LAST_12M"),
    (PRIYA, "PROMISE_TO_PAY", "PTP_BROKEN_RECENTLY"),
    (LUIS, "SKIP_A_PAY", "SKIP_PRODUCT_NOT_ELIGIBLE"),
    (GRACE, "SKIP_A_PAY", "SKIP_ACCOUNT_TOO_NEW"),
])
def test_ineligible_options_have_reason_codes(client, persona, code, reason):
    o = opts(client, persona, verify(client, persona))
    assert o[code]["eligible"] is False and o[code]["reason_code"] == reason


def test_suppressed_account_only_offers_human(client):
    hv = verify(client, TOM)
    r = client.get(f"/v1/accounts/{TOM[0]}/options", headers=hv).json()
    assert r["eligible_codes"] == ["HARDSHIP_REVIEW"]
    s = client.get(f"/v1/accounts/{TOM[0]}/summary", headers=hv).json()
    assert s["self_serve_allowed"] is False and s["reason_code"] == "ACCOUNT_SUPPRESSED"


def test_promise_window_shrinks_near_30_dpd(client):
    o = opts(client, KEN, verify(client, KEN))
    assert o["PROMISE_TO_PAY"]["latest_date"] == "2026-10-26"  # 27 DPD -> 3 days


# --- server-side enforcement of writes ---

def test_promise_within_policy_accepted(client):
    hv = verify(client, DANA)
    o = opts(client, DANA, hv)["PROMISE_TO_PAY"]
    r = client.post(f"/v1/accounts/{DANA[0]}/promises", headers=hv,
                    json={"amount": o["min_amount"], "promise_date": o["latest_date"]}).json()
    assert r["accepted"] and r["id"].startswith("PTP-")


@pytest.mark.parametrize("amount,date_,reason", [
    (1.00, "2026-10-30", "PTP_AMOUNT_BELOW_MINIMUM"),
    (9999, "2027-03-01", "PTP_DATE_OUT_OF_WINDOW"),
    (9999, "2026-10-01", "PTP_DATE_IN_PAST"),
])
def test_out_of_policy_promise_rejected(client, amount, date_, reason):
    hv = verify(client, DANA)
    r = client.post(f"/v1/accounts/{DANA[0]}/promises", headers=hv,
                    json={"amount": amount, "promise_date": date_}).json()
    assert r["accepted"] is False and r["reason_code"] == reason


def test_second_promise_rejected(client):
    hv = verify(client, DANA)
    o = opts(client, DANA, hv)["PROMISE_TO_PAY"]
    body = {"amount": o["min_amount"], "promise_date": o["latest_date"]}
    assert client.post(f"/v1/accounts/{DANA[0]}/promises", headers=hv, json=body).json()["accepted"]
    r = client.post(f"/v1/accounts/{DANA[0]}/promises", headers=hv, json=body).json()
    assert r["accepted"] is False and r["reason_code"] == "PTP_ALREADY_ACTIVE"


def test_idempotent_promise(client):
    hv = {**verify(client, DANA), "Idempotency-Key": "abc-123"}
    o = opts(client, DANA, hv)["PROMISE_TO_PAY"]
    body = {"amount": o["min_amount"], "promise_date": o["latest_date"]}
    a = client.post(f"/v1/accounts/{DANA[0]}/promises", headers=hv, json=body).json()
    b = client.post(f"/v1/accounts/{DANA[0]}/promises", headers=hv, json=body).json()
    assert a == b and a["accepted"]


def test_skip_pay_rejected_when_ineligible(client):
    hv = verify(client, MARCUS)
    r = client.post(f"/v1/accounts/{MARCUS[0]}/skip-pay-enrollments", headers=hv).json()
    assert r["accepted"] is False and r["reason_code"] == "SKIP_USED_LAST_12M"


def test_promise_on_suppressed_account_rejected(client):
    hv = verify(client, TOM)
    r = client.post(f"/v1/accounts/{TOM[0]}/promises", headers=hv,
                    json={"amount": 9999, "promise_date": "2026-10-25"}).json()
    assert r["accepted"] is False and r["reason_code"] == "ACCOUNT_SUPPRESSED"


# --- suppression ---

def test_cease_contact_works_without_verification_and_stops_outreach(client):
    r = client.post(f"/v1/accounts/{DANA[0]}/flags", headers=H,
                    json={"flag": "CEASE_CONTACT", "note": "member texted STOP"}).json()
    assert r["accepted"]
    ids = {x["account_id"] for x in client.get("/v1/delinquencies", headers=H).json()["accounts"]}
    assert DANA[0] not in ids
    hv = verify(client, DANA)
    assert opts(client, DANA, hv).keys() == {"HARDSHIP_REVIEW"}


def test_audit_records_blocked_disclosure(client):
    client.get(f"/v1/accounts/{DANA[0]}/summary", headers=H)
    events = client.get("/v1/audit", headers=ADMIN).json()
    assert any(e["action"] == "DISCLOSURE_BLOCKED" for e in events)
