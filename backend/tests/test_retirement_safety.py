"""Non-database regression for reviewed retirement scope and restore comparison."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

retire = script("retire_demo_catalog")
restore = script("verify_backup_restore")

def test_workspace_primary_key_is_never_treated_as_a_retired_resource():
    ids = retire._resource_ids({"functions": {"fn_retired"}, "demo_states": {"ws-dev"}})
    assert ids == {"fn_retired"}
    assert not retire._references({"workspace_id": "ws-dev", "resource_id": "ws-dev"}, ids)
    assert retire._references({"definition": {"composite": {"dependencies": ["fn_retired"]}}}, ids)

def test_recursive_reverse_check_covers_json_keys_versions_and_urls_without_substring_collision():
    ids = {"fn_retired", "release_old"}
    assert retire._references({"versions": {"fn_retired@2": {}}}, ids)
    assert retire._references("/api/agent/releases/release_old", ids)
    assert not retire._references("fn_retired_other", ids)
    assert not retire._references({"release": "release_new", "numbers": [1, None, False]}, ids)

def policy(expression, *, name="workspace_lifecycle_audit", role="{public}", command="INSERT"):
    return ("audit", name, "PERMISSIVE", role, command, None, expression)

BEFORE = "action = ANY ((ARRAY['workspace.rename'::character varying, 'workspace.archive'::character varying])::text[])"
AFTER = "action = ANY (ARRAY[('workspace.rename'::character varying)::text, ('workspace.archive'::character varying)::text])"

def test_only_lossless_literal_array_cast_is_canonicalized():
    assert restore.canonical_lifecycle_policy(BEFORE) == restore.canonical_lifecycle_policy(AFTER)
    assert restore.compare_policies([policy(BEFORE)], [policy(AFTER)]) == [(BEFORE, AFTER)]
    assert restore.compare_policies([policy(BEFORE)], [policy(BEFORE)]) == []
    assert restore.canonical_lifecycle_policy(None) is None

@pytest.mark.parametrize("changed", [
    AFTER.replace("workspace.archive", "workspace.delete"),
    AFTER.replace("= ANY", "<> ANY"),
    AFTER.replace("action", "resource_id"),
    AFTER.replace("character varying", "character varying(5)"),
    AFTER + " OR true",
])
def test_cast_exception_does_not_ignore_real_policy_semantics(changed):
    with pytest.raises(AssertionError):
        restore.compare_policies([policy(BEFORE)], [policy(changed)])

@pytest.mark.parametrize("metadata", [{"name": "other_policy"}, {"role": "{intent_app}"}, {"command": "ALL"}])
def test_cast_exception_is_limited_to_the_reviewed_policy(metadata):
    with pytest.raises(AssertionError):
        restore.compare_policies([policy(BEFORE)], [policy(AFTER, **metadata)])

def test_cast_normalization_preserves_literal_contents_and_escaped_quotes():
    before = "(ARRAY['x::character varying'::character varying, 'a''b'::character varying])::text[]"
    after = "ARRAY[('x::character varying'::character varying)::text, ('a''b'::character varying)::text]"
    assert restore.canonical_lifecycle_policy(before) == restore.canonical_lifecycle_policy(after)
    assert "x::character varying" in restore.canonical_lifecycle_policy(before)

def test_retirement_guard_covers_accounts_keys_configuration_and_versions():
    assert {"local_accounts", "local_credentials", "integration_keys", "memberships", "workspaces",
            "model_profiles", "model_profile_versions", "workspace_governance", "assets"} <= retire.PROTECTED
    assert "model_profile_versions" not in retire.IMMUTABLE_TABLES
    assert len(retire.REVIEWED_FIXTURES) == 3
