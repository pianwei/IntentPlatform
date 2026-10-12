"""Black-box INT-019 checks against the local synthetic candidate.

Creates labelled test spaces and agents, leaving evidence in place. Never accepts
model credentials and never changes the reference app or existing memberships.
"""
import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--workspace", default="ws-dev")
    parser.add_argument("--reference-agent", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Keep existing evidence; use a new output path")
    if args.base_url.rstrip("/") not in {"http://127.0.0.1:5173", "http://localhost:5173"}:
        parser.error("This diagnostic is limited to the local development server")
    report = {"kind": "INT-019-local-synthetic-integration", "created_at": datetime.now(timezone.utc).isoformat(),
              "checks": [], "resources": {}, "reference_agent": args.reference_agent}
    suffix = uuid.uuid4().hex[:10]
    with httpx.Client(base_url=args.base_url, timeout=25, trust_env=False) as client:
        def call(method, path, token=None, body=None, expected=None):
            response = client.request(method, path, headers={"Authorization": "Bearer " + token} if token else {}, json=body)
            if expected is not None:
                assert response.status_code in expected, f"{method} {path}: HTTP {response.status_code}"
            else:
                response.raise_for_status()
            return response.json() if response.content else None

        def check(name, operation):
            start = time.monotonic()
            try:
                detail = operation()
                item = {"name": name, "passed": True, "detail": detail}
            except Exception as exc:
                item = {"name": name, "passed": False, "error_type": type(exc).__name__, "error": str(exc)[:350]}
            item["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
            report["checks"].append(item)
            print(f"{name}: {'PASS' if item['passed'] else 'FAIL'}", flush=True)
            save()
            return item["passed"]

        def save():
            report["passed"] = sum(item["passed"] for item in report["checks"])
            report["total"] = len(report["checks"])
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        admin = call("POST", "/api/v2/dev/login", body={"user_id": "dev-admin"})["access_token"]
        other = call("POST", "/api/v2/dev/login", body={"user_id": "dev-other"})["access_token"]
        reader = call("POST", "/api/v2/dev/login", body={"user_id": "dev-reader"})["access_token"]
        base = f"/api/v2/management/workspaces/{args.workspace}"
        agents = call("GET", base + "/agents", admin)
        reference = next(a for a in agents if a["id"] == args.reference_agent)
        assert reference.get("model_profile_id"), "Published synthetic model configuration required"
        functions = call("GET", base + "/functions", admin)
        by_key = {f["definition"]["connector"].get("workflow_id"): f for f in functions
                  if f["definition"]["connector"]["kind"] == "fixture" and f["latest_version"]}

        def spaces():
            context = call("GET", "/api/v2/management/platform-context", admin)
            assert context["can_manage_workspaces"]
            assert not call("GET", "/api/v2/management/platform-context", other)["can_manage_workspaces"]
            call("POST", "/api/v2/management/workspaces", other,
                 {"name": "forbidden", "administrator_id": "dev-other"}, {403})
            ws = call("POST", "/api/v2/management/workspaces", admin,
                      {"name": "INT019-isolation-" + suffix, "administrator_id": "dev-other"})
            report["resources"]["workspace"] = ws["id"]
            path = f"/api/v2/management/workspaces/{ws['id']}"
            call("GET", path + "/functions", admin, expected={403, 404})
            members = call("GET", path + "/members", other)
            assert any(m["user_id"] == "dev-other" and "admin" in m["roles"] for m in members)
            grant = call("PUT", path + "/members/dev-reader", other,
                         {"revision": 0, "roles": ["user"], "active": True})
            assert any(w["id"] == ws["id"] for w in call("GET", "/api/v2/management/workspaces", reader))
            call("GET", path + "/members", reader, expected={403})
            call("PUT", path + "/members/dev-reader", other,
                 {"revision": grant["revision"], "roles": ["platform_admin"], "active": True}, {403, 422})
            call("PUT", path + "/members/dev-reader", other,
                 {"revision": grant["revision"], "roles": ["user"], "active": False})
            assert not any(w["id"] == ws["id"] for w in call("GET", "/api/v2/management/workspaces", reader))
            return {"workspace_id": ws["id"], "revocation_observed": True}

        check("space-creation-role-boundaries-and-revocation", spaces)
        subsets = [
            ("funds", ["demo.composite.customer_funds"],
             "查询 C001 的所有账户余额，给我做资金概览", None),
            ("transaction-status", ["demo.transaction.detail", "demo.account.status"],
             "比较 T001 交易详情和 A001 账户状态", None),
        ]
        skills = call("GET", base + "/business-skills", admin)
        for label, keys, question, skill_key in subsets:
            def subset(label=label, keys=keys, question=question, skill_key=skill_key):
                chosen = [by_key[key]["id"] for key in keys]
                body = {"name": "INT019-" + label + "-" + suffix, "capability_ids": chosen,
                        "planning_enabled": True, "routing_strategy": "three-layer-v1",
                        "model_profile_id": reference["model_profile_id"],
                        "business_skill_ids": [s["id"] for s in skills if s["key"] == skill_key],
                        "allowed_origins": ["http://localhost:5173", "http://127.0.0.1:5173"]}
                agent = call("POST", base + "/agents", admin, body)
                report["resources"][label] = {"agent_id": agent["id"], "public_id": agent["public_id"]}
                path = base + "/agents/" + agent["id"]
                preview = call("POST", path + "/compile-preview", admin, {"revision": agent["revision"]})
                assert preview["valid"], str(preview["issues"])
                assert set(preview["projection"]["explicit_function_ids"]) == set(chosen)
                if label == "funds":
                    assert set(preview["projection"]["dependency_function_ids"]) == {
                        by_key[key]["id"] for key in ("demo.customer.search", "demo.account.list", "demo.account.balance")}
                assert call("GET", path + "/releases", admin) == []
                released = call("POST", path + "/publish", admin, {"revision": agent["revision"]})
                runtime = call("POST", f"/api/v2/runtime/apps/{agent['public_id']}/sessions", admin, {})["access_token"]
                call("GET", base + "/functions", runtime, expected={403})
                call("GET", "/api/v2/management/platform-context", runtime, expected={403})
                conversation = call("POST", f"/api/v2/runtime/agents/{agent['id']}/conversations", runtime, {})
                before = conversation["release_id"]
                changed = call("PATCH", path + "/draft", admin,
                               {**body, "name": body["name"] + " updated", "revision": agent["revision"]})
                call("POST", path + "/publish", admin, {"revision": changed["revision"]})
                later = call("POST", f"/api/v2/runtime/agents/{agent['id']}/conversations", runtime, {})
                assert later["release_id"] != before
                message_body = {"text": question, "client_message_id": str(uuid.uuid4())}
                message_path = f"/api/v2/runtime/conversations/{conversation['id']}/messages"
                accepted = call("POST", message_path, runtime, message_body)
                assert call("POST", message_path, runtime, message_body)["run_id"] == accepted["run_id"]
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    run = call("GET", "/api/v2/runtime/runs/" + accepted["run_id"], runtime)
                    if run["status"] in {"SUCCEEDED", "FAILED", "CANCELLED", "UNKNOWN", "WAITING_INPUT", "WAITING_CONFIRMATION"}:
                        break
                    time.sleep(.5)
                assert run["status"] == "SUCCEEDED", str({"status": run["status"], "question": run.get("question")})
                assert run.get("steps", []) == []
                events = call("GET", f"/api/v2/runtime/conversations/{conversation['id']}/events", runtime)
                assert any(e["event_type"] == "RESULT" for e in events)
                forbidden = {"skill_version_id", "agent_projection", "model_profile", "compiler_version", "resolver_ref", "validation_evidence"}
                def no_internal(value):
                    if isinstance(value, dict):
                        assert not (set(value) & forbidden), str(set(value) & forbidden)
                        for child in value.values(): no_internal(child)
                    elif isinstance(value, list):
                        for child in value: no_internal(child)
                no_internal(run); no_internal(events)
                trace = call("GET", base + "/runs/" + accepted["run_id"] + "/trace", admin)
                assert all(trace["layers"][layer] for layer in ("entity", "dynamics", "planning"))
                assert trace["steps"], "Administrator execution facts must remain available"
                repeat = call("GET", f"/api/v2/runtime/conversations/{conversation['id']}/events", runtime)
                assert repeat == events
                outside = call("POST", f"/api/v2/runtime/agents/{agent['id']}/conversations", runtime, {})
                denied = call("POST", f"/api/v2/runtime/conversations/{outside['id']}/messages", runtime,
                              {"text": "查询 P001 的产品详情。", "client_message_id": str(uuid.uuid4())})
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    rejected = call("GET", "/api/v2/runtime/runs/" + denied["run_id"], runtime)
                    if rejected["status"] not in {"RECEIVED", "ROUTING", "EXECUTING"}: break
                    time.sleep(.5)
                assert rejected["status"] in {"WAITING_INPUT", "FAILED"} and rejected["calls"] == []
                if rejected["status"] == "WAITING_INPUT":
                    call("POST", "/api/v2/runtime/runs/" + denied["run_id"] + "/cancel", runtime,
                         {"revision": rejected["revision"]})
                return {"agent_id": agent["id"], "run_id": accepted["run_id"], "conversation_release": before,
                        "new_conversation_release": later["release_id"], "plan_source": trace["plan_source"],
                        "step_count": len(trace["steps"]), "elapsed_ms": run.get("timing", {}).get("total_ms"),
                        "outside_subset_zero_calls": True, "rejected_run_id": denied["run_id"]}
            check("subset-" + label + "-compile-publish-model-and-projection", subset)

        def extension():
            from intent_platform.demo.catalog import function_packages
            sample = by_key["demo.account.balance"]["definition"]
            domain_ref = sample["domain_refs"][0]
            package = next(p for p in function_packages(domain_ref, three_layer=True, include_extension=True)
                           if p["key"] == "demo.account.currency")
            function = by_key.get(package["key"])
            if not function:
                function = call("POST", base + "/functions/import", admin, {"definition": package["definition"]})
                valid = call("POST", base + "/functions/" + function["id"] + "/validate", admin,
                             {"arguments": {"account_id": "A001"}})
                assert valid["valid"], str(valid["errors"])
                function = call("POST", base + "/functions/" + function["id"] + "/publish", admin, {})
            body = {"name": "INT019-extension-21-" + suffix,
                    "capability_ids": list(dict.fromkeys([*reference["capability_ids"], function["id"]])),
                    "planning_enabled": True, "routing_strategy": "three-layer-v1",
                    "model_profile_id": reference["model_profile_id"],
                    "business_skill_ids": reference.get("business_skill_ids", []),
                    "allowed_origins": ["http://localhost:5173", "http://127.0.0.1:5173"]}
            agent = call("POST", base + "/agents", admin, body)
            path = base + "/agents/" + agent["id"]
            preview = call("POST", path + "/compile-preview", admin, {"revision": agent["revision"]})
            assert preview["valid"] and function["id"] in preview["projection"]["explicit_function_ids"]
            release = call("POST", path + "/publish", admin, {"revision": agent["revision"]})
            report["resources"]["extension"] = {"agent_id": agent["id"], "public_id": agent["public_id"],
                                                "release_id": release["release_id"], "function_id": function["id"]}
            save()
            runtime = call("POST", f"/api/v2/runtime/apps/{agent['public_id']}/sessions", admin, {})["access_token"]
            conversation = call("POST", f"/api/v2/runtime/agents/{agent['id']}/conversations", runtime, {})
            accepted = call("POST", f"/api/v2/runtime/conversations/{conversation['id']}/messages", runtime,
                            {"text": "请用查询账户币种功能查询 A001 的记账币种。", "client_message_id": str(uuid.uuid4())})
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                run = call("GET", "/api/v2/runtime/runs/" + accepted["run_id"], runtime)
                if run["status"] not in {"RECEIVED", "ROUTING", "EXECUTING"}:
                    break
                time.sleep(.5)
            assert run["status"] == "SUCCEEDED", run["status"]
            trace = call("GET", base + "/runs/" + accepted["run_id"] + "/trace", admin)
            step = next(s for s in trace["steps"] if s["name"] == "查询账户币种")
            assert step["input"] == {"account_id": "A001"}
            assert step["output"] == {"account_id": "A001", "currency": "CNY"}
            return {"run_id": accepted["run_id"], **report["resources"]["extension"],
                    "original_development_suite": "Run all 100 cases against this extension application separately"}
        check("registered-extension-21-self-service-and-model", extension)
    save()
    print(f"Checks {report['passed']}/{report['total']}; evidence {args.output}", flush=True)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
