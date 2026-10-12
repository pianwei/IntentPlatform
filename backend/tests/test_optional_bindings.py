"""Optional object filters stay optional; supplied IDs still need trusted grants."""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from intent_platform import context_binding as cb
from intent_platform.zhiyu import function_packages


def setup_binding(monkeypatch):
    definition = next(p["definition"] for p in function_packages({"package_id": "d", "version": 1}, three_layer=True)
                      if p["key"] == "zhiyu.marketing.list")
    run = SimpleNamespace(bindings={}, workspace_id="ws", user_id="guest")
    profile = {"entity_type": "zhiyu:Customer", "kind": "zhiyu"}
    release = SimpleNamespace(snapshot={"resolver_profiles": {"zhiyu.entity.Customer@1": profile}, "prompt_policy_version": "three-layer-v1"})
    request = {"participants": [{"role": "target", "type": "zhiyu:MarketingTask"}], "arguments": {}}
    calls = []
    def resolve(reference, pinned, workspace, user, query, selected_id=None, **kwargs):
        calls.append((query, selected_id))
        return [] if query == "ZY-C002" else [{"id": "ZY-C001", "label": "星澜科技", "type": "zhiyu:Customer", "authorized": True}]
    monkeypatch.setattr(cb, "resolve", resolve)
    monkeypatch.setattr(cb, "resolver_kwargs", lambda *a: {})
    return definition, run, release, request, calls


def test_absent_optional_filter_has_no_question_or_resolver_call(monkeypatch):
    definition, run, release, request, calls = setup_binding(monkeypatch)
    facts, question = cb.bind_arguments(None, run, "0", request, {"definition": definition}, release)
    assert facts == {"identity.authenticated": True} and question is None and calls == []
    assert run.bindings == {} and request["arguments"] == {}
    cb.revalidate_call_bindings(None, run, SimpleNamespace(definition=definition, arguments={}, bindings={}), release)
    assert calls == []


def test_optional_filter_when_provided_is_bound_and_revalidated(monkeypatch):
    definition, run, release, request, calls = setup_binding(monkeypatch)
    request["participants"].append({"role": "customer", "type": "zhiyu:Customer", "mention": "星澜科技"})
    facts, question = cb.bind_arguments(None, run, "0", request, {"definition": definition}, release)
    assert question is None and facts["entity.customer.authorized"] is True
    assert request["arguments"] == {"customer_id": "ZY-C001"} and len(calls) == 1
    cb.revalidate_call_bindings(None, run, SimpleNamespace(definition=definition, arguments=request["arguments"], bindings=run.bindings), release)
    assert len(calls) == 2


def test_optional_filter_cannot_read_unauthorized_object(monkeypatch):
    definition, run, release, request, calls = setup_binding(monkeypatch)
    request["participants"].append({"role": "customer", "type": "zhiyu:Customer", "mention": "ZY-C002"})
    with pytest.raises(cb.BindingDenied, match="No authorized"):
        cb.bind_arguments(None, run, "0", request, {"definition": definition}, release)
    assert len(calls) == 1 and request["arguments"] == {}


@pytest.mark.parametrize("value", [None, "", "ZY-C001"])
def test_explicit_optional_value_without_participant_is_not_treated_as_absent(monkeypatch, value):
    definition, run, release, request, calls = setup_binding(monkeypatch)
    request["arguments"]["customer_id"] = value
    with pytest.raises(cb.BindingDenied):
        cb.bind_arguments(None, run, "0", request, {"definition": definition}, release)
    assert calls == []


def test_empty_mention_and_saved_binding_do_not_bypass_resolution(monkeypatch):
    definition, run, release, request, calls = setup_binding(monkeypatch)
    request["participants"].append({"role": "customer", "type": "zhiyu:Customer", "mention": ""})
    _, question = cb.bind_arguments(None, run, "0", request, {"definition": definition}, release)
    assert question["kind"] == "entity_mention"
    request["participants"] = request["participants"][:1]
    with pytest.raises(cb.BindingDenied):
        cb.bind_arguments(None, run, "0", request, {"definition": definition}, release)


def test_required_role_is_not_relaxed_when_parameter_is_missing(monkeypatch):
    definition, run, release, request, calls = setup_binding(monkeypatch)
    definition = deepcopy(definition)
    role = next(r for r in definition["participant_roles"] if r["role"] == "customer")
    role.update(required=True, min_count=1)
    with pytest.raises(cb.BindingDenied):
        cb.bind_arguments(None, run, "0", request, {"definition": definition}, release)


def test_dispatch_rejects_unbound_optional_filter_and_revoked_access(monkeypatch):
    definition, run, release, request, calls = setup_binding(monkeypatch)
    with pytest.raises(cb.BindingDenied, match="unique trusted binding"):
        cb.revalidate_call_bindings(None, run, SimpleNamespace(definition=definition, arguments={"customer_id": "ZY-C001"}, bindings={}), release)
    request["participants"].append({"role": "customer", "type": "zhiyu:Customer", "mention": "星澜科技"})
    cb.bind_arguments(None, run, "0", request, {"definition": definition}, release)
    monkeypatch.setattr(cb, "resolve", lambda *a, **kw: [])
    with pytest.raises(cb.BindingDenied, match="authorization changed"):
        cb.revalidate_call_bindings(None, run, SimpleNamespace(definition=definition, arguments=request["arguments"], bindings=run.bindings), release)
