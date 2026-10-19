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
    text = (ROOT / "openapi/collections-api.openapi.yaml").read_text()
    assert "anyOf" not in text, "mixed/nullable types can break External Services import"
    assert "examples:" not in text and "type: 'null'" not in text, "3.1-only constructs"
