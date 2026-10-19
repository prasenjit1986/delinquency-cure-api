# Collections & Policy API (synthetic)

External system for the **early-delinquency cure** prototype (Salesforce Data 360 + Agentforce panel).
It plays the credit union's **collections system**: the system of record for delinquency status,
what a member is allowed to be offered, promises to pay, and "stop" flags.

All data is synthetic. Names are invented, emails use `example.com`, phones use the fictional `555-01xx` range.

## Panel materials

| What | File |
|---|---|
| Panel deck (PDF, 11 slides) | [docs/panel/Vera_Panel_Deck.pdf](docs/panel/Vera_Panel_Deck.pdf) |
| Architecture page (diagram, where each rule is enforced, gaps) | [docs/architecture.html](docs/architecture.html) |
| Testing Center results (two runs, defects and fixes) | [eval/TESTING_CENTER_RESULTS.md](eval/TESTING_CENTER_RESULTS.md) |
| Screenshots from the org | [evidence/](evidence/) |
| How AI was used | [AI_USAGE_LOG.md](AI_USAGE_LOG.md) |

## What this proves (the tested uncertainty)

> Can the agent be trusted to offer **only** what policy allows, and never disclose a debt to the wrong person?

The answer this design gives: **don't trust the prompt. Enforce it at the system of record.**

| Rule | Where it is enforced | Test |
|---|---|---|
| No debt details before right-party verification | API returns `403 VERIFICATION_REQUIRED` | `test_no_disclosure_without_verification` |
| Verification is per account, expires in 15 min, locks after 3 failures | API | `test_verification_for_one_account...`, `test_lockout...` |
| Wrong details don't reveal which field was wrong | API | `test_wrong_details_do_not_say_which_field` |
| Agent may offer only eligible options | `/options` returns eligible + ineligible with reason codes | `test_ineligible_options_have_reason_codes` |
| Fee waivers are never self-serve | Always `eligible:false, requires_human:true` | `test_happy_path_options` |
| Out-of-policy promises are rejected even if the agent tries | Promise re-validated server-side | `test_out_of_policy_promise_rejected` |
| Hardship / bankruptcy / cease-contact / dispute stop self-serve and outreach | Suppression flags | `test_suppressed_account_only_offers_human`, `test_cease_contact...` |
| Amounts are quoted live, never from a stale copy | `/summary` is the only quotable source; the outreach list carries a labeled *snapshot* | design + field naming |
| Every decision is traceable | Audit log with reason codes; JSON access logs without DOB/ZIP | `test_audit_records_blocked_disclosure` |

Run them: `pip install -r requirements-dev.txt && pytest -q` (25 tests).

## Ownership boundary

| System | Owns | Does not own |
|---|---|---|
| **This API (collections system, external)** | Delinquency status, eligibility rules, promises, suppression flags | Conversations, routing, identity matching across channels |
| **Data 360** | Unified member profile across core, collections and digital banking; consent and channel; the daily outreach segment; grounding context | Live balances, policy decisions, the record of a promise |
| **Agentforce** | Verify, explain, offer eligible options, capture the member's choice, escalate | Deciding eligibility, inventing offers, approving hardship |
| **Service Cloud** | Hardship queue, human review, case record | Credit decisions |

## Endpoints

All require header `X-API-Key`. Endpoints marked 🔒 also require `X-Verification-Id` from `/v1/verifications`.

| Method | Path | Agent action | Purpose |
|---|---|---|---|
| GET | `/v1/delinquencies` | `listDelinquencies` | Today's 1–29 DPD accounts, not suppressed (feeds Data 360 segment) |
| POST | `/v1/verifications` | `verifyMember` | DOB + ZIP check, returns a 15-minute verification id |
| GET 🔒 | `/v1/accounts/{id}/summary` | `getAccountSummary` | Live amount due. Only source the agent may quote |
| GET 🔒 | `/v1/accounts/{id}/options` | `getEligibleOptions` | Eligible options with limits; ineligible ones with reason codes |
| POST 🔒 | `/v1/accounts/{id}/promises` | `createPromiseToPay` | Re-validated; supports `Idempotency-Key` |
| POST 🔒 | `/v1/accounts/{id}/skip-pay-enrollments` | `enrollSkipAPay` | Re-validated |
| POST | `/v1/accounts/{id}/flags` | `setAccountFlag` | Stop self-serve + outreach. No verification needed: a "stop" must be honored immediately |
| GET | `/v1/audit` | — (admin key) | Last 500 decisions, for the demo |
| POST | `/v1/admin/reset` | — (admin key) | Reload seed data between rehearsals |
| GET | `/health` | — | Liveness |

Business-rule rejections return **200 with `accepted:false` + `reason_code`** so the agent can explain them.
Auth and verification failures return **401/403**. This is a deliberate trade-off: easier agent handling,
while enforcement stays on the server.

## Policy rules (synthetic)

- **In scope:** 1–29 days past due, no suppression flag.
- **Pay now:** past-due amount + late fee.
- **Promise to pay:** amount ≥ past-due + fee; latest date = today + max(3, min(14, 30 − DPD)) days;
  one active promise at a time; not allowed if a promise was missed in the last 90 days.
- **Skip-a-pay:** AUTO and PERSONAL only; account open ≥ 6 months; not used in the last 12 months; $35 fee.
- **Fee waiver:** never self-serve.
- **Hardship:** always available, always a human.

## Demo personas (business date 2026-10-23)

| Account | Member | DOB | ZIP | Situation | Expected behavior |
|---|---|---|---|---|---|
| L-50001 | Dana Whitfield | 1988-04-12 | 95112 | Auto, 12 DPD | All options; promise window to 2026-11-06 |
| L-50002 | Marcus Okafor | 1979-09-30 | 94538 | Auto, 24 DPD, skipped 5 months ago | Skip-a-pay refused (`SKIP_USED_LAST_12M`) |
| L-50003 | Priya Raman | 1992-01-08 | 95014 | Personal, missed a promise recently | Promise refused (`PTP_BROKEN_RECENTLY`) |
| L-50004 | Luis Delgado | 1984-06-21 | 94086 | Credit card, 17 DPD | Skip-a-pay refused (`SKIP_PRODUCT_NOT_ELIGIBLE`) |
| L-50005 | Grace Huang | 1990-11-02 | 95134 | Personal, opened 3 months ago | Skip-a-pay refused (`SKIP_ACCOUNT_TOO_NEW`) |
| L-50006 | Tom Brennan | 1975-03-15 | 95050 | Hardship review flag | Only a human; excluded from outreach |
| L-50007 | Aisha Siddiqui | 1987-12-27 | 94040 | HELOC, 33 DPD | Out of scope; excluded from outreach |
| L-50008 | Ken Tanaka | 1981-07-04 | 94555 | Auto, 27 DPD | Promise window only 3 days (to 2026-10-26) |

## Synthetic data for Data 360 (`data/`)

Three files from three "systems", with **no shared clean key**:

- `core_members.csv`: core banking, keyed by `member_id` (identity source of truth)
- `collections_accounts.csv`: collections, keyed by `account_id` → `member_id`
- `digital_contact_prefs.csv`: digital banking, keyed by its own `digital_user_id`; **no member_id**

Deliberate identity problems in the digital file (verified after generation):
- 14 emails in uppercase; phones in three formats (`(408) 555-0100`, `408-555-0101`, `4085550102`) vs E.164 in core
- 5 nickname variants (e.g. "Thomas" → "Tom", "Derek" → "Derrick")
- 3 stale duplicate profiles with an old email: M-1013, M-1028, M-1042
- 3 members with no digital profile: M-1021, M-1034, M-1048 (no consent on record → must not be texted)

Identity resolution should match on normalized phone and email, not name.

Regenerate: `python scripts/generate_data.py --business-date 2026-10-23` (seeded, same output every run).

## Run locally

```bash
pip install -r requirements-dev.txt
API_KEY=demo-key ADMIN_API_KEY=demo-admin uvicorn app.main:app --reload
# docs: http://localhost:8000/docs
```

## Deploy (Render free tier)

1. Push this folder to a GitHub repo.
2. Render → New → Blueprint → pick the repo (uses `render.yaml`). Keys are generated for you;
   copy `API_KEY` from the service's Environment tab.
3. Check `https://<your-app>.onrender.com/health`.
4. Export the spec with your real URL:
   `PUBLIC_BASE_URL=https://<your-app>.onrender.com python scripts/export_openapi.py`

**Free-tier risk:** the service sleeps when idle; the first call can take 30–60 seconds, which can
time out an agent action. Hit `/health` a few minutes before the panel, or use a paid instance for the day.
State is in memory: it resets on restart. Call `/v1/admin/reset` before each rehearsal.

## Connect Salesforce (outline; menu labels vary by release)

1. **External Credential** with a custom auth protocol and a principal holding the API key;
   add a custom header `X-API-Key` that reads the key from the credential.
   Grant the principal to the agent's user through a permission set.
2. **Named Credential** pointing at the Render URL, using that External Credential.
3. **External Services:** add a service from `openapi/collections-api.openapi.yaml`, select the Named Credential.
   The 7 operations become invocable actions.
4. **Agentforce:** add the actions to a topic (e.g. "Resolve past-due balance").
   Instructions should say, at minimum:
   - Verify first. Never state an amount, due date or option before `verifyMember` returns `verified: true`.
   - Quote amounts only from `getAccountSummary`.
   - Offer only codes listed in `eligible_codes`. If asked for anything else, say it needs a person.
   - If the member mentions hardship, bankruptcy, a lawyer, a dispute, or asks to stop contact:
     call `setAccountFlag` and hand off to the hardship queue.
5. **Testing Center:** run the tricky-prompt test set against the agent and record pass rates.

**Open risk to test in the org:** the agent must carry the `verification_id` from `verifyMember` into
later actions. If it doesn't do this reliably, wrap the calls in a Flow that stores the id in a
conversation variable instead of relying on the model to repeat it.

## Known limits (state these, don't hide them)

- In-memory state; no database. The real collections system is the durable record.
- DOB + ZIP is a demo-grade check. Production would use an OTP to the phone on file (e.g. Twilio Verify).
- Payments are not taken here. `PAY_NOW` would hand off to a payment processor (designed, not built).
- One reason code is returned per rejection (the first rule that fails).
- Audit timestamps use the real clock; policy uses the fixed `BUSINESS_DATE`.

## How AI was used

Code, data generator, tests and this README were drafted with Claude and then checked by running them.
Verification found real defects in the generated output, for example:
- FastAPI emits OpenAPI 3.1 constructs (`exclusiveMinimum: 0` as a number, `anyOf` with null) that
  Salesforce External Services (OpenAPI 3.0) can reject. Caught by schema validation; fixed in
  `scripts/export_openapi.py` and locked in by `tests/test_openapi.py`.
- A cleanup command matched its own shell and killed it, so config files were silently missing. Caught by listing the folder.
- The first data generator used random chance for duplicate and missing profiles and produced **zero**
  duplicates, while the README claimed some existed. Caught by checking the data against the README;
  the cases are now fixed, not random.

Keep adding to this list as you build. The panel will ask what you accepted, rejected and verified.
