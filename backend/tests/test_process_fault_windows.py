"""Actual OS process interruption, opt-in dedicated database only.

Run scripts/verify_process_faults.py --run. Ordinary pytest records three skips.
The response-loss case proves HTTP event recovery, not frontend DOM recovery.
"""
import os
from concurrent.futures import ThreadPoolExecutor
import time
import httpx
import pytest
from process_fault_harness import FaultCase, guard_environment, wait_for

pytestmark = pytest.mark.skipif(os.environ.get("INTENT_PROCESS_FAULTS_ENABLED") != "1",
                              reason="Actual process faults require the isolated dedicated launcher")


def test_process_exit_before_dispatch_recovers_exactly_once():
    guard_environment()
    with FaultCase("before-dispatch") as case:
        worker = case.worker_and_confirm("before-dispatch")
        case.evidence["checkpoint"] = worker.barrier()
        before = case.snapshot(); case.evidence["before_kill"] = before
        assert len(before["calls"]) == 1
        call = before["calls"][0]
        assert call["status"] == "EXECUTING" and call["dispatched_at"] is None
        assert before["runtime_downstream_posts"] == 0
        assert case.stop(worker)["exit_code"] != 0
        recovery_started = time.monotonic()
        recovered_worker = case.start("worker")
        wait_for(lambda: case.run()["status"] == "SUCCEEDED", seconds=40, description="natural lease recovery before send")
        after = case.snapshot(); case.evidence["after_recovery"] = after
        case.evidence["recovery_elapsed_seconds"] = time.monotonic() - recovery_started
        assert len(after["calls"]) == 1 and after["calls"][0]["id"] == call["id"]
        assert after["calls"][0]["status"] == "SUCCEEDED" and after["runtime_downstream_posts"] == 1
        assert sum(e["type"] == "RESULT" for e in after["events"]) == 1
        time.sleep(1.2)  # Multiple real worker polling intervals, no timestamp edits.
        assert case.count() - case.baseline == 1
        case.stop(recovered_worker)


def test_process_exit_after_downstream_success_stays_unknown_without_replay():
    guard_environment()
    with FaultCase("after-downstream") as case:
        worker = case.worker_and_confirm("after-downstream")
        case.evidence["checkpoint"] = worker.barrier()
        before = case.snapshot(); case.evidence["before_kill"] = before
        assert len(before["calls"]) == 1
        call = before["calls"][0]
        assert call["status"] == "EXECUTING" and call["dispatched_at"] is not None and not call["result_present"]
        assert before["runtime_downstream_posts"] == 1 and not any(e["type"] == "RESULT" for e in before["events"])
        assert case.stop(worker)["exit_code"] != 0
        recovery_started = time.monotonic()
        recovered_worker = case.start("worker")
        wait_for(lambda: case.run()["status"] == "UNKNOWN", seconds=40, description="natural expired sent lease to UNKNOWN")
        time.sleep(1.2)
        after = case.snapshot(); case.evidence["after_recovery"] = after
        case.evidence["recovery_elapsed_seconds"] = time.monotonic() - recovery_started
        assert len(after["calls"]) == 1 and after["calls"][0]["id"] == call["id"]
        assert after["calls"][0]["status"] == "UNKNOWN" and after["runtime_downstream_posts"] == 1
        assert not any(e["type"] == "RESULT" for e in after["events"])
        case.evidence["manual_reconciliation_required"] = True
        case.evidence["downstream_status_endpoint"] = "absent; no successful reconciliation claimed"
        case.stop(recovered_worker)


def test_process_exit_after_event_commit_before_http_body_recovers_cursor():
    guard_environment()
    with FaultCase("before-events-delivery") as case:
        worker = case.worker_and_confirm()
        wait_for(lambda: case.run()["status"] == "SUCCEEDED", description="committed call and outbox event")
        case.stop(worker)
        before = case.snapshot(); case.evidence["committed_before_delivery"] = before
        assert before["runtime_downstream_posts"] == 1
        assert sum(e["type"] == "RESULT" for e in before["events"]) == 1
        case.stop(case.api)
        case.api = case.start_api("before-events-delivery")
        path = f"/api/v2/runtime/conversations/{case.cid}/events?after={case.cursor}"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(case.request, "GET", path, runtime=True)
            point = case.api.barrier(); case.evidence["checkpoint"] = point
            assert not pending.done(), "Client must not have received the event body"
            assert case.snapshot()["calls"] == before["calls"]
            assert case.stop(case.api)["exit_code"] != 0
            with pytest.raises(httpx.TransportError) as disconnected:
                pending.result(timeout=5)
            case.evidence["actual_client_disconnect"] = type(disconnected.value).__name__
        case.api = case.start_api()
        recovered = case.request("GET", path, runtime=True)
        replayed = case.request("GET", path, runtime=True)
        assert recovered == replayed
        assert [event["sequence"] for event in recovered] == point["sequences"]
        assert len([e for e in recovered if e["run_id"] == case.rid and e["event_type"] == "RESULT"]) == 1
        after = case.snapshot(); case.evidence["after_recovery"] = after
        assert after["calls"] == before["calls"] and after["events"] == before["events"]
        assert after["runtime_downstream_posts"] == 1
        case.evidence["recovered_event_sequences"] = [e["sequence"] for e in recovered]
        case.evidence["same_cursor_repeat_identical"] = True
        case.evidence["message_or_confirmation_resubmitted"] = False
