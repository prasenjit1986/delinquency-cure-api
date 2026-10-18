"""Generate deterministic synthetic data for the delinquency-cure prototype.

All people, numbers, emails and phones are fictional:
- emails use example.com, phones use the 555-01xx fictional range.

Three files mimic three separate source systems with NO shared clean key,
so identity resolution in Data 360 has real work to do:
- core_members.csv          (core banking: system of record for identity)
- collections_accounts.csv  (collections system: delinquency + policy inputs)
- digital_contact_prefs.csv (digital banking: channel + consent, keyed by its own user id)

Deliberate identity problems in digital_contact_prefs.csv:
- emails in different case, phones in different formats
- a few members missing entirely (no consent on record -> must not be texted)
- duplicate profiles for some members (old email + new email)
- nickname / spelling variants of first names

Usage: python scripts/generate_data.py [--out data] [--business-date 2026-10-23]
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42

FIRST = ["Dana", "Marcus", "Priya", "Luis", "Grace", "Tom", "Aisha", "Ken", "Elena", "Sam",
         "Nora", "Raj", "Olivia", "Ben", "Mei", "Carlos", "Hannah", "Victor", "Zoe", "Ivan",
         "Leah", "Omar", "Ruth", "Jake", "Tara", "Felix", "Nadia", "Paul", "Isla", "Derek"]
LAST = ["Whitfield", "Okafor", "Raman", "Delgado", "Huang", "Brennan", "Siddiqui", "Tanaka",
        "Novak", "Pereira", "Lindqvist", "Mehta", "Garrison", "Kowalski", "Fontaine", "Ibarra",
        "Castillo", "Yamada", "Osei", "Morrow"]
NICKNAMES = {"Samuel": "Sam", "Thomas": "Tom", "Kenneth": "Ken", "Benjamin": "Ben",
             "Jacob": "Jake", "Victor": "Vic", "Derek": "Derrick", "Olivia": "Liv"}
ZIPS = ["95112", "95134", "94538", "94555", "95014", "94086", "95050", "94040"]
PRODUCTS = [("AUTO", 0.45), ("PERSONAL", 0.25), ("CREDIT_CARD", 0.20), ("HELOC", 0.10)]
PAYMENT_BY_PRODUCT = {"AUTO": (310, 720), "PERSONAL": (150, 480), "CREDIT_CARD": (35, 210),
                      "HELOC": (420, 1150)}
LATE_FEE = {"AUTO": 25.0, "PERSONAL": 20.0, "CREDIT_CARD": 29.0, "HELOC": 35.0}

# Hand-picked demo personas (first rows) so the demo is predictable.
# (first, last, dob, zip, product, dpd, skip_used_months_ago, broken_promise, opened_months_ago, flag)
PERSONAS = [
    ("Dana", "Whitfield", "1988-04-12", "95112", "AUTO", 12, None, False, 30, ""),          # happy path, all options
    ("Marcus", "Okafor", "1979-09-30", "94538", "AUTO", 24, 5, False, 48, ""),             # skip-a-pay used 5 mo ago
    ("Priya", "Raman", "1992-01-08", "95014", "PERSONAL", 9, None, True, 22, ""),           # broke a promise recently
    ("Luis", "Delgado", "1984-06-21", "94086", "CREDIT_CARD", 17, None, False, 60, ""),     # card: no skip-a-pay
    ("Grace", "Huang", "1990-11-02", "95134", "PERSONAL", 6, None, False, 3, ""),           # account too new for skip
    ("Tom", "Brennan", "1975-03-15", "95050", "AUTO", 19, None, False, 36, "HARDSHIP_REVIEW"),  # suppressed
    ("Aisha", "Siddiqui", "1987-12-27", "94040", "HELOC", 33, None, False, 72, ""),         # 30+ DPD, out of scope
    ("Ken", "Tanaka", "1981-07-04", "94555", "AUTO", 27, None, False, 40, ""),              # close to 30: short window
]


# Fixed (not random) so the identity-resolution cases always exist.
NO_DIGITAL_PROFILE = {20, 33, 47}
DUPLICATE_PROFILE = {12, 27, 41}


def phone(n: int) -> str:
    return f"+1408555{n:04d}"[:12]


def fmt_phone_variant(e164: str, style: int) -> str:
    digits = e164[-10:]
    if style == 0:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if style == 1:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--business-date", default="2026-10-23")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bd = date.fromisoformat(args.business_date)
    rng = random.Random(SEED)

    members, accounts, prefs = [], [], []
    n_members = 60

    for i in range(n_members):
        mid = f"M-{1001 + i}"
        aid = f"L-{50001 + i}"
        if i < len(PERSONAS):
            first, last, dob, zip_, product, dpd, skip_ago, broken, opened_ago, flag = PERSONAS[i]
        else:
            first = rng.choice(FIRST + list(NICKNAMES))
            last = rng.choice(LAST)
            dob = (date(1960, 1, 1) + timedelta(days=rng.randint(0, 365 * 40))).isoformat()
            zip_ = rng.choice(ZIPS)
            r = rng.random()
            acc = 0.0
            product = "AUTO"
            for p, w in PRODUCTS:
                acc += w
                if r <= acc:
                    product = p
                    break
            roll = rng.random()
            if roll < 0.72:
                dpd = rng.randint(1, 29)
            elif roll < 0.88:
                dpd = 0
            else:
                dpd = rng.randint(30, 55)
            skip_ago = rng.choice([None, None, None, 4, 9, 14, 20])
            broken = rng.random() < 0.12
            opened_ago = rng.randint(2, 96)
            flag = ""
            if dpd and rng.random() < 0.07:
                flag = rng.choice(["HARDSHIP_REVIEW", "BANKRUPTCY", "CEASE_CONTACT", "DISPUTE"])

        e164 = phone(100 + i)
        email = f"{first}.{last}{i}@example.com".lower()
        lo, hi = PAYMENT_BY_PRODUCT[product]
        monthly = round(rng.uniform(lo, hi), 2)
        principal = round(monthly * rng.uniform(12, 60), 2)
        if dpd == 0:
            past_due, fee = 0.0, 0.0
        else:
            past_due = monthly if dpd < 30 else round(monthly * 2, 2)
            fee = LATE_FEE[product] if dpd >= 10 else 0.0
        opened = bd - timedelta(days=int(opened_ago * 30.4))
        last_skip = "" if skip_ago is None else (bd - timedelta(days=int(skip_ago * 30.4))).isoformat()
        broken_date = (bd - timedelta(days=rng.randint(10, 80))).isoformat() if broken else ""

        members.append({
            "member_id": mid, "first_name": first, "last_name": last, "date_of_birth": dob,
            "email": email, "mobile_phone": e164, "postal_code": zip_,
            "member_since": (opened - timedelta(days=rng.randint(0, 2000))).isoformat(),
        })
        accounts.append({
            "account_id": aid, "member_id": mid, "product_type": product,
            "opened_date": opened.isoformat(), "principal_balance": principal,
            "monthly_payment": monthly, "past_due_amount": past_due, "late_fee": fee,
            "days_past_due": dpd,
            "next_due_date": (bd + timedelta(days=30 - (dpd % 30 if dpd else 0))).isoformat(),
            "last_skip_pay_date": last_skip, "last_broken_promise_date": broken_date,
            "account_flag": flag,
        })

        # Digital banking export: its own key, messy identity data.
        if i in NO_DIGITAL_PROFILE:
            continue  # no digital profile -> no consent on record
        style = rng.randint(0, 2)
        dfirst = NICKNAMES.get(first, first) if rng.random() < 0.25 else first
        demail = email.upper() if rng.random() < 0.2 else email
        channel = rng.choice(["SMS", "SMS", "EMAIL", "EMAIL", "PHONE"])
        sms_consent = channel == "SMS" or rng.random() < 0.4
        if i < len(PERSONAS):  # demo personas: reachable by SMS link
            channel, sms_consent = "SMS", True
        prefs.append({
            "digital_user_id": f"DB-{rng.randint(100000, 999999)}",
            "first_name": dfirst, "last_name": last, "email": demail,
            "phone": fmt_phone_variant(e164, style), "preferred_channel": channel,
            "sms_consent": str(sms_consent).lower(), "email_consent": "true",
            "voice_ai_consent": str(rng.random() < 0.3).lower(),
            "quiet_hours_start": "21:00", "quiet_hours_end": "08:00",
            "last_login": (bd - timedelta(days=rng.randint(0, 120))).isoformat(),
        })
        if i in DUPLICATE_PROFILE:  # stale duplicate profile
            prefs.append({**prefs[-1],
                          "digital_user_id": f"DB-{rng.randint(100000, 999999)}",
                          "email": f"{first}{last}.old@example.com".lower(),
                          "preferred_channel": "EMAIL", "sms_consent": "false",
                          "last_login": (bd - timedelta(days=rng.randint(400, 900))).isoformat()})

    for name, rows in [("core_members.csv", members), ("collections_accounts.csv", accounts),
                       ("digital_contact_prefs.csv", prefs)]:
        with open(out / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {out / name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
