"""Explicit launcher for three synthetic process fault windows.

Example: .venv/Scripts/python.exe scripts/verify_process_faults.py --run
Only intent_process_faults_test is permitted. Existing records are never erased.
Ordinary backend pytest skips these tests. No production fault switch is added.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND / "tests"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="explicitly run in the dedicated local fault-test database")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to authorize this dedicated synthetic test execution")
    app_url = os.environ.get("INTENT_PROCESS_DATABASE_URL", "postgresql+psycopg://intent_app:local-app-only@127.0.0.1:54329/intent_process_faults_test")
    owner_url = os.environ.get("INTENT_PROCESS_MIGRATION_DATABASE_URL", "postgresql+psycopg://intent_owner:local-owner-only@127.0.0.1:54329/intent_process_faults_test")
    trial = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
    report_dir = BACKEND.parent / "output" / "process-faults" / trial
    report_dir.mkdir(parents=True, exist_ok=False)
    for key in list(os.environ):
        if key.startswith("INTENT_"):
            del os.environ[key]
    os.environ.update({"INTENT_ENV": "test", "INTENT_MODEL_MODE": "fixture", "INTENT_ALLOW_DEV_AUTH": "true",
                       "INTENT_DATABASE_URL": app_url, "INTENT_MIGRATION_DATABASE_URL": owner_url,
                       "INTENT_PROCESS_FAULTS_ENABLED": "1", "INTENT_PROCESS_FAULT_REPORT_DIR": str(report_dir)})
    from process_fault_harness import guard_environment, safe_text
    guard_environment()  # Must succeed before any migration or database connection.
    from sqlalchemy import create_engine, text, inspect
    owner = create_engine(owner_url)
    with owner.connect() as db:
        if inspect(db).has_table("runs"):
            active = db.execute(text("SELECT count(*) FROM runs WHERE status IN ('RECEIVED','ROUTING','EXECUTING','WAITING_INPUT','WAITING_CONFIRMATION')")).scalar()
            if active:
                raise RuntimeError("Prior unfinished fault evidence exists; inspect it before another run. Nothing was changed.")
    from intent_platform.migrate import main as migrate
    migrate()
    command = [sys.executable, "-m", "pytest", "-q", "tests/test_process_fault_windows.py", "--tb=short"]
    result = subprocess.run(command, cwd=BACKEND, env=os.environ.copy(), capture_output=True, text=True, encoding="utf-8",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = safe_text(result.stdout + result.stderr)
    print(output, end="")
    with (report_dir / "pytest.txt").open("x", encoding="utf-8") as stream:
        stream.write(output)
    cases = []
    for path in sorted(report_dir.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        cases.append({"case": value["case"], "trial_id": value["trial_id"], "status": value["status"], "report": path.name,
                      "run_id": value.get("run_id"), "runtime_downstream_posts": value.get("runtime_downstream_posts")})
    success = result.returncode == 0 and len(cases) == 3 and all(case["status"] == "passed" for case in cases)
    summary = {"schema_version": "1", "trial_id": trial, "database": "intent_process_faults_test", "status": "passed" if success else "failed",
               "pytest_exit_code": result.returncode, "cases": cases, "fixture_mode": True, "acceptance_evidence": False,
               "frontend_dom_tested": False, "evidence_layers": ["real PostgreSQL restricted runtime role", "actual subprocess termination and restart", "real loopback HTTP", "synthetic downstream writes"],
               "fault_placement": "test-only checkpoints", "lease_timestamps_modified": False, "existing_records_deleted": False}
    with (report_dir / "summary.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
    print("Process fault report: " + str(report_dir / "summary.json"))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
