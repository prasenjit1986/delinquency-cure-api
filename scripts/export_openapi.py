"""Export an OpenAPI 3.0.3 spec that Salesforce External Services can import.

FastAPI + Pydantic v2 emit some OpenAPI 3.1-only constructs. This script:
- forces openapi: 3.0.3
- converts `examples: [x]` (3.1) into `example: x` (3.0)
- converts `anyOf: [{...}, {type: null}]` into `{..., nullable: true}`
- converts numeric `exclusiveMinimum/Maximum` (3.1) into `minimum` + boolean (3.0)
- sets `servers` to PUBLIC_BASE_URL
- keeps only the endpoints the agent should call (ops/admin routes are hidden)
Then it validates the result against the OpenAPI 3.0 schema.

Usage: PUBLIC_BASE_URL=https://your-app.onrender.com python scripts/export_openapi.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.main import app  # noqa: E402

AGENT_OPERATIONS = {"verifyMember", "getAccountSummary", "getEligibleOptions",
                    "createPromiseToPay", "enrollSkipAPay", "setAccountFlag", "listDelinquencies"}


def downgrade(node):
    if isinstance(node, dict):
        if "anyOf" in node:
            non_null = [s for s in node["anyOf"] if s.get("type") != "null"]
            if len(non_null) == 1 and len(non_null) < len(node["anyOf"]):
                rest = {k: v for k, v in node.items() if k != "anyOf"}
                node = {**non_null[0], **rest, "nullable": True}
        if "examples" in node and isinstance(node["examples"], list):
            ex = node.pop("examples")
            if ex:
                node["example"] = ex[0]
        node.pop("const", None)
        # 3.1: exclusiveMinimum: <number>   3.0: minimum: <number>, exclusiveMinimum: true
        for bound, ex in (("minimum", "exclusiveMinimum"), ("maximum", "exclusiveMaximum")):
            if isinstance(node.get(ex), (int, float)) and not isinstance(node.get(ex), bool):
                node[bound] = node[ex]
                node[ex] = True
        return {k: downgrade(v) for k, v in node.items()}
    if isinstance(node, list):
        return [downgrade(x) for x in node]
    return node


def main() -> None:
    spec = app.openapi()
    spec["openapi"] = "3.0.3"
    spec["servers"] = [{"url": os.getenv("PUBLIC_BASE_URL", "https://YOUR-APP.onrender.com")}]

    paths = {}
    for path, ops in spec["paths"].items():
        kept = {m: op for m, op in ops.items() if op.get("operationId") in AGENT_OPERATIONS}
        if kept:
            paths[path] = kept
    spec["paths"] = paths
    spec = downgrade(spec)

    # FastAPI's built-in error schema uses anyOf[string, integer] for `loc`. Legal in 3.0,
    # but mixed-type items are a common cause of External Services import trouble.
    ve = spec.get("components", {}).get("schemas", {}).get("ValidationError")
    if ve and "loc" in ve.get("properties", {}):
        ve["properties"]["loc"]["items"] = {"type": "string"}

    # Apply the API key to every operation.
    spec.setdefault("components", {}).setdefault("securitySchemes", {})["ApiKeyAuth"] = {
        "type": "apiKey", "in": "header", "name": "X-API-Key"}
    spec["security"] = [{"ApiKeyAuth": []}]
    for ops in spec["paths"].values():
        for op in ops.values():
            op.pop("security", None)
    for k in list(spec["components"]["securitySchemes"]):
        if k != "ApiKeyAuth":
            del spec["components"]["securitySchemes"][k]

    out = Path(__file__).resolve().parent.parent / "openapi"
    out.mkdir(exist_ok=True)
    (out / "collections-api.openapi.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    (out / "collections-api.openapi.json").write_text(json.dumps(spec, indent=2))

    from openapi_spec_validator import validate
    validate(spec)
    print(f"OK: OpenAPI {spec['openapi']} valid, {sum(len(v) for v in spec['paths'].values())} operations")


if __name__ == "__main__":
    main()
