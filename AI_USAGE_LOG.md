# AI Usage Log

Running record for the panel's "Use of AI Tools" questions: where AI was used, what it produced,
what I accepted, rejected or changed, how I verified it, and where it was wrong.

**Tools:** Claude (chat + coding agent + in-app browser).
**Rule I follow:** AI drafts; I decide. Nothing goes into the demo without a test, a run, or a check against a source.

---

## 2026-10-16

### 1. Choosing the scenario
- **Used for:** Critique of two candidate use cases (appointment booking vs. early-delinquency cure) against the panel brief.
- **Output:** Recommended delinquency cure. Flagged weak points: my claim that "outbound doesn't work" contradicted my own outbound solution; sponsor mismatch (collections belongs to the Chief Lending Officer, not the CDO/CAO); missing compliance (Rosenthal Act, TCPA AI-voice consent, right-party verification, plus CCPA for California consumers, which I added); scope too wide for 20 minutes.
- **My judgment:** Accepted delinquency cure, based on a real anonymized Interface.ai deployment. Accepted cutting voice to phase 2. Decided the contact center is Salesforce Service Cloud.
- **Verification:** Compared against the brief's evaluation criteria myself. Legal points to be confirmed before I state them to the panel.
- **Where AI could mislead:** Legal and regulatory claims come from model knowledge, not a lawyer. I will cite only what I can source.
- **Open check on CCPA:** CCPA applies to for-profit businesses, and it exempts personal data already covered by GLBA (the federal law for financial institutions). A nonprofit credit union may fall outside CCPA for this data. The California Financial Information Privacy Act (CalFIPA) may be the state law that actually applies. Confirm before naming CCPA to the panel.

### 2. Checking the Salesforce environment
- **Used for:** Browsing my Salesforce org Setup to confirm Data 360 and Agentforce availability.
- **Output:** Found my first org was a **Starter Suite trial** with no Data 360 license and no Agent Builder access. It also carried a former employer's name as the company name.
- **My judgment:** Abandoned that org and created a Developer Edition. AI then confirmed the new org has Data Cloud licenses, Agentforce on, Testing Center and Prompt Builder.
- **Where AI was wrong or blocked:** The signup link it found (`developer.salesforce.com/signup`) returned a 404 in the app browser; I signed up through another browser.

### 3. Building the external Collections & Policy API
- **Used for:** Drafting a FastAPI service, synthetic data generator, OpenAPI spec exporter, 25 tests and README.
- **Output:** Server-side enforcement of verification-before-disclosure, eligible-options-only, re-validated writes, suppression flags and an audit log.
- **My judgment:**
  - Accepted the core design: policy enforced at the system of record, not in the prompt.
  - Accepted "200 + `accepted:false`" for business-rule rejections so the agent can explain them (trade-off: HTTP status alone no longer signals failure).
  - **To review myself:** the policy numbers (14-day promise window, $35 skip-a-pay fee, 90-day broken-promise lookback) are AI-invented placeholders. Check them against what I saw in the real deployment and adjust.
- **Verification:** Ran all tests (25 pass). Ran the API locally and walked the full flow with curl: blocked disclosure (403) → verify → options → out-of-policy promise rejected → in-policy promise accepted → audit log. Validated the OpenAPI spec against the 3.0 schema.
- **Where the AI output was wrong (caught by verification):**
  1. **OpenAPI version mismatch.** FastAPI generated OpenAPI 3.1 constructs (numeric `exclusiveMinimum`, `anyOf` with null, `examples` arrays) while Salesforce External Services expects 3.0. The schema validator rejected it; fixed in the exporter and locked in with a test.
  2. **Silent missing files.** A cleanup command (`pkill -f`) matched its own shell and killed it, so config files were never written and nothing reported an error. Caught by listing the folder.
  3. **README claimed data that didn't exist.** The generator used random chance for duplicate and missing digital profiles and produced zero duplicates, while the README described them. Caught by checking the data against the README; the cases are now fixed, not random.
  4. **Over-strict test.** My first spec test banned all `anyOf`, but FastAPI's built-in error schema legitimately uses it. Decided to flatten it anyway, since mixed types can break External Services import.
- **Known gaps I'm carrying forward:** DOB + ZIP is demo-grade verification (production = OTP). In-memory state. No payment processing. The agent passing `verification_id` between actions is untested in the org.

### 4. Data 360 permissions
- **Used for:** Assigning my user the Data 360 admin permissions through the in-app browser.
- **Output:** First tried **Data Cloud Admin**. Salesforce rejected it because that set needs the older "Customer Data Platform" license, which is disabled in a Developer Edition. Then tried **Data Cloud Architect** (same setup access, uses the active "Data Cloud" license). It turned out to be already assigned by provisioning.
- **My judgment:** No change needed; my user already has Data 360 setup access.
- **Where AI was wrong:** It picked the permission set by name, not by license. The license column showed the mismatch before the attempt. Lesson: check the license behind a permission set before assigning it.

### 5. Loading data into Data 360 (file upload)
- **Used for:** Choosing the ingestion route and configuring the Core Members data stream.
- **Output:** Checked available connectors in the org (File Upload, Ingestion API, S3, GCS, Salesforce CRM). Recommended File Upload now and Ingestion API as the production design.
- **My judgment:** Accepted File Upload for the demo. In the presentation I will say the daily feed is a snapshot and production uses the Ingestion API.
- **Where the platform's defaults were wrong (caught by review):** Data 360 auto-detected `postal_code` as **Number**. ZIP codes are identifiers, not numbers: as Number, a ZIP like 02134 would lose its leading zero and never match. Changed to Text before deploying.
- **Where Einstein's auto-mapping was incomplete (caught by review):** Einstein mapped only 4 of 11 needed fields (email, first name, last name, birth date). It missed the three things identity resolution depends on: the Individual's own key (`member_id` → Individual Id), the link from each email and phone back to the person (`member_id` → Party), and the phone itself. Without those, the email and phone would float unattached and no profiles could merge. Added 7 mappings by hand: Individual Id; Contact Point Email Id and Party; Contact Point Phone Id, Party, Telephone Number and Formatted E164 Phone Number.
- **Panel talking point:** "Einstein suggested a mapping that looked complete but would have produced zero unified profiles. I checked it against what identity resolution actually needs."

### 6. Cleaning the digital banking data at ingestion
- **Used for:** Primary key choice and formula fields on the Digital Contact Prefs stream.
- **Primary key:** `digital_user_id`. Checked the file first: all 60 unique; phone has 3 duplicates (the deliberate stale profiles). Using phone as the key would have silently overwritten those rows.
- **Formula fields added (tested on all three phone formats in the platform's tester before saving):**
  - `Phone E164` = `CONCAT("+1", CONCAT(LEFT(RIGHT(<phone without spaces/dashes>, 11), 3), RIGHT(<same>, 7)))` → `(408) 555-0100`, `408-555-0101` and `4085550102` all become `+1408555xxxx`, matching core banking.
  - `Email Normalized` = `LOWER(email)` → fixes the 14 uppercase emails.
- **Where AI's first attempt failed (caught by testing in the platform):**
  1. The obvious formula, `REPLACE(phone, "(", "")`, was rejected as "unmatched brackets": Salesforce's formula checker counts brackets inside quoted text. Using `CHAR(40)` for "(" was also rejected. Switched to an approach that needs no brackets: keep the last 7 digits and the 3 before them.
  2. Text typed into the formula box by automation wasn't registered until a real keystroke followed. Every formula looked "invalid" until that was found.
- **Where Einstein's auto-mapping was wrong:** It mapped the **raw** `email` (with uppercase values) to Email Address, ignoring the cleaned field. Removed it and mapped `Email Normalized` instead. Also mapped `Phone E164` (not raw phone) to both Formatted E164 and Telephone Number, so both sources store the same format.
- **Assumption to state on the panel:** the phone fix assumes US 10-digit numbers. International members would need a country-aware rule.
- **Not yet mapped:** consent fields (sms/email/voice). Needed for the outreach segment; to be handled with the segment.

### 7. Collections Accounts stream
- **Caught before deploy:** the stream had defaulted to category **Profile**. Loan records aren't people; as Profile, Data 360 would treat each loan as a person. Changed to **Other** before deploying, since category can't be changed afterwards. Switching category silently cleared the primary key; re-set it to `account_id`.
- **Lesson:** review the field settings screen every time, even when the wizard lets you skip straight to Deploy.

### 8. Loan Delinquency object and relationship
- Created a custom data model object **Loan Delinquency** from the Collections Accounts stream (13 fields, key `account_id`).
- Related `member_id` → Individual.Individual Id, cardinality **N:1** (one member can have several loans). Verified the relationship saved by reopening it.
- **Why it matters:** without this link, a segment can't ask "which members are 1–29 days past due," and the agent can't get from a person to their loans.

### 9. Identity resolution ruleset ("Member Unification")
- **Rule 1 (custom):** Contact Point Email, Exact Normalized, email only.
- **Rule 2 (Salesforce default):** first name (fuzzy) + last name (exact) + Formatted E164 phone (exact normalized).
- **Judgment calls:**
  - My first idea for Rule 2 was phone + last name. The built-in "Fuzzy Name and Normalized Phone" is better: it also checks first name, so spouses sharing a phone stay separate. Took the platform's rule over my own.
  - Kept Rule 1 email-only despite the platform's "add more criteria" warning. Requiring a name match could miss nicknames ("Liv" vs "Olivia") when the email is identical. Trade-off: a shared family email would merge two people. State this on the panel.
  - Rule 2 only works because the digital phone was cleaned into +1 format at ingestion (entry 6).
- **Reconciliation fix:** the platform warned that Individual Id had no supported reconciliation rule. Set it to **Source Priority** with Core Members first: core banking is the identity system of record.
- **Ignored warning:** "Contact_Home" is a default org object, not part of this design.
- **Expected result, calculated from the data before running:** 120 source profiles → **60 unified** (57 email matches, 3 stale duplicates via Rule 2, 3 core-only members). Any other number means a rule is wrong.

### 10. Consent fields on the person
- Added custom Individual fields from digital banking: SMS Consent, Email Consent (Boolean), Preferred Channel (Text); AI Voice Consent planned.
- **Design point:** members with no digital profile have no consent on record, so they drop out of SMS outreach automatically. Safe default under the TCPA.
- **Gap to state:** consent is a snapshot from a file. Production needs consent changes (e.g. a member texting STOP) to flow back within minutes, not nightly.

### 11. Outreach segment "Early Delinquency SMS Outreach"
- **Built:** on Unified Individual (MBR1). Rule: has a Loan Delinquency with days past due 1–29 AND no account flag, AND SMS Consent is true.
- **Delinquency part verified:** Data 360 counted **39**. The same rule run on the CSV gives 39, and the API's daily list (`/v1/delinquencies`) also returns 39. Three systems agree.
- **Where AI was wrong:** before counting, the AI told me to expect **36**. That number was never calculated. It came from a test client that hadn't loaded data, and then a guess. Caught only because the platform said 39 and I re-ran the count from source. Lesson: an "expected" number is only useful if it was actually computed.
- **Consent part: expected value, calculated from the files:** of the 39, **24** have SMS consent, 11 said no, 2 have no digital profile (correctly excluded), and 2 (M-1013, M-1042) have a stale duplicate profile that says "no" and a current one that says "yes".
- **Design question this exposes:** for those 2 members, which consent wins? It depends on the reconciliation rule for SMS Consent. The safe rule for TCPA is "most recent" (latest login), not "any source says yes". Checked the org: SMS/Email/AI Voice Consent use the default **Last Updated** rule. Every row came from the same file upload, so "last updated" is effectively a tie and the winner for those 2 members is arbitrary. **Gap to state on the panel:** production should drop or deprioritize stale profiles (last login over a year ago) at ingestion, or treat any "no" as no. Consent must never default to yes.
- **Blocker found:** SMS Consent returned 0, even for "has any value". Cause: the identity resolution re-run that adds the new consent fields was still In Progress. Not a mapping error. Re-check once the run completes.
- **Result (2026-10-18), after the run completed:**
  - SMS Consent true on unified profiles: **31 of 60**. Calculated from the files: 31 clean "yes", 3 conflicting, 3 with no digital profile. So all 3 conflicting members (M-1013, M-1028, M-1042) resolved to "no".
  - Final segment: **24**, exactly the number calculated beforehand. Evidence: `evidence/03_segment_24_sms_outreach.jpg`.
  - **Honest caveat:** the conflicting members came out "no", which is the safe answer, but by chance. The rule is still "Last Updated", so a future load could flip them. The fix stands as stated above.
- **Lost work, caught:** the first time, I left the builder without saving and the conditions were lost. The segment list still showed 60, which gave it away. Lesson: check the saved record, not the editor screen.

---

## Template for new entries

### N. <task>
- **Used for:**
- **Output:**
- **My judgment:** (accepted / rejected / changed, and why)
- **Verification:** (test, run, source, manual check)
- **Where AI was wrong, incomplete or misleading:**
