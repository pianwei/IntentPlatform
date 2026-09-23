"""Offline scoring and authorized execution, with an atomic, non-overwriting archive."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from pydantic import Field

from .evaluation_runner import (
    EvaluationTrial, RunnerOptions, RunnerTarget, RuntimeCredential, RuntimeEvaluationRunner, redact,
)
from .evaluation_scoring import score_evaluation, EvaluationReport
from .evaluation_spec import EvaluationConfiguration, EvaluationDataset, Label, ObservationBatch, SpecModel


class CredentialBinding(SpecModel):
    user_id: Label
    workspace_id: Label
    env_var: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    runtime_session_id: Label | None = None


class CredentialBindings(SpecModel):
    bindings: list[CredentialBinding] = Field(min_length=1)


def reserve_trial(output_root, trial_id):
    """Reserve once, before any runtime action. Incomplete directories are not reused."""
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", trial_id)
            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", trial_id, re.I)):
        raise ValueError("Trial ID must be a safe, non-reserved single directory name")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    folder = root / trial_id
    folder.mkdir(exist_ok=False)
    return folder


def commit_trial(folder, trial):
    """Publish one bundle atomically. No partially written file is a valid archive."""
    folder = Path(folder)
    if folder.name != trial.observations.trial_id or folder.is_symlink() or not folder.is_dir():
        raise ValueError("Reserved folder must match the actual trial ID")
    if any(folder.iterdir()):
        raise FileExistsError("Trial directory is not empty; no overwrite or resume is allowed")
    # Scoring inputs retain exact business keys/values; heuristic redaction is wire-only.
    dataset = EvaluationDataset.model_validate(trial.dataset.model_dump(mode="python"))
    observations = ObservationBatch.model_validate(trial.observations.model_dump(mode="python"))
    original_report = EvaluationReport.model_validate(trial.report.model_dump(mode="python"))
    report = score_evaluation(dataset, observations)
    if original_report.model_dump(mode="json") != report.model_dump(mode="json"):
        raise ValueError("Supplied report does not match authoritative scoring inputs")
    def contains_known_secret(value):
        if isinstance(value, str): return any(secret and secret in value for secret in trial.secrets)
        if isinstance(value, dict): return any(contains_known_secret(k) or contains_known_secret(v) for k, v in value.items())
        if isinstance(value, (list, tuple)): return any(contains_known_secret(v) for v in value)
        return False
    authoritative = {"dataset": dataset.model_dump(mode="json"), "observations": observations.model_dump(mode="json"), "report": report.model_dump(mode="json")}
    if contains_known_secret(authoritative):
        raise ValueError("Known secret occurs in scoring inputs; archive refused without changing the score")
    bundle = {
        "schema_version": "1", "kind": "intent-evaluation-trial", "trial_id": trial.observations.trial_id,
        "created_at": trial.created_at, "acceptance_evidence": False,
        **authoritative, "trace": trial.trace,
        "runtime_evidence": trial.runtime_evidence, "gaps": trial.gaps,
    }
    safe = {**bundle, "trace": redact(trial.trace, trial.secrets), "runtime_evidence": redact(trial.runtime_evidence, trial.secrets), "gaps": redact(trial.gaps, trial.secrets)}
    safe["redactions_applied"] = safe != bundle
    safe["sanitization"] = {"mode": "known-secrets-and-wire-fields", "score_inputs": "preserved", "unknown_secrets_checked": False, "source_responsibility": "caller-reviewed"}
    raw = json.dumps(safe, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    actual = json.loads(raw)
    persisted_dataset = EvaluationDataset.model_validate(actual["dataset"])
    persisted_observations = ObservationBatch.model_validate(actual["observations"])
    persisted_report = EvaluationReport.model_validate(actual["report"])
    if score_evaluation(persisted_dataset, persisted_observations).model_dump(mode="json") != persisted_report.model_dump(mode="json"):
        raise ValueError("Serialized archive changes authoritative scoring")
    temp = folder / (".trial-" + uuid4().hex + ".tmp")
    final = folder / "trial.json"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link creation is atomic and refuses an existing target on NTFS/POSIX.
        # Unsupported filesystems fail closed; do not fall back to overwriting rename.
        os.link(temp, final)
    finally:
        if temp.exists():
            temp.unlink()
    return final


def archive_trial(output_root, trial):
    return commit_trial(reserve_trial(output_root, trial.observations.trial_id), trial)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown argument values, which might be a token.
        self.exit(2, "Invalid evaluation CLI arguments; use --help. Token values are accepted only through environment bindings.\n")


def parser():
    result = SafeParser(description="Score or execute an evaluation; archives never constitute acceptance approval.")
    sub = result.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    offline = sub.add_parser("score", help="Score supplied observations without any runtime requests")
    offline.add_argument("--dataset", required=True)
    offline.add_argument("--observations", required=True)
    offline.add_argument("--output", required=True)
    execute = sub.add_parser("execute", help="Drive the authorized runtime API, defaulting to read-only business effects")
    for name in ("dataset", "configuration", "target", "bindings", "base-url", "trial-id", "output"):
        execute.add_argument("--" + name, required=True)
    execute.add_argument("--allow-effects", action="store_true")
    execute.add_argument("--allow-sealed", action="store_true")
    execute.add_argument("--step-timeout", type=float, default=30)
    return result


def read_model(path, model):
    # Parse original JSON bytes directly. This retains integer versus float types.
    return model.model_validate_json(Path(path).read_bytes())


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        dataset = read_model(args.dataset, EvaluationDataset)
        if args.command == "score":
            observations = read_model(args.observations, ObservationBatch)
            report = score_evaluation(dataset, observations)
            trial = EvaluationTrial(dataset, observations, report, [], {},
                                    [{"code": "OFFLINE_OBSERVATIONS_UNVERIFIED", "detail": "No runtime provenance was verified by this command"}],
                                    datetime.now(timezone.utc).isoformat())
            final = archive_trial(args.output, trial)
        else:
            configuration = read_model(args.configuration, EvaluationConfiguration)
            target = read_model(args.target, RunnerTarget)
            bindings = read_model(args.bindings, CredentialBindings)
            credentials = [RuntimeCredential(b.user_id, b.workspace_id, os.environ[b.env_var], b.runtime_session_id)
                           for b in bindings.bindings]
            options = RunnerOptions(allow_effects=args.allow_effects, allow_sealed=args.allow_sealed,
                                    step_timeout_seconds=args.step_timeout)
            runner = RuntimeEvaluationRunner(args.base_url, target, credentials, options)
            # Validate purpose/configuration and sealed policy before reserving or making HTTP calls.
            score_evaluation(dataset, ObservationBatch(dataset=dataset.reference, configuration=configuration,
                                                       trial_id=args.trial_id, cases=[]))
            if dataset.reference.purpose == "sealed" and not options.allow_sealed:
                raise ValueError("Sealed execution requires a separate explicit switch")
            folder = reserve_trial(args.output, args.trial_id)
            trial = runner.run(dataset, configuration, args.trial_id)
            final = commit_trial(folder, trial)
        print(str(final))
        # The CLI completed and archived even when the report describes evaluation failures.
        return 0
    except (ValueError, KeyError, OSError):
        # Do not expose Pydantic input values, environment values or request credentials.
        print("Evaluation command failed validation, credential loading or archive creation. No archive was overwritten; inspect input files and any incomplete trial directory.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
