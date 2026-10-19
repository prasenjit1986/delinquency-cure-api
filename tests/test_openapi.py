"""The exported spec must stay importable by Salesforce External Services (OpenAPI 3.0)."""
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_spec_exports_as_valid_openapi_30():
    r = subprocess.run([sys.executable, "scripts/export_openapi.py"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    spec = yaml.safe_load((ROOT / "openapi/collections-api.openapi.yaml").read_text())
    assert spec["openapi"] == "3.0.3"
    assert spec["servers"][0]["url"] == "https://collections-policy-api.onrender.com", \
        "spec must point at the deployed API, not a placeholder"
    ops = {op["operationId"] for p in spec["paths"].values() for op in p.values()}
    assert "getAudit" not in ops, "admin routes must not be exposed to the agent"
    assert {"verifyMember", "getEligibleOptions", "createPromiseToPay"} <= ops
    from app.main import app
    member_ops = {r.operation_id for r in app.routes
                  if getattr(r, "tags", None) and "member" in r.tags and r.include_in_schema}
    assert member_ops <= ops, f"member operations missing from the Salesforce spec: {member_ops - ops}"
    text = (ROOT / "openapi/collections-api.openapi.yaml").read_text()
    assert "anyOf" not in text, "mixed/nullable types can break External Services import"
    assert "examples:" not in text and "type: 'null'" not in text, "3.1-only constructs"


def test_salesforce_spec_is_served_and_matches_export():
    import json
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/openapi-sfdc.json")
    assert r.status_code == 200
    served = r.json()
    assert served["openapi"] == "3.0.3"
    assert served == json.loads((ROOT / "openapi/collections-api.openapi.json").read_text())
