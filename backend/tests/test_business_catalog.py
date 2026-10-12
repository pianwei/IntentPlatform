"""Business catalogue administration, explicit adoption and compiled relationships."""
import uuid

from sqlalchemy.orm import Session

from intent_platform.db import engine, set_identity
from intent_platform.models import Function, FunctionVersion
from test_local_identity import local


def catalog_path(workspace, suffix=""):
    return f"/api/v2/management/workspaces/{workspace}/business-catalog" + suffix


def test_platform_defaults_are_adopted_explicitly_and_keep_history(client, local):
    workspace = local["ws"]
    assert client.post(f"/api/v2/management/workspaces/{workspace}/function-options/bootstrap",
                       headers=local["headers"]).status_code == 200

    defaults = client.get("/api/v2/management/platform/business-catalog", headers=local["headers"])
    assert defaults.status_code == 200, defaults.text
    customer = next(row for row in defaults.json()["items"] if row["key"] == "business:Customer")

    first = client.post(catalog_path(workspace, "/adopt"), headers=local["headers"],
                        json={"default_id": customer["id"], "version": 1})
    assert first.status_code == 200, first.text
    assert first.json()["default_id"] == customer["id"]
    assert first.json()["adopted_version"] == 1

    changed = client.patch(f"/api/v2/management/platform/business-catalog/{customer['id']}",
                           headers=local["headers"], json={"name": "客户对象", "description": "空间显式采用后才更新",
                                                           "effect": None, "enabled": True, "revision": customer["revision"]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["latest_version"] == 2

    current = client.get(catalog_path(workspace), headers=local["headers"]).json()
    adopted = next(row for row in current["items"] if row["default_id"] == customer["id"])
    assert adopted["adopted_version"] == 1

    impact = client.get(catalog_path(workspace, "/adopt-impact") +
                        f"?default_id={customer['id']}&version=2", headers=local["headers"])
    assert impact.status_code == 200 and impact.json()["target_version"] == 2
    second = client.post(catalog_path(workspace, "/adopt"), headers=local["headers"],
                         json={"default_id": customer["id"], "version": 2})
    assert second.status_code == 200 and second.json()["adopted_version"] == 2

    history = client.get(catalog_path(workspace, f"/{adopted['id']}/versions"), headers=local["headers"])
    assert history.status_code == 200
    assert [row["version"] for row in history.json()] == [3, 2, 1]
    assert history.json()[-1]["definition"]["name"] == "客户"


def test_relationships_use_published_direct_and_composite_versions_and_respect_space(client, local):
    workspace = local["ws"]
    assert client.post(f"/api/v2/management/workspaces/{workspace}/function-options/bootstrap",
                       headers=local["headers"]).status_code == 200
    object_response = client.post(catalog_path(workspace), headers=local["headers"], json={
        "kind": "object", "name": "目录关系测试客户", "description": "测试用业务对象", "enabled": True})
    action_response = client.post(catalog_path(workspace), headers=local["headers"], json={
        "kind": "action", "name": "目录关系测试查询", "description": "只读查询", "effect": "READ_ONLY", "enabled": True})
    assert object_response.status_code == action_response.status_code == 201
    object_item, action_item = object_response.json(), action_response.json()
    suffix = uuid.uuid4().hex[:10]
    direct_id = "fn_catalog_direct_" + suffix
    composite_id = "fn_catalog_composite_" + suffix
    direct = {"name": "目录关系直连功能", "execution_kind": "direct", "object_type": object_item["key"], "action": action_item["key"]}
    composite = {"name": "目录关系组合功能", "execution_kind": "composite",
                 "composite": {"dependencies": [{"function_id": direct_id, "version": 1}]}}
    with Session(engine) as db, db.begin():
        set_identity(db, local["user"])
        db.add(Function(id=direct_id, workspace_id=workspace, definition=direct, status="published", latest_version=1))
        db.add(Function(id=composite_id, workspace_id=workspace, definition=composite, status="published", latest_version=1))
        db.add(FunctionVersion(workspace_id=workspace, function_id=direct_id, version=1, definition=direct, compiled={}))
        db.add(FunctionVersion(workspace_id=workspace, function_id=composite_id, version=1, definition=composite, compiled={}))

    response = client.get(catalog_path(workspace, "/relationships"), headers=local["headers"])
    assert response.status_code == 200, response.text
    customer = next(row for row in response.json()["objects"] if row["id"] == object_item["id"])
    action = next(row for row in customer["actions"] if row["key"] == action_item["key"])
    function = next(row for row in action["functions"] if row["id"] == direct_id)
    assert function["version"] == 1
    assert function["composites"] == [{"id": composite_id, "name": "目录关系组合功能", "version": 1}]
    assert client.get(catalog_path("workspace-not-authorized"), headers=local["headers"]).status_code == 403
