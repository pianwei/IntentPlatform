"""Finite one-step shadow evidence; deliberately separate from E2E evaluation."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from urllib.parse import quote
import httpx
from pydantic import Field, model_validator
from .evaluation_spec import SpecModel, Label
from .evaluation_cli import reserve_trial, SafeParser
from .evaluation_runner import redact
from .shadow_evaluation import ShadowOut


class ShadowGold(SpecModel):
    decision: Literal["READY", "NEEDS_CONFIRMATION", "NEEDS_ARGUMENTS", "AMBIGUOUS", "OUT_OF_SCOPE", "FORBIDDEN", "ERROR"]
    capability_ids: list[Label]

    @model_validator(mode="after")
    def distinct_capabilities(self):
        if len(set(self.capability_ids)) != len(self.capability_ids):
            raise ValueError("Gold capability set cannot contain duplicates")
        return self


class ShadowCase(SpecModel):
    case_id: Label
    conversation_id: Label
    release_id: Label
    text: str = Field(min_length=1, max_length=8000)
    expected: ShadowGold


class ShadowDataset(SpecModel):
    dataset_id: Label
    version: Label
    purpose: Literal["development", "sealed"]
    review_ref: Label | None = None
    frozen_ref: Label | None = None
    cases: list[ShadowCase] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def coherent(self):
        if len({c.case_id for c in self.cases}) != len(self.cases): raise ValueError("Duplicate case")
        if self.purpose == "sealed" and not (self.review_ref and self.frozen_ref): raise ValueError("Sealed data needs reviewed frozen references")
        return self


class ShadowBinding(SpecModel):
    conversation_id: Label
    env_var: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class ShadowBindings(SpecModel):
    bindings: list[ShadowBinding] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent(self):
        if len({b.conversation_id for b in self.bindings}) != len(self.bindings): raise ValueError("Duplicate credential binding")
        return self


def observation(row):
    decision = row.get("decision")
    requests = row.get("validated_requests", [])
    question = row.get("question") or {}
    if decision == "AMBIGUOUS":
        if question.get("kind") == "function_choice":
            identifiers = [option["id"] for option in question.get("options", [])]
        elif question.get("kind") in {"entity_choice", "history_choice"}:
            identifiers = [request["capability_id"] for request in (row.get("proposal") or {}).get("requests", [])]
        else:
            identifiers = []
        complete = bool(identifiers) and len({option["id"] for option in question.get("options", [])}) >= 2
        return {"variant": row["variant"], "outcome": row["outcome"] if complete else "error", "decision": decision, "capability_ids": sorted(set(identifiers))}
    if decision == "NEEDS_ARGUMENTS": requests = (row.get("proposal") or {}).get("requests", [])
    if decision not in {"READY", "NEEDS_CONFIRMATION", "NEEDS_ARGUMENTS"}: requests = []
    complete = decision not in {"READY", "NEEDS_CONFIRMATION", "NEEDS_ARGUMENTS"} or bool(requests)
    return {"variant": row["variant"], "outcome": row["outcome"] if complete else "error", "decision": decision,
            "capability_ids": sorted({r["capability_id"] for r in requests})}


def summarize(dataset, observed):
    by_case = {item["case_id"]: item for item in observed}
    report = {"metric": "shadow-exact-route-v1", "acceptance_evidence": False, "denominator": len(dataset.cases), "variants": {}}
    for variant in ("A", "B", "C"):
        correct = completed = 0
        differences = []
        for case in dataset.cases:
            actual = next((v for v in by_case.get(case.case_id, {}).get("variants", []) if v["variant"] == variant), None)
            if actual and actual["outcome"] == "completed": completed += 1
            match = bool(actual and actual["outcome"] == "completed" and actual["decision"] == case.expected.decision
                         and actual["capability_ids"] == sorted(case.expected.capability_ids))
            correct += int(match)
            if not match: differences.append({"case_id": case.case_id, "expected": case.expected.model_dump(), "observed": actual})
        report["variants"][variant] = {"correct": correct, "completed": completed, "total": len(dataset.cases), "rate": correct / len(dataset.cases), "differences": differences}
    return report


def collect(dataset, bindings, base_url, *, client=None):
    tokens = {binding.conversation_id: os.environ[binding.env_var] for binding in bindings.bindings}
    if any(case.conversation_id not in tokens for case in dataset.cases): raise ValueError("Missing runtime identity binding")
    own = client is None
    client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=240, follow_redirects=False, trust_env=False)
    evidence, observed = [], []
    try:
        for case in dataset.cases:
            raw = None; fault = None
            try:
                response = client.post(f"/api/v2/runtime/conversations/{quote(case.conversation_id, safe='')}/shadow-comparison",
                                       headers={"Authorization": "Bearer " + tokens[case.conversation_id]},
                                       json={"release_id": case.release_id, "text": case.text})
                response.raise_for_status()
                parsed = ShadowOut.model_validate(response.json())
                if parsed.conversation_id != case.conversation_id or parsed.release_id != case.release_id:
                    raise ValueError("Mismatched shadow evidence")
                if [v.variant for v in parsed.variants] != ["A", "B", "C"]:
                    raise ValueError("Incomplete or reordered shadow variants")
                raw = parsed.model_dump(mode="json")
                variants = [observation(row) for row in raw["variants"]]
            except httpx.TimeoutException:
                fault = {"stage": "transport", "code": "COMPARISON_TIMEOUT_NO_RETRY"}
                variants = [{"variant": v, "outcome": "timeout", "decision": None, "capability_ids": []} for v in ("A", "B", "C")]
            except (httpx.HTTPError, ValueError, KeyError):
                fault = {"stage": "transport", "code": "COMPARISON_REJECTED_OR_INVALID"}
                variants = [{"variant": v, "outcome": "error", "decision": None, "capability_ids": []} for v in ("A", "B", "C")]
            observed.append({"case_id": case.case_id, "variants": variants})
            evidence.append({"case_id": case.case_id, "response": raw, "fault": fault})
    finally:
        if own: client.close()
    return observed, evidence, list(tokens.values())


def contains_secret(value, secrets):
    if isinstance(value, str): return any(secret and secret in value for secret in secrets)
    if isinstance(value, dict): return any(contains_secret(k, secrets) or contains_secret(v, secrets) for k, v in value.items())
    if isinstance(value, list): return any(contains_secret(v, secrets) for v in value)
    return False


def commit_shadow(folder, dataset, observed, evidence, secrets):
    if not folder.is_dir() or folder.is_symlink() or any(folder.iterdir()): raise ValueError("Reserved directory is not empty")
    dataset = ShadowDataset.model_validate(dataset.model_dump())
    authoritative = {"dataset": dataset.model_dump(mode="json"), "observations": observed, "report": summarize(dataset, observed)}
    if contains_secret(authoritative, secrets): raise ValueError("Known secret contamination; archive refused")
    safe = redact(evidence, secrets)
    bundle = {"schema_version": "1", "kind": "intent-shadow-trial", "trial_id": folder.name,
              "created_at": datetime.now(timezone.utc).isoformat(), "acceptance_evidence": False,
              **authoritative, "evidence": safe, "redactions_applied": safe != evidence,
              "sanitization": {"score_inputs": "preserved", "unknown_secrets_checked": False, "source_responsibility": "caller-reviewed"},
              "limitations": ["shadow-only", "no-business-dispatch", "not-E2E-or-S3", "no-statistical-significance-claim"]}
    encoded = json.dumps(bundle, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    parsed = json.loads(encoded)
    persisted_dataset = ShadowDataset.model_validate(parsed["dataset"])
    if summarize(persisted_dataset, parsed["observations"]) != parsed["report"]: raise ValueError("Serialized evidence changed scores")
    temporary = folder / (".shadow-" + uuid4().hex + ".tmp")
    final = folder / "shadow.json"
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, final)
    finally:
        if temporary.exists(): temporary.unlink()
    return final


def main(argv=None):
    parser = SafeParser(description="Finite authorized shadow A/B/C; no business dispatch or acceptance claim.")
    for name in ("dataset", "bindings", "base-url", "trial-id", "output"): parser.add_argument("--" + name, required=True)
    parser.add_argument("--allow-sealed", action="store_true")
    args = parser.parse_args(argv)
    try:
        dataset = ShadowDataset.model_validate_json(Path(args.dataset).read_bytes())
        bindings = ShadowBindings.model_validate_json(Path(args.bindings).read_bytes())
        if dataset.purpose == "sealed" and not args.allow_sealed: raise ValueError("Sealed execution is disabled")
        folder = reserve_trial(args.output, args.trial_id)
        observed, evidence, secrets = collect(dataset, bindings, args.base_url)
        print(str(commit_shadow(folder, dataset, observed, evidence, secrets)))
        return 0
    except (ValueError, KeyError, OSError):
        print("Shadow evaluation failed; inspect authorized local inputs. No submitted comparison was retried.", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__": raise SystemExit(main())
