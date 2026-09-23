"""Authorized runtime API driver. Gold is used only by preflight/scoring, never observation.

Every business operation goes through the normal runtime state machine. HTTP
submissions are never retried, including ambiguous timeouts and UNKNOWN results.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import time
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import Field

from .evaluation_spec import (
    Action, CaseObservation, ConfirmationObserved, EvaluationConfiguration, EvaluationDataset,
    Execution, Label, ObservationBatch, ObservedFault, SpecModel, StepObservation, OBSERVATION_NORMALIZATION_VERSION,
)
from .evaluation_scoring import EvaluationReport, json_key, score_evaluation


class RunnerTarget(SpecModel):
    public_id: Label
    agent_id: Label
    workspace_id: Label
    channel: Literal["chat", "embed"] = "chat"


class RunnerOptions(SpecModel):
    allow_effects: bool = False
    allow_sealed: bool = False
    step_timeout_seconds: float = Field(default=30, gt=0, le=600)
    request_timeout_seconds: float = Field(default=10, gt=0, le=120)
    poll_interval_seconds: float = Field(default=.1, gt=0, le=10)


@dataclass(frozen=True)
class RuntimeCredential:
    user_id: str
    workspace_id: str
    token: str = field(repr=False)
    runtime_session_id: str | None = None


@dataclass
class EvaluationTrial:
    dataset: EvaluationDataset
    observations: ObservationBatch
    report: EvaluationReport
    trace: list[dict]
    runtime_evidence: dict
    gaps: list[dict]
    created_at: str
    secrets: tuple[str, ...] = field(repr=False, default=())


class RuntimeEvidenceError(Exception):
    def __init__(self, code, detail, stage="validation", timeout=False):
        super().__init__(detail)
        self.code, self.detail, self.stage, self.timeout = code, detail, stage, timeout


def opaque_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise RuntimeEvidenceError("INVALID_SERVER_ID", "Runtime returned an unsafe or missing opaque ID")
    return value


def redact(value, secrets=()):
    """Keep non-secret wire content; do not archive credentials or token-bearing fields."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            # Report S2.authorization is a numeric Rate, not an HTTP authorization header.
            rate = (isinstance(item, dict) and set(item) == {"numerator", "denominator", "value", "defined"}
                    and type(item["numerator"]) is int and type(item["denominator"]) is int
                    and (item["value"] is None or type(item["value"]) in {int, float}) and type(item["defined"]) is bool)
            header = re.fullmatch(r"(?:proxy-)?authorization", key, re.I) and not rate
            secret_field = re.fullmatch(r"[a-z0-9_-]*(?:token|secret|cookie|password|credential|api.?key|ticket)[a-z0-9_-]*", key, re.I)
            # stage:CODE keys in fault-count maps are descriptive numeric metrics.
            result[key] = "[REDACTED]" if header or secret_field else redact(item, secrets)
        return result
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
    return value


class RuntimeEvaluationRunner:
    def __init__(self, base_url: str, target: RunnerTarget, credentials: list[RuntimeCredential],
                 options: RunnerOptions | None = None, *, transport=None, sleep=time.sleep, clock=time.monotonic):
        url = httpx.URL(base_url)
        if url.scheme not in {"http", "https"} or not url.host or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
            raise ValueError("Use an explicit HTTP(S) API origin without credentials, query or path")
        self.origin, self.target, self.options = str(url).rstrip("/"), target, options or RunnerOptions()
        self.credentials = {(c.user_id, c.workspace_id): c for c in credentials}
        if len(self.credentials) != len(credentials) or any(not c.token.strip() or "\n" in c.token or "\r" in c.token for c in credentials):
            raise ValueError("Credentials must have unique subjects/spaces and nonempty bearer values")
        self.transport, self.sleep, self.clock = transport, sleep, clock
        self.trace, self.evidence, self.gaps, self.secrets = [], {}, [], {c.token for c in credentials}

    def request(self, client, method, path, token=None, body=None):
        if not path.startswith("/api/v2/") or "?" in path or "\\" in path or "/../" in path:
            raise RuntimeEvidenceError("INVALID_PATH", "Only fixed same-origin runtime API paths are allowed")
        item = {"method": method, "path": path, "request": redact(body, self.secrets)}
        headers = {"Authorization": "Bearer " + token} if token else {}
        try:
            response = client.request(method, path, headers=headers, json=body,
                                      timeout=self.options.request_timeout_seconds, follow_redirects=False)
        except httpx.TimeoutException:
            item["outcome"] = "timeout"; self.trace.append(item)
            raise RuntimeEvidenceError("HTTP_TIMEOUT", "Runtime request timed out; the submission is not retried", "timeout", True) from None
        except httpx.HTTPError:
            item["outcome"] = "transport_error"; self.trace.append(item)
            raise RuntimeEvidenceError("HTTP_TRANSPORT", "Runtime transport failed; no submission retry was made", "infrastructure") from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("access_token"), str):
            self.secrets.add(data["access_token"])
        item.update(status=response.status_code, response=redact(data, self.secrets))
        self.trace.append(item)
        if response.is_redirect:
            raise RuntimeEvidenceError("REDIRECT_REFUSED", "Runtime origin redirected; bearer credentials were not forwarded", "authorization")
        if not response.is_success:
            stage = "authorization" if response.status_code in {401, 403} else "infrastructure"
            raise RuntimeEvidenceError(f"HTTP_{response.status_code}", f"Runtime API returned HTTP {response.status_code}", stage)
        if not isinstance(data, dict):
            raise RuntimeEvidenceError("INVALID_RESPONSE", "Expected a JSON object from the runtime API")
        return data

    def _me(self, client, token, user_id, workspace_id, scope=None):
        me = self.request(client, "GET", "/api/v2/me", token)
        if me.get("user_id") != user_id or (scope and me.get("scope") != scope):
            raise RuntimeEvidenceError("SUBJECT_MISMATCH", "Credential does not represent the scenario's actual user/scope", "authorization")
        if workspace_id not in {w.get("id") for w in me.get("workspaces", []) if isinstance(w, dict)}:
            raise RuntimeEvidenceError("WORKSPACE_MISMATCH", "Credential has no active scenario workspace membership", "authorization")
        return me

    def _session(self, client, scenario):
        credential = self.credentials.get((scenario.user_id, scenario.workspace_id))
        if credential is None:
            raise RuntimeEvidenceError("MISSING_CREDENTIAL", "No explicit token was supplied for this scenario subject/workspace", "authorization")
        me = self._me(client, credential.token, scenario.user_id, scenario.workspace_id)
        token, session_id = credential.token, credential.runtime_session_id
        if me.get("scope") == "management":
            if self.target.channel != "chat":
                raise RuntimeEvidenceError("EMBED_TOKEN_REQUIRED", "Embed execution requires an already-authorized host runtime token", "authorization")
            data = self.request(client, "POST", f"/api/v2/runtime/apps/{opaque_id(self.target.public_id)}/sessions", token, {})
            if data.get("user_id") != scenario.user_id or data.get("agent_id") != self.target.agent_id:
                raise RuntimeEvidenceError("SESSION_BINDING", "Issued runtime token is bound to a different subject or agent", "authorization")
            token, session_id = data.get("access_token"), opaque_id(data.get("runtime_session_id"))
            if not isinstance(token, str) or not token:
                raise RuntimeEvidenceError("SESSION_TOKEN_MISSING", "Session exchange returned no runtime token", "authorization")
        elif me.get("scope") != "runtime":
            raise RuntimeEvidenceError("RUNTIME_SCOPE_REQUIRED", "Unsupported identity scope", "authorization")
        self._me(client, token, scenario.user_id, scenario.workspace_id, "runtime")
        info = self.request(client, "GET", f"/api/v2/runtime/agents/{opaque_id(self.target.agent_id)}", token)
        if info.get("id") != self.target.agent_id or info.get("workspace_id") != self.target.workspace_id:
            raise RuntimeEvidenceError("AGENT_BINDING", "Runtime token/agent workspace binding does not match the target", "authorization")
        return token, session_id

    def _check_profile(self, profile, scenario, config, conversation_id, session_id):
        expected = {"release_id": config.release_id, "conversation_id": conversation_id,
                    "workspace_id": scenario.workspace_id, "user_id": scenario.user_id,
                    "agent_id": self.target.agent_id, "channel": self.target.channel}
        if any(profile.get(key) != val for key, val in expected.items()):
            raise RuntimeEvidenceError("PROFILE_BINDING", "Observed release/subject/space/agent/channel differs from the requested candidate", "authorization")
        actual_session = opaque_id(profile.get("runtime_session_id"))
        if session_id and actual_session != session_id:
            raise RuntimeEvidenceError("SESSION_BINDING", "Evidence belongs to a different runtime session", "authorization")
        gaps = list(profile.get("gaps", []))
        for field_name in ("model_id", "model_version", "model_parameters", "prompt_version", "ontology_version", "function_versions", "connector_versions"):
            actual, declared = profile.get(field_name), getattr(config, field_name)
            if actual is None:
                gaps.append(field_name + "_unobserved")
            elif json_key(actual) != json_key(declared):
                raise RuntimeEvidenceError("CONFIGURATION_MISMATCH", f"Observed {field_name} differs from the declared candidate")
        if profile.get("fixture_mode") is True and config.resource_mode != "fixture":
            raise RuntimeEvidenceError("RESOURCE_MODE_MISMATCH", "Fixture execution cannot be declared real or synthetic")
        if profile.get("fixture_mode") is not True and config.resource_mode != "real":
            raise RuntimeEvidenceError("RESOURCE_MODE_MISMATCH", "Non-fixture execution must be explicitly declared real")
        return sorted(set(gaps))

    def _poll(self, client, token, run_id):
        deadline = self.clock() + self.options.step_timeout_seconds
        while True:
            run = self.request(client, "GET", f"/api/v2/runtime/runs/{opaque_id(run_id)}", token)
            if run.get("run_id") != run_id:
                raise RuntimeEvidenceError("RUN_BINDING", "Runtime returned a different run")
            if run.get("status") in {"WAITING_INPUT", "WAITING_CONFIRMATION", "SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"}:
                return run
            if run.get("status") not in {"RECEIVED", "ROUTING", "EXECUTING"}:
                raise RuntimeEvidenceError("INVALID_RUN_STATUS", "Runtime returned an unsupported state")
            if self.clock() >= deadline:
                raise RuntimeEvidenceError("STEP_TIMEOUT", "Run did not settle before the evaluation deadline; no resubmission", "timeout", True)
            self.sleep(self.options.poll_interval_seconds)

    def _submit(self, client, token, cid, stimulus, current):
        if stimulus.kind == "request":
            return self.request(client, "POST", f"/api/v2/runtime/conversations/{cid}/messages", token,
                                {"text": stimulus.text, "client_message_id": uuid4().hex})
        if current is None or current.get("status") == "UNKNOWN":
            raise RuntimeEvidenceError("NO_ACTIONABLE_RUN", "There is no certain current run for this step", "input")
        rid = opaque_id(current.get("run_id"))
        latest = self.request(client, "GET", f"/api/v2/runtime/runs/{rid}", token)
        if latest.get("run_id") != rid or not isinstance(latest.get("revision"), int):
            raise RuntimeEvidenceError("RUN_BINDING", "Cannot bind the action to an actual run/revision")
        revision = latest["revision"]
        if stimulus.kind == "answer":
            question = latest.get("question") or {}
            if latest.get("status") != "WAITING_INPUT" or not question.get("id"):
                raise RuntimeEvidenceError("QUESTION_UNAVAILABLE", "No current question is available for this answer", "input")
            option_id = stimulus.option_id
            if stimulus.option_label is not None:
                matches = [option for option in question.get("options", []) if option.get("label") == stimulus.option_label]
                if len(matches) != 1:
                    raise RuntimeEvidenceError("OPTION_LABEL_NOT_UNIQUE", "Explicit answer label must exactly match one current question option", "input")
                option_id = opaque_id(matches[0].get("id"))
            elif option_id is not None and question.get("kind") != "function_choice":
                raise RuntimeEvidenceError("OPAQUE_OPTION_REQUIRES_LABEL", "Entity/history options must be selected by a unique explicit label", "input")
            body = {"revision": revision, "question_id": question["id"], "values": stimulus.values, "option_id": option_id}
            return self.request(client, "POST", f"/api/v2/runtime/runs/{rid}/answers", token, body)
        if stimulus.kind == "confirm":
            if stimulus.confirmed and not self.options.allow_effects:
                raise RuntimeEvidenceError("SIDE_EFFECTS_NOT_ALLOWED", "Affirmative confirmation requires explicit allow_effects; gold never authorizes it", "confirmation")
            if latest.get("status") != "WAITING_CONFIRMATION":
                raise RuntimeEvidenceError("CONFIRMATION_UNAVAILABLE", "No current confirmation is available", "confirmation")
            proof = self.request(client, "GET", f"/api/v2/runtime/runs/{rid}/evaluation-evidence", token)
            confirmation = proof.get("confirmation") or {}
            if proof.get("run_id") != rid or proof.get("revision") != revision or confirmation.get("revision") != revision or not confirmation.get("event_sequence"):
                raise RuntimeEvidenceError("CONFIRMATION_BINDING", "Confirmation evidence is not bound to the current run/revision", "confirmation")
            try:
                expiry = datetime.fromisoformat(confirmation["expires_at"].replace("Z", "+00:00"))
                valid = expiry.tzinfo is not None and expiry > datetime.now(timezone.utc)
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise RuntimeEvidenceError("CONFIRMATION_EXPIRED", "The actual confirmation expiry is unavailable or expired", "confirmation")
            return self.request(client, "POST", f"/api/v2/runtime/runs/{rid}/confirmations", token,
                                {"revision": revision, "confirmed": stimulus.confirmed})
        return self.request(client, "POST", f"/api/v2/runtime/runs/{rid}/cancel", token, {"revision": revision})

    def _observation(self, step_id, proof, previous_calls, extra_gaps):
        gaps = sorted(set(extra_gaps + proof.get("gaps", [])))
        confirmation = proof.get("confirmation")
        known_confirmation = None
        if isinstance(confirmation, dict) and confirmation.get("executions_before_confirmation") is not None:
            known_confirmation = ConfirmationObserved.model_validate({key: confirmation.get(key) for key in
                ("required", "accepted", "binding_valid", "executions_before_confirmation")})
        else:
            gaps.append("confirmation_observation_unavailable")
        calls = proof.get("calls")
        execution = None
        if isinstance(calls, list) and all(isinstance(c, dict) and "dispatched_at" in c and "id" in c for c in calls):
            dispatched = [c for c in calls if c["id"] not in previous_calls and c["dispatched_at"] is not None]
            states = {c.get("status") for c in dispatched}
            status = ("NOT_EXECUTED" if not dispatched else "SUCCEEDED" if states == {"SUCCEEDED"} else
                      "UNKNOWN" if states & {"UNKNOWN", "EXECUTING", "RECONCILING"} else
                      "FAILED" if "FAILED" in states else "CANCELLED" if states == {"CANCELLED"} else "UNKNOWN")
            values = [(c.get("result") or {}).get("data") for c in dispatched]
            result = values[0] if len(values) == 1 else values if values else None
            execution = Execution(status=status, count=len(dispatched), result=result, displayed_result=None)
            # HTTP components are not evidence that a real browser displayed them correctly.
            if dispatched:
                gaps.append("browser_display_unobserved")
        else:
            gaps.append("execution_count_unobserved")
        faults = [ObservedFault(stage="validation", code="EVIDENCE_GAP", detail=gap) for gap in sorted(set(gaps))]
        for event in proof.get("errors", []):
            payload = event.get("payload", {})
            code = str(payload.get("code", "RUNTIME_ERROR"))
            if code == proof.get("final_decision") and code in {"OUT_OF_SCOPE", "FORBIDDEN"}:
                continue  # A normal terminal denial is a decision, not an infrastructure fault.
            stage = "downstream" if code == "UNKNOWN" else "authorization" if code == "FORBIDDEN" else "unknown"
            faults.append(ObservedFault(stage=stage, code=code, detail=str(payload.get("message", "Runtime error"))))
        if execution and execution.status in {"FAILED", "UNKNOWN"}:
            faults.append(ObservedFault(stage="downstream", code="BUSINESS_" + execution.status, detail="Observed business outcome is not successful"))
        question = proof.get("question") or {}
        kind = question.get("kind")
        options = question.get("options", [])
        labels = [o["label"] if kind in {"entity_choice", "history_choice"} else o["id"] for o in options]
        return StepObservation(step_id=step_id, routing_outcome="error" if proof.get("final_decision") == "ERROR" else "completed",
            decision=proof.get("final_decision"), validation_completed=proof.get("validation_completed") is True,
            actions=[Action.model_validate(a) for a in proof.get("selected_requests", [])], semantics=proof.get("semantics"),
            clarification_options=labels, clarification_kind=kind,
            confirmation=known_confirmation, execution=execution, unauthorized_executions=proof.get("unauthorized_executions"),
            leakage_events=proof.get("leakage_events"), faults=faults)

    def run(self, dataset: EvaluationDataset, configuration: EvaluationConfiguration, trial_id: str) -> EvaluationTrial:
        dataset = EvaluationDataset.model_validate(dataset.model_dump())
        configuration = EvaluationConfiguration.model_validate(configuration.model_dump())
        if dataset.reference.purpose == "sealed" and not self.options.allow_sealed:
            raise ValueError("Sealed execution is disabled; explicit allow_sealed is required separately from tokens/effects")
        if any(case.workspace_id != self.target.workspace_id for case in dataset.scenarios):
            raise ValueError("All scenarios must belong to the explicit target workspace")
        blank = ObservationBatch(dataset=dataset.reference, configuration=configuration, trial_id=trial_id, cases=[])
        score_evaluation(dataset, blank)  # Validate the dataset/configuration before any HTTP action.
        self.trace, self.evidence, self.gaps = [], {}, []
        observed_cases = []
        with httpx.Client(base_url=self.origin, transport=self.transport, trust_env=False, follow_redirects=False) as client:
            for scenario in dataset.scenarios:
                steps, current, stopped = [], None, False
                previous_calls = set()
                try:
                    public = self.request(client, "GET", f"/api/v2/runtime/apps/{opaque_id(self.target.public_id)}")
                    if public.get("agent_id") != self.target.agent_id or public.get("release_id") != configuration.release_id:
                        raise RuntimeEvidenceError("ACTIVE_RELEASE_MISMATCH", "Candidate release is not the target's active published release; no publication change was made")
                    token, sid = self._session(client, scenario)
                    conversation = self.request(client, "POST", f"/api/v2/runtime/agents/{opaque_id(self.target.agent_id)}/conversations", token, {})
                    cid = opaque_id(conversation.get("id") or conversation.get("conversation_id"))
                    profile = self.request(client, "GET", f"/api/v2/runtime/conversations/{cid}/evaluation-profile", token)
                    profile_gaps = self._check_profile(profile, scenario, configuration, cid, sid)
                    self.evidence[scenario.case_id] = {"profile": profile, "steps": {}}
                    setup_error = None
                except (RuntimeEvidenceError, ValueError, KeyError, TypeError) as exc:
                    setup_error = exc if isinstance(exc, RuntimeEvidenceError) else RuntimeEvidenceError("INVALID_EVIDENCE", "Runtime evidence failed schema validation")
                for step in scenario.steps:
                    try:
                        if setup_error:
                            raise setup_error
                        if stopped:
                            raise RuntimeEvidenceError("AFTER_UNCERTAIN_STEP", "Remaining steps were not submitted after uncertain or blocked execution", "input")
                        receipt = self._submit(client, token, cid, step.stimulus, current)
                        run_id = opaque_id(receipt.get("run_id"))
                        if receipt.get("conversation_id") != cid:
                            raise RuntimeEvidenceError("CONVERSATION_BINDING", "Submitted action returned a different conversation")
                        current = self._poll(client, token, run_id)
                        proof = self.request(client, "GET", f"/api/v2/runtime/runs/{run_id}/evaluation-evidence", token)
                        if proof.get("run_id") != run_id or proof.get("revision") != current.get("revision"):
                            raise RuntimeEvidenceError("EVIDENCE_REVISION", "Evidence does not match the observed run/revision")
                        if proof.get("observation_normalization_version") != OBSERVATION_NORMALIZATION_VERSION:
                            raise RuntimeEvidenceError("NORMALIZATION_VERSION", "Runtime evidence normalization is absent or unsupported")
                        extra = self._check_profile(proof.get("profile", {}), scenario, configuration, cid, sid)
                        self.evidence[scenario.case_id]["steps"][step.step_id] = proof
                        observation = self._observation(step.step_id, proof, previous_calls, profile_gaps + extra)
                        steps.append(observation)
                        self.gaps.extend({"case_id": scenario.case_id, "step_id": step.step_id,
                                          "code": f.code, "detail": f.detail}
                                         for f in observation.faults if f.code == "EVIDENCE_GAP")
                        previous_calls.update(c["id"] for c in proof.get("calls", []) if c.get("dispatched_at") is not None)
                        if current.get("status") == "UNKNOWN":
                            stopped = True
                    except (RuntimeEvidenceError, ValueError, KeyError, TypeError) as exc:
                        error = exc if isinstance(exc, RuntimeEvidenceError) else RuntimeEvidenceError("INVALID_EVIDENCE", "Runtime evidence failed schema validation")
                        steps.append(StepObservation(step_id=step.step_id, routing_outcome="timeout" if error.timeout else "not_run" if error.code in {"AFTER_UNCERTAIN_STEP", "SIDE_EFFECTS_NOT_ALLOWED", "MISSING_CREDENTIAL"} else "error",
                            faults=[ObservedFault(stage=error.stage, code=error.code, detail=error.detail)]))
                        stopped = True
                        self.gaps.append({"case_id": scenario.case_id, "step_id": step.step_id, "code": error.code, "detail": error.detail})
                if setup_error is None:
                    try:
                        history = self.request(client, "GET", f"/api/v2/runtime/conversations/{cid}/history", token)
                        if (history.get("conversation") or {}).get("id") != cid:
                            raise RuntimeEvidenceError("HISTORY_BINDING", "History belongs to a different conversation")
                        self.evidence[scenario.case_id]["history"] = history
                    except RuntimeEvidenceError as error:
                        # A read-only recovery snapshot may fail; never resubmit the underlying operation.
                        self.gaps.append({"case_id": scenario.case_id, "code": error.code, "detail": error.detail})
                        steps[-1].faults.append(ObservedFault(stage=error.stage, code=error.code, detail=error.detail))
                observed_cases.append(CaseObservation(case_id=scenario.case_id, steps=steps))
        observations = ObservationBatch(dataset=dataset.reference, configuration=configuration, trial_id=trial_id, cases=observed_cases)
        return EvaluationTrial(dataset, observations, score_evaluation(dataset, observations), redact(self.trace, self.secrets),
                               redact(self.evidence, self.secrets), self.gaps, datetime.now(timezone.utc).isoformat(), tuple(self.secrets))


def run_comparison(dataset, candidates):
    """Run independent pre-published candidates; never mutate a release or pool trials.

    candidates is [(runner, configuration, trial_id), ...]. Each runner has its own
    explicit origin/credentials. Prompt/ontology/ablation may differ; other listed
    model/function/environment controls must be identical.
    """
    if len(candidates) < 2:
        raise ValueError("Comparison requires at least two independent candidates")
    configs = [EvaluationConfiguration.model_validate(c.model_dump()) for _, c, _ in candidates]
    controls = ("purpose", "model_id", "model_version", "model_parameters", "function_versions", "connector_versions", "environment_ref", "resource_mode")
    baseline = configs[0]
    if any(any(json_key(getattr(c, k)) != json_key(getattr(baseline, k)) for k in controls) for c in configs[1:]):
        raise ValueError("Comparison candidates do not share the required experimental controls")
    if len({c.release_id for c in configs}) != len(configs) or len({c.ablation for c in configs}) != len(configs):
        raise ValueError("Each comparison candidate needs a distinct release and ablation label")
    if len({trial for _, _, trial in candidates}) != len(candidates):
        raise ValueError("Comparison trials must be distinct")
    return [runner.run(dataset, config, trial) for (runner, _, trial), config in zip(candidates, configs)]
