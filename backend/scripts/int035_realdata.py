"""Narrow, test-only replay adapter for the INT-035 CRM snapshot.

This module never connects to the source CRM. It accepts only the locally
exported, pseudonymized snapshot and builds a fresh Zhiyu engine state for an
isolated evaluation database.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _money_cents(value: Any) -> int | None:
    if value is None or _text(value) == "":
        return None
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _source_id_map(rows: list[dict[str, Any]], field: str, existing: dict[str, Any]) -> dict[str, str]:
    """Keep simulator IDs stable while assigning colliding source IDs a namespace."""
    result: dict[str, str] = {}
    occupied = set(existing)
    for row in rows:
        raw = _text(row.get(field))
        if not raw:
            continue
        if raw in result:
            raise ValueError(f"INT-035 source snapshot contains duplicate {field}")
        candidate = raw
        if candidate in occupied:
            candidate = f"SWB-{raw}"
            while candidate in occupied:
                candidate = f"SWB-{candidate}"
        result[raw] = candidate
        occupied.add(candidate)
    return result


def source_state(snapshot: dict[str, Any], base_state: dict[str, Any]) -> dict[str, Any]:
    """Merge approved source rows over the original fixture without collisions.

    The source remains separately identifiable in private replay metadata. The
    simulator supplies categories and records that the source snapshot lacks;
    values from different customers or products are never cross-filled.
    """
    meta = snapshot.get("meta") or {}
    if meta.get("read_only_transaction_verified") is not True:
        raise ValueError("INT-035 source snapshot lacks verified read-only provenance")
    # The user explicitly allowed source-origin verification item 3 to remain
    # unverified. We still require the export itself to have been read-only;
    # reports retain the origin caveat and never call it production data.

    state = deepcopy(base_state)
    provenance: dict[str, dict[str, str]] = {}
    for category in (
        "customers", "managers", "products", "cooperation", "contacts", "policies", "faqs",
        "todos", "segments", "marketing_tasks", "visits", "postloan_tasks",
    ):
        values = state.get(category)
        if isinstance(values, dict):
            provenance[category] = {str(key): "project_simulation" for key in values}
    provenance["feedback"] = {
        f"{index}:{_text(row.get('customer_id'))}": "project_simulation"
        for index, row in enumerate(state.get("feedback", []))
    }
    provenance["performance"] = {
        f"{index}:{_text(row.get('manager_id'))}:{_text(row.get('period'))}": "project_simulation"
        for index, row in enumerate(state.get("performance", []))
    }
    provenance["postloan_checks"] = {
        f"{index}:{_text(row.get('customer_id'))}:{_text(row.get('postloan_id'))}": "project_simulation"
        for index, row in enumerate(state.get("postloan_checks", []))
    }

    manager_rows = [row for row in snapshot.get("managers", []) if _text(row.get("manager_id"))]
    manager_ids_referenced = {
        _text(row.get("manager_id")) for row in snapshot.get("customers", []) if _text(row.get("manager_id"))
    }
    known_manager_ids = {_text(row.get("manager_id")) for row in manager_rows}
    manager_rows.extend({"manager_id": manager_id, "name": ""}
                        for manager_id in sorted(manager_ids_referenced - known_manager_ids))
    manager_ids = _source_id_map(manager_rows, "manager_id", state["managers"])
    for row in manager_rows:
        raw_id = _text(row.get("manager_id"))
        runtime_id = manager_ids[raw_id]
        state["managers"][runtime_id] = {
            "manager_id": runtime_id,
            "name": _text(row.get("name")) or "未登记客户经理",
        }
        provenance.setdefault("managers", {})[runtime_id] = "super_workbench_source"

    metric_by_customer = {_text(row.get("customer_id")): row for row in snapshot.get("customer_latest_metrics", [])}
    payroll_by_customer = {_text(row.get("customer_id")): row for row in snapshot.get("payroll", [])}
    limits_by_customer: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot.get("credit_limits", []):
        if _text(row.get("currency_code")) == "CNY":
            limits_by_customer.setdefault(_text(row.get("customer_id")), []).append(row)
    tags_by_customer: dict[str, list[str]] = {}
    for row in snapshot.get("customer_tags", []):
        cid = _text(row.get("customer_id"))
        tag = _text(row.get("value")) or _text(row.get("label"))
        if cid and tag and tag not in tags_by_customer.setdefault(cid, []):
            tags_by_customer[cid].append(tag)

    source_customer_rows = [row for row in snapshot.get("customers", []) if _text(row.get("customer_id"))]
    customer_ids = _source_id_map(source_customer_rows, "customer_id", state["customers"])
    for row in source_customer_rows:
        raw_cid = _text(row.get("customer_id"))
        cid = customer_ids[raw_cid]
        metric = metric_by_customer.get(raw_cid, {})
        payroll = payroll_by_customer.get(raw_cid, {})
        limits = limits_by_customer.get(raw_cid, [])
        credit_used = None
        if len(limits) == 1 and limits[0].get("limit_amount") is not None and limits[0].get("available_amount") is not None:
            credit_used = _money_cents(Decimal(str(limits[0]["limit_amount"])) - Decimal(str(limits[0]["available_amount"])))
        state["customers"][cid] = {
            "customer_id": cid,
            "name": _text(row.get("name")),
            "city": "本次导出未包含城市",
            "manager_id": manager_ids.get(_text(row.get("manager_id")), _text(row.get("manager_id"))),
            "industry": "已导出行业编码，未映射名称",
            "tags": list(tags_by_customer.get(raw_cid, [])),
            "deposit_cents": _money_cents(metric.get("cny_deposit_balance")),
            "credit_used_cents": credit_used,
            "payroll_count": int(payroll["payroll_count"]) if payroll.get("payroll_count") is not None else None,
        }
        provenance.setdefault("customers", {})[cid] = "super_workbench_source"

    source_product_rows = [row for row in snapshot.get("products", []) if _text(row.get("product_id"))]
    product_ids = _source_id_map(source_product_rows, "product_id", state["products"])
    for row in source_product_rows:
        raw_pid = _text(row.get("product_id"))
        pid = product_ids[raw_pid]
        state["products"][pid] = {
            "product_id": pid,
            "name": _text(row.get("name")),
            "category": _text(row.get("category")),
            "department": _text(row.get("department")),
            "description": _text(row.get("description")),
            "admission": "源数据未提供准入规则",
            "currency": "源数据未提供币种",
        }
        provenance.setdefault("products", {})[pid] = "super_workbench_source"

    source_segment_rows = [row for row in snapshot.get("marketing_segments", []) if _text(row.get("segment_id"))]
    segment_ids = _source_id_map(source_segment_rows, "segment_id", state["segments"])
    for row in source_segment_rows:
        raw_sid = _text(row.get("segment_id"))
        sid = segment_ids[raw_sid]
        name = _text(row.get("name"))
        if sid and name:
            state["segments"][sid] = {
                "segment_id": sid,
                "name": name,
                "tag": name,
                "description": _text(row.get("description")) or "源数据未提供客群说明",
            }
            provenance.setdefault("segments", {})[sid] = "super_workbench_source"
    for link in snapshot.get("marketing_segment_customers", []):
        raw_sid, raw_cid = _text(link.get("segment_id")), _text(link.get("customer_id"))
        sid, cid = segment_ids.get(raw_sid), customer_ids.get(raw_cid)
        if sid in state["segments"] and cid in state["customers"]:
            tag = state["segments"][sid]["tag"]
            if tag not in state["customers"][cid]["tags"]:
                state["customers"][cid]["tags"].append(tag)

    source_task_rows = [row for row in snapshot.get("marketing_tasks", []) if _text(row.get("task_id"))]
    task_rows = {_text(row.get("task_id")): row for row in source_task_rows}
    task_ids = _source_id_map(source_task_rows, "task_id", state["marketing_tasks"])
    task_links: dict[str, list[str]] = {}
    for link in snapshot.get("marketing_task_customers", []):
        tid, cid = _text(link.get("task_id")), customer_ids.get(_text(link.get("customer_id")))
        if tid in task_rows and cid in state["customers"]:
            task_links.setdefault(tid, []).append(cid)
    manager_names = {mid: row["name"] for mid, row in state["managers"].items()}
    for tid, row in task_rows.items():
        for index, cid in enumerate(dict.fromkeys(task_links.get(tid, [])), 1):
            customer = state["customers"][cid]
            # One source task may cover several customers. Keep the relationship
            # rows distinct in this single-customer test adapter.
            local_id = task_ids[tid] if len(task_links.get(tid, [])) == 1 else f"{task_ids[tid]}-L{index:02d}"
            while local_id in state["marketing_tasks"]:
                local_id = f"SWB-{local_id}"
            state["marketing_tasks"][local_id] = {
                "task_id": local_id,
                "customer_id": cid,
                "title": _text(row.get("title")),
                "owner_name": manager_names.get(customer.get("manager_id"), "未登记管户"),
                "due_on": _text(row.get("end_date")),
                "status": f"SOURCE_STATUS_{_text(row.get('status_code'))}",
            }
            provenance.setdefault("marketing_tasks", {})[local_id] = "super_workbench_source"

    source_visit_rows = [row for row in snapshot.get("visits", []) if _text(row.get("visit_id"))]
    visit_ids = _source_id_map(source_visit_rows, "visit_id", state["visits"])
    for row in source_visit_rows:
        raw_vid, raw_cid = _text(row.get("visit_id")), _text(row.get("customer_id"))
        vid, cid = visit_ids[raw_vid], customer_ids.get(raw_cid)
        if cid not in state["customers"]:
            continue
        state["visits"][vid] = {
            "visit_id": vid,
            "customer_id": cid,
            "visited_on": _text(row.get("visited_at"))[:10] or "源数据未提供日期",
            "status": f"SOURCE_STATUS_{_text(row.get('status_code'))}" if _text(row.get("status_code")) else "源数据未提供状态",
            # Free-text notes are not needed to test visit lookup and can
            # contain customer information. Keep only structured visit facts.
            "minutes": "",
        }
        provenance.setdefault("visits", {})[vid] = "super_workbench_source"

    state["_int035_provenance"] = provenance
    state["_int035_source_key_map"] = {
        "managers": manager_ids,
        "customers": customer_ids,
        "products": product_ids,
        "segments": segment_ids,
        "marketing_tasks": task_ids,
        "visits": visit_ids,
    }
    state["_int035_source_snapshot"] = True
    return state


def source_cases(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a small, frozen set from available real source rows only."""
    from intent_platform import zhiyu
    from intent_platform.output_contract import project_output

    meta = snapshot.get("meta") or {}
    if meta.get("read_only_transaction_verified") is not True:
        raise ValueError("INT-035 source snapshot lacks verified read-only provenance")
    customers = {_text(row.get("customer_id")): row for row in snapshot.get("customers", [])}
    metrics = {_text(row.get("customer_id")): row for row in snapshot.get("customer_latest_metrics", [])}
    payroll = {_text(row.get("customer_id")): row for row in snapshot.get("payroll", [])}
    limits: dict[str, list[dict[str, Any]]] = {}
    for row in snapshot.get("credit_limits", []):
        if _text(row.get("currency_code")) == "CNY":
            limits.setdefault(_text(row.get("customer_id")), []).append(row)
    named_products = [row for row in snapshot.get("products", [])
                      if all(_text(row.get(field)) for field in ("name", "category", "department", "description"))]
    name_counts: dict[str, int] = {}
    for row in named_products:
        name = _text(row.get("name")).casefold()
        name_counts[name] = name_counts.get(name, 0) + 1
    # Exact-name tests need unambiguous gold answers; don't silently choose
    # the first of duplicate product labels.
    products = [row for row in named_products if name_counts[_text(row.get("name")).casefold()] == 1]
    products = [row for row in products if len([
        candidate for candidate in products
        if _text(row.get("name")).casefold() in "".join(
            _text(candidate.get(field)) for field in ("name", "description", "category", "department")
        ).casefold()
    ]) == 1]
    replay = source_state(snapshot, zhiyu.initial_state())
    source_ids = replay["_int035_source_key_map"]
    runtime_customer_ids = source_ids["customers"]
    runtime_product_ids = source_ids["products"]
    runtime_segment_ids = source_ids["segments"]
    definitions = {item["key"]: item["definition"] for item in zhiyu.function_packages()}
    probe_number = 0

    def contract_valid(key: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Preflight actual source-backed fixture output before freezing a paid case."""
        nonlocal probe_number
        probe_number += 1
        try:
            result, _ = zhiyu.execute(key, arguments, replay, f"int035-case-probe-{probe_number}",
                                      persona_id="supervisor")
            if result.get("status") != "SUCCEEDED":
                return None
            data = result.get("data") or {}
            project_output(definitions[key], data)
            return data
        except (KeyError, TypeError, ValueError):
            return None

    detail_products = [row for row in products if contract_valid(
        "zhiyu.product.detail", {"product_id": runtime_product_ids[_text(row.get("product_id"))]}) is not None]
    searchable_products = []
    for row in detail_products:
        result = contract_valid("zhiyu.product.search", {"query": _text(row.get("name"))})
        if result and len(result.get("products", [])) == 1 and result["products"][0].get("product_id") == runtime_product_ids[_text(row.get("product_id"))]:
            searchable_products.append(row)
    if not customers or not detail_products:
        raise ValueError("INT-035 snapshot has no usable customer or product records")

    cases: list[dict[str, Any]] = []

    def add(case_id: str, group: str, text: str, keys: list[str], assertions: dict[str, Any], *,
            persona_id: str = "supervisor", manager_id: str | None = None,
            expected_status: str = "SUCCEEDED", expected_source: str = "business",
            allow_model_fallback: bool = False) -> None:
        case = {
            "id": case_id,
            "data_origin": "super_workbench_source",
            "group": group,
            "text": text,
            "persona_id": persona_id,
            "expected_keys": keys,
            "allowed_keys": keys,
            "expected_source": expected_source,
            "expected_status": expected_status,
            "assertions": assertions,
            "allow_model_fallback": allow_model_fallback,
        }
        if manager_id:
            case["eval_persona_manager_id"] = manager_id
        cases.append(case)

    # Every source customer with a complete, correctly aligned profile metric
    # set gets one fact-checked case. No missing metric is coerced to zero.
    source_names = [_text(row.get("name")).casefold() for row in replay["customers"].values()]
    unique_runtime_names = {name for name in source_names if name and source_names.count(name) == 1}
    profile_ids = [cid for cid in customers if cid in metrics and cid in payroll and len(limits.get(cid, [])) == 1
                   and _text(customers[cid].get("name")).casefold() in unique_runtime_names
                   and _text(customers[cid].get("manager_id")) in source_ids["managers"]
                   and metrics[cid].get("cny_deposit_balance") is not None
                   and limits[cid][0].get("limit_amount") is not None
                   and limits[cid][0].get("available_amount") is not None]
    for index, cid in enumerate(profile_ids, 1):
        customer, metric, pay, credit = customers[cid], metrics[cid], payroll[cid], limits[cid][0]
        deposit_cents = _money_cents(metric["cny_deposit_balance"])
        credit_used_cents = _money_cents(Decimal(str(credit["limit_amount"])) - Decimal(str(credit["available_amount"])))
        add(
            f"crm-profile-{index:02d}", "single",
            f"查一下{customer['name']}的存款余额、授信已使用金额和代发人数。",
            ["zhiyu.customer.profile"],
            {
                "business_calls": 1,
                "answer_source": "business",
                "data.customer_id": runtime_customer_ids[cid],
                # The fixture executor stores integer fen and converts back
                # to yuan. Freeze exactly that deterministic, cent-rounded
                # representation rather than comparing binary floats made
                # independently from the unrounded CRM decimals.
                "data.deposit": float(Decimal(deposit_cents) / 100),
                "data.credit_used": float(Decimal(credit_used_cents) / 100),
                "data.payroll_count": int(pay["payroll_count"]),
            },
            persona_id="manager_a", manager_id=source_ids["managers"].get(_text(customer.get("manager_id"))),
        )
        cases[-1]["source_customer_id"] = cid

    # Two identity lookups use different owners; source aliases replace names.
    owned_customers = [row for row in customers.values()
                       if _text(row.get("manager_id")) in source_ids["managers"]]
    searchable_customers = []
    for customer in owned_customers:
        result = contract_valid("zhiyu.customer.search", {"query": _text(customer.get("name"))})
        if result and len(result.get("customers", [])) == 1 and result["customers"][0].get("customer_id") == runtime_customer_ids[_text(customer.get("customer_id"))]:
            searchable_customers.append(customer)
    for index, customer in enumerate(searchable_customers[:2], 1):
        cid = _text(customer.get("customer_id"))
        add(
            f"crm-customer-search-{index:02d}", "single",
            f"帮我定位名为{customer['name']}的客户。",
            ["zhiyu.customer.search"],
            {"business_calls": 1, "answer_source": "business", "data.customers.0.customer_id": runtime_customer_ids[cid]},
            persona_id="manager_a", manager_id=source_ids["managers"].get(_text(customer.get("manager_id"))),
        )
        cases[-1]["source_customer_id"] = _text(customer.get("customer_id"))

    for index, product in enumerate(detail_products[:2], 1):
        add(
            f"crm-product-detail-{index:02d}", "single",
            f"查询产品“{product['name']}”的详细说明。",
            ["zhiyu.product.detail"],
            {"business_calls": 1, "answer_source": "business", "data.product_id": runtime_product_ids[_text(product["product_id"])]},
        )
        cases[-1]["source_product_id"] = _text(product["product_id"])
    for index, product in enumerate(searchable_products[:2], 1):
        add(
            f"crm-product-search-{index:02d}", "single",
            f"筛选名称为“{product['name']}”的产品列表。",
            ["zhiyu.product.search"],
            {"business_calls": 1, "answer_source": "business", "data.products.0.product_id": runtime_product_ids[_text(product["product_id"])]},
        )
        cases[-1]["source_product_id"] = _text(product["product_id"])

    segment_links = snapshot.get("marketing_segment_customers", [])
    linked_segment_ids = {_text(link.get("segment_id")) for link in segment_links
                          if _text(link.get("customer_id")) in customers}
    segments = [row for row in snapshot.get("marketing_segments", [])
                if _text(row.get("name"))
                and _text(row.get("segment_id")) in linked_segment_ids]
    segment = None
    if segments:
        segment = next((row for row in segments if _text(row.get("name"))), None)
        segment_result = contract_valid("zhiyu.segment.list", {"query": _text(segment.get("name"))}) if segment else None
        matches = (segment_result or {}).get("segments", [])
        if segment and len(matches) == 1 and matches[0].get("name") == _text(segment.get("name")):
            expected_count = matches[0]["customer_count"]
        else:
            segment = None
    if segment:
        add(
            "crm-segment-list-01", "single", f"查看客群包“{segment['name']}”及客户数量。",
            ["zhiyu.segment.list"],
            {"business_calls": 1, "answer_source": "business", "data.segments.0.name": segment["name"],
             "data.segments.0.customer_count": expected_count, "data.segments.length": 1},
        )
        cases[-1]["source_segment_id"] = _text(segment.get("segment_id"))

    task_links = snapshot.get("marketing_task_customers", [])
    task_map = {_text(row.get("task_id")): row for row in snapshot.get("marketing_tasks", [])}
    complete_task_ids = {tid for tid, row in task_map.items()
                         if _text(row.get("title")) and _text(row.get("end_date")) and _text(row.get("status_code"))}
    customer_ids_with_tasks = list(dict.fromkeys(
        _text(link.get("customer_id")) for link in task_links
        if _text(link.get("task_id")) in complete_task_ids and _text(link.get("customer_id")) in customers
        and _text(customers[_text(link.get("customer_id"))].get("manager_id")) in source_ids["managers"]
        and _text(customers[_text(link.get("customer_id"))].get("name")).casefold() in unique_runtime_names
    ))
    task_customer_ids: dict[str, set[str]] = {}
    for link in task_links:
        tid, cid = _text(link.get("task_id")), _text(link.get("customer_id"))
        if tid in task_map and cid in customers:
            task_customer_ids.setdefault(tid, set()).add(cid)
    single_task_customer = next((
        (customers[cid], [task_map[tid] for tid in task_map if cid in task_customer_ids.get(tid, set())])
        for cid in customer_ids_with_tasks
        if all(tid in complete_task_ids for tid in task_map if cid in task_customer_ids.get(tid, set()))
    ), None)
    if single_task_customer:
        customer, related_tasks = single_task_customer
        customer_id = _text(customer.get("customer_id"))
        add(
            "crm-marketing-list-01", "single", f"查询客户{customer['name']}关联的营销任务。",
            ["zhiyu.marketing.list"],
            {"business_calls": 1, "answer_source": "business", "data.tasks.length": len(related_tasks),
             "data.tasks.0.title": _text(related_tasks[0].get("title"))},
            persona_id="manager_a", manager_id=source_ids["managers"].get(_text(customer.get("manager_id"))),
        )
        cases[-1]["source_customer_id"] = customer_id

    visits = [row for row in snapshot.get("visits", [])
              if _text(row.get("visit_id")) and _text(row.get("visited_at")) and _text(row.get("status_code"))
              and _text(row.get("customer_id")) in customers
              and _text(customers[_text(row.get("customer_id"))].get("manager_id")) in source_ids["managers"]
              and _text(customers[_text(row.get("customer_id"))].get("name")).casefold() in unique_runtime_names]
    if visits:
        visit = visits[0]
        customer = customers[_text(visit.get("customer_id"))]
        add(
            "crm-visit-list-01", "single", f"查询客户{customer['name']}最近的拜访记录。",
            ["zhiyu.visit.list"],
            {"business_calls": 1, "answer_source": "business", "data.visits.0.visit_id": source_ids["visits"].get(_text(visit["visit_id"]))},
            persona_id="manager_a", manager_id=source_ids["managers"].get(_text(customer.get("manager_id"))),
        )
        cases[-1]["source_customer_id"] = _text(customer.get("customer_id"))

    comparable = [row for row in searchable_products if _text(row.get("description"))][:2]
    if len(comparable) == 2:
        ids = [runtime_product_ids[_text(row["product_id"])] for row in comparable]
        add(
            "crm-skill-product-comparison-01", "compound",
            f"比较产品“{comparable[0]['name']}”和“{comparable[1]['name']}”的类别、负责部门和产品说明。",
            ["zhiyu.product.detail", "zhiyu.product.detail"],
            {"product_ids": ids, "answer_source": "business"},
        )
        cases[-1]["expected_skill_key"] = "zhiyu.skill.product_comparison"
        customer = next((row for row in owned_customers if _text(row.get("customer_id")) in metrics
                         and _text(row.get("customer_id")) in payroll
                         and len(limits.get(_text(row.get("customer_id")), [])) == 1
                         and _text(row.get("name")).casefold() in unique_runtime_names), None)
        if customer:
            add(
                "crm-autonomous-single-01", "autonomous",
                f"把客户{customer['name']}的存款余额、授信使用和代发人数整理成指标摘要。",
                ["zhiyu.customer.profile"],
                {"business_calls": 1, "answer_source": "business"},
                persona_id="manager_a", manager_id=source_ids["managers"].get(_text(customer.get("manager_id"))),
            )
            cases[-1]["source_customer_id"] = _text(customer.get("customer_id"))
            add(
                "crm-autonomous-multi-01", "autonomous",
                f"查询客户{customer['name']}的存款余额，同时查询产品“{comparable[0]['name']}”的类别和负责部门。",
                ["zhiyu.customer.profile", "zhiyu.product.detail"],
                {"business_calls": 2, "answer_source": "business", "min_steps": 2},
                persona_id="manager_a", manager_id=source_ids["managers"].get(_text(customer.get("manager_id"))),
            )
            cases[-1]["source_customer_id"] = _text(customer.get("customer_id"))

    # The platform must bind a named entity without applying a global object
    # ACL. The selected function then applies its own manager-scope policy;
    # this case verifies it returns no rows for another manager's customer.
    manager_owners = [row for row in owned_customers if _text(row.get("name")).casefold() in unique_runtime_names]
    if len(manager_owners) >= 2:
        first = manager_owners[0]
        other = next((row for row in manager_owners[1:] if row.get("manager_id") != first.get("manager_id")), None)
        if other:
            add(
                "crm-permission-cross-manager-01", "boundary",
                f"帮我定位名为{other['name']}的客户。", ["zhiyu.customer.search"],
                {"business_calls": 1, "data.customers.length": 0},
                persona_id="manager_a", manager_id=source_ids["managers"].get(_text(first.get("manager_id"))),
                expected_status="SUCCEEDED", expected_source="business",
            )
            cases[-1]["source_customer_id"] = _text(other.get("customer_id"))
            cases[-1]["boundary_kind"] = "function_owned_object_authorization"
            cases[-1]["allowed_keys"] = ["zhiyu.customer.search"]

    if len(cases) < 10:
        raise ValueError(f"INT-035 source-backed case set too small: {len(cases)}")
    return cases


def simulator_fill_cases(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Reuse existing synthetic cases for source gaps and partial source data."""
    import re
    from copy import deepcopy

    from intent_platform import zhiyu
    from intent_platform.zhiyu.evaluation import evaluation_cases

    source_keys = {key for case in source_cases(snapshot) for key in case.get("expected_keys", [])}
    simulator = zhiyu.initial_state()
    all_keys = {item["key"] for item in zhiyu.function_packages()}
    source_gaps = all_keys - source_keys
    partially_source_backed = {
        "zhiyu.customer.profile", "zhiyu.product.detail", "zhiyu.visit.list", "zhiyu.marketing.list",
    }

    customer_name_counts: dict[str, int] = {}
    for row in simulator["customers"].values():
        name = _text(row.get("name"))
        customer_name_counts[name] = customer_name_counts.get(name, 0) + 1
    customer_replacements = {
        _text(row.get("name")): f"客户编号{_text(row.get('customer_id'))}"
        for row in simulator["customers"].values()
        if _text(row.get("name")) and customer_name_counts.get(_text(row.get("name"))) == 1
    }
    product_replacements = {
        _text(row.get("name")): f"产品编号{_text(row.get('product_id'))}"
        for row in simulator["products"].values() if _text(row.get("name"))
    }
    ambiguous_customer_names = {name for name, count in customer_name_counts.items() if count > 1}

    result: list[dict[str, Any]] = []
    for original in evaluation_cases():
        keys = set(original.get("expected_keys", []))
        has_source_gap = bool(keys & source_gaps)
        has_explicit_partial_fixture = bool(keys & partially_source_backed) and bool(
            re.search(r"\bZY-(?:C|P|V|MT)\d+\b", original.get("text", ""))
        )
        is_model_fallback = original.get("group") == "fallback"
        if not (has_source_gap or has_explicit_partial_fixture or is_model_fallback):
            continue

        case = deepcopy(original)
        text = case.get("text", "")
        if any(name in text for name in ambiguous_customer_names) and not re.search(r"\bZY-C\d+\b", text):
            continue
        for name, replacement in sorted(customer_replacements.items(), key=lambda item: len(item[0]), reverse=True):
            text = text.replace(name, replacement)
        for name, replacement in sorted(product_replacements.items(), key=lambda item: len(item[0]), reverse=True):
            text = text.replace(name, replacement)
        case["text"] = text
        case["id"] = "sim-fill-" + case["id"]
        case["data_origin"] = "project_simulation"
        fill_for = sorted(keys & source_gaps)
        case["simulator_fill_for"] = fill_for or [
            "partial_source_data" if has_explicit_partial_fixture else "model_fallback"
        ]
        result.append(case)
    return result


def hybrid_cases(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the frozen, quota-complete INT-035 lightweight evaluation set.

    Source cases are included once and remain source-labelled. Gaps are filled
    only from the project's existing synthetic catalog and fixed paraphrase
    forms. The returned order and IDs are deterministic for snapshot/code.
    """
    from copy import deepcopy

    from intent_platform import zhiyu
    from intent_platform.zhiyu.evaluation import evaluation_cases

    source = source_cases(snapshot)
    simulation = simulator_fill_cases(snapshot)
    baseline = evaluation_cases()
    for case in baseline:
        case.setdefault("data_origin", "project_simulation")

    def phrase_variants(text: str) -> list[str]:
        prefixes = (
            "", "请问，", "麻烦帮我", "我想了解一下：", "能否协助我",
            "辛苦查询一下：", "帮忙确认：", "请帮我查看", "我需要了解，", "请协助核实：",
        )
        return list(dict.fromkeys(prefix + text for prefix in prefixes))

    def copied(seed: dict[str, Any], case_id: str, text: str, group: str) -> dict[str, Any]:
        case = deepcopy(seed)
        case["id"] = case_id
        case["text"] = text
        case["group"] = group
        case["seed_case_id"] = seed["id"]
        return case

    packages = [item["key"] for item in zhiyu.function_packages()]
    if len(packages) != 26 or len(set(packages)) != 26:
        raise ValueError("INT-035 evaluation target requires exactly 26 registered functions")
    singles: list[dict[str, Any]] = []
    source_singles = [case for case in source if case.get("group") == "single"]
    sim_singles = [case for case in [*baseline, *simulation]
                   if case.get("group") == "single" and len(case.get("expected_keys", [])) == 1]
    for key in packages:
        source_seeds = [case for case in source_singles if case["expected_keys"] == [key]]
        sim_seeds = [case for case in sim_singles if case["expected_keys"] == [key]]
        selected: list[dict[str, Any]] = []
        seen_text: set[str] = set()
        for seed in source_seeds:
            if seed["text"] not in seen_text:
                selected.append(seed); seen_text.add(seed["text"])
        # Existing simulation scenarios supply distinct arguments and expected
        # behavior. Only after those are exhausted do safe utterance variants
        # fill the per-function quota.
        for seed in sim_seeds:
            if seed["text"] not in seen_text:
                selected.append(seed); seen_text.add(seed["text"])
        generated = 0
        while len(selected) < 10:
            seed = sim_seeds[generated % len(sim_seeds)] if sim_seeds else None
            if seed is None:
                raise ValueError(f"INT-035 has no simulator seed for {key}")
            variant_index = generated // len(sim_seeds) + 1
            variants = phrase_variants(seed["text"])
            text = variants[variant_index % len(variants)]
            generated += 1
            if text in seen_text:
                continue
            selected.append(copied(seed, f"variant:{key}:{generated}", text, "single"))
            selected[-1]["data_origin"] = "project_simulation"
            seen_text.add(text)
        for index, seed in enumerate(selected[:10], 1):
            case_id = seed["id"] if seed.get("data_origin") == "super_workbench_source" else f"s1-{key.removeprefix('zhiyu.')}-{index:02d}"
            singles.append(copied(seed, case_id, seed["text"], "single"))
            singles[-1]["data_origin"] = seed.get("data_origin", "project_simulation")
            singles[-1]["sample_dimension"] = "single_intent"

    # Boundary cases distinguish ambiguity, missing inputs, out-of-domain
    # fallback, unavailable business facts, and function-owned object policy.
    boundary: list[dict[str, Any]] = []
    ambiguity_seed = {
        "id": "boundary-seed-ambiguity", "group": "boundary", "data_origin": "project_simulation",
        "text": "查看远川制造客户资料", "persona_id": "manager_a",
        "expected_keys": [], "allowed_keys": ["zhiyu.customer.profile"],
        "expected_source": "business", "expected_status": "WAITING_INPUT",
        "assertions": {"business_calls": 0, "question_kind": "entity_choice", "option_count": 2},
        "allow_model_fallback": False, "boundary_kind": "ambiguity",
    }
    missing_seed = {
        "id": "boundary-seed-missing", "group": "boundary", "data_origin": "project_simulation",
        "text": "创建营销任务", "persona_id": "manager_a",
        "expected_keys": [], "allowed_keys": ["zhiyu.marketing.create"],
        "expected_source": "business", "expected_status": "WAITING_INPUT",
        "assertions": {"business_calls": 0}, "allow_model_fallback": False,
        "boundary_kind": "missing_required_input",
    }
    out_of_scope_seed = {
        "id": "boundary-seed-out-of-scope", "group": "boundary", "data_origin": "project_simulation",
        "text": "写一首关于秋天的短诗，不涉及银行业务", "persona_id": "manager_a",
        "expected_keys": [], "allowed_keys": [], "expected_source": "model",
        "expected_status": "SUCCEEDED", "assertions": {"business_calls": 0, "answer_source": "model"},
        "allow_model_fallback": True, "boundary_kind": "out_of_scope",
    }
    unknown_policy_seed = {
        "id": "boundary-seed-unknown-policy", "group": "boundary", "data_origin": "project_simulation",
        "text": "我行2027年航空航天授信具体准入阈值是多少", "persona_id": "manager_a",
        "expected_keys": ["zhiyu.policy.search"], "allowed_keys": ["zhiyu.policy.search"],
        "expected_source": "business", "expected_status": "SUCCEEDED",
        "assertions": {"business_calls": 1, "data.matched": False},
        "allow_model_fallback": False, "boundary_kind": "missing_business_fact",
    }
    boundary_seeds = [ambiguity_seed, missing_seed, out_of_scope_seed, unknown_policy_seed]
    for seed, quota in zip(boundary_seeds, (10, 10, 10, 9)):
        variants = phrase_variants(seed["text"])
        for index in range(quota):
            boundary.append(copied(seed, f"boundary-{seed['boundary_kind']}-{index + 1:02d}",
                                    variants[index], "boundary"))
    source_policy_boundary = next((case for case in source if case.get("boundary_kind") == "function_owned_object_authorization"), None)
    if source_policy_boundary:
        boundary.append(copied(source_policy_boundary, source_policy_boundary["id"], source_policy_boundary["text"], "boundary"))
        boundary[-1]["data_origin"] = "super_workbench_source"
        boundary[-1]["boundary_kind"] = "function_owned_object_authorization"
    else:
        fallback_unknown = copied(unknown_policy_seed, "boundary-function-delegated-object-access", unknown_policy_seed["text"], "boundary")
        fallback_unknown["boundary_kind"] = "missing_business_fact"
        boundary.append(fallback_unknown)

    # True multi-turn cases share one conversation and verify that each turn
    # reaches its own intended capability. Follow-up language references the
    # prior result instead of restating the full entity.
    flows = [
        ("customer-search-profile", "查一下我名下的代发高潜客户", "再看一下第一位客户的资料", "zhiyu.customer.search", "zhiyu.customer.profile"),
        ("product-search-detail", "查几款流动资金贷款产品", "继续看列表里第一款产品的详情", "zhiyu.product.search", "zhiyu.product.detail"),
        ("todo-list-plan", "查看我近期的待办事项", "请给第一项待办一条处理建议", "zhiyu.todo.list", "zhiyu.todo.plan"),
        ("customer-search-visits", "筛选我名下的代发高潜客户", "查看刚才第一位客户的拜访记录", "zhiyu.customer.search", "zhiyu.visit.list"),
        ("customer-search-cooperation", "列出我名下的客户", "再看第一位客户与本行的合作情况", "zhiyu.customer.search", "zhiyu.customer.cooperation"),
    ]
    multi_turn: list[dict[str, Any]] = []
    for flow_id, first, second, first_key, second_key in flows:
        for index, prefix in enumerate(("", "请帮我", "麻烦查询：", "我想先了解，", "请协助我", "辛苦看一下："), 1):
            multi_turn.append({
                "id": f"multi-turn-{flow_id}-{index:02d}", "group": "multi_turn",
                "data_origin": "project_simulation", "persona_id": "manager_a",
                "text": prefix + first, "expected_keys": [first_key, second_key],
                "allowed_keys": [first_key, second_key], "expected_source": "business",
                "expected_status": "SUCCEEDED", "allow_model_fallback": False,
                "turns": [{"text": prefix + first, "expected_status": "SUCCEEDED", "expected_keys": [first_key]},
                          {"text": prefix + second, "expected_status": "SUCCEEDED", "expected_keys": [second_key]}],
                "assertions": {"turn_count": 2}, "flow_id": flow_id,
            })

    skills_by_key: dict[str, list[dict[str, Any]]] = {}
    skill_seeds = [case for case in [*source, *baseline, *simulation] if case.get("group") == "compound"]
    for seed in skill_seeds:
        skill_key = seed.get("expected_skill_key")
        if skill_key:
            skills_by_key.setdefault(skill_key, []).append(seed)
    skills: list[dict[str, Any]] = []
    for skill in zhiyu.business_skill_templates():
        key = skill["key"]
        seeds = skills_by_key.get(key, [])
        if not seeds:
            raise ValueError(f"INT-035 has no fixed Skill case for {key}")
        selected = []
        seen: set[str] = set()
        for seed in seeds:
            if seed["text"] not in seen:
                selected.append(seed); seen.add(seed["text"])
        variant_index = 0
        while len(selected) < 5:
            seed = seeds[variant_index % len(seeds)]
            variants = phrase_variants(seed["text"])
            text = variants[(variant_index + 1) % len(variants)]
            variant_index += 1
            if text in seen:
                continue
            variant = copied(seed, f"variant:{key}:{variant_index}", text, "compound")
            variant["data_origin"] = "project_simulation"
            selected.append(variant); seen.add(text)
        for index, seed in enumerate(selected[:5], 1):
            case_id = seed["id"] if seed.get("data_origin") == "super_workbench_source" else f"skill-{key.removeprefix('zhiyu.skill.')}-{index:02d}"
            case = copied(seed, case_id, seed["text"], "compound")
            case["data_origin"] = seed.get("data_origin", "project_simulation")
            case["sample_dimension"] = "preset_skill"
            skills.append(case)

    autonomous_seeds = [case for case in [*source, *baseline, *simulation] if case.get("group") == "autonomous"]
    autonomous: list[dict[str, Any]] = []
    for kind, quota in (("single", 6), ("multi", 6)):
        candidates = [case for case in autonomous_seeds if case.get("kind") == kind]
        if kind == "single":
            candidates.extend(case for case in source if case.get("id") == "crm-autonomous-single-01")
        elif kind == "multi":
            candidates.extend(case for case in source if case.get("id") == "crm-autonomous-multi-01")
        if not candidates:
            raise ValueError(f"INT-035 has no {kind}-goal autonomous planning seed")
        unique = []
        seen = set()
        for case in candidates:
            marker = (case["text"], tuple(case.get("expected_keys", [])))
            if marker not in seen:
                unique.append(case); seen.add(marker)
        variant_index = 0
        while len(unique) < quota:
            seed = candidates[variant_index % len(candidates)]
            variants = phrase_variants(seed["text"])
            variant_index += 1
            text = variants[variant_index % len(variants)]
            marker = (text, tuple(seed.get("expected_keys", [])))
            if marker in seen:
                continue
            variant = copied(seed, f"variant:autonomous:{kind}:{variant_index}", text, "autonomous")
            variant["kind"] = kind
            variant["data_origin"] = "project_simulation"
            unique.append(variant); seen.add(marker)
        for index, seed in enumerate(unique[:quota], 1):
            case_id = seed["id"] if seed.get("data_origin") == "super_workbench_source" else f"autonomous-{kind}-{index:02d}"
            case = copied(seed, case_id, seed["text"], "autonomous")
            case["kind"] = kind
            case["data_origin"] = seed.get("data_origin", "project_simulation")
            autonomous.append(case)

    fallback_seeds = [case for case in baseline if case.get("group") == "fallback"]
    # Identical prompts can represent distinct reliability assertions, such as
    # definite failure versus timeout/unknown. Keep each frozen case by ID.
    fallback: list[dict[str, Any]] = []
    for seed in fallback_seeds:
        fallback.append(copied(seed, seed["id"], seed["text"], "fallback"))
        fallback[-1]["data_origin"] = seed.get("data_origin", "project_simulation")
    if len(fallback) != 12:
        raise ValueError(f"INT-035 expected 12 model-fallback cases, got {len(fallback)}")

    cases = [*singles, *boundary, *multi_turn, *skills, *autonomous, *fallback]
    expected_groups = {"single": 260, "boundary": 40, "multi_turn": 30, "compound": 30, "autonomous": 12, "fallback": 12}
    counts: dict[str, int] = {}
    for case in cases:
        counts[case["group"]] = counts.get(case["group"], 0) + 1
    if counts != expected_groups or len(cases) != 384 or len({case["id"] for case in cases}) != 384:
        raise ValueError(f"INT-035 frozen quotas invalid: {counts}")
    if any(sum(case["group"] == "single" and case["expected_keys"] == [key] for case in cases) != 10 for key in packages):
        raise ValueError("INT-035 single-intent coverage is not exactly 10 cases per function")
    return cases


def processor_preflight(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Check source replay results against the current output contracts offline."""
    import re

    from intent_platform import zhiyu
    from intent_platform.zhiyu import engine
    from intent_platform.ontology import input_errors
    from intent_platform.output_contract import project_output

    state = source_state(snapshot, zhiyu.initial_state())
    source_ids = state["_int035_source_key_map"]
    cases = {case["id"]: case for case in source_cases(snapshot)}
    definitions = {item["key"]: item["definition"] for item in zhiyu.function_packages()}
    check_ids: list[str] = []

    def path_value(value: Any, path: str) -> Any:
        for part in path.split("."):
            if part == "length":
                value = len(value)
            elif isinstance(value, list):
                value = value[int(part)]
            else:
                value = value[part]
        return value

    def call(check_id: str, capability: str, arguments: dict[str, Any], *, persona_id: str = "supervisor",
             manager_id: str | None = None, authorization_mode: str = "function") -> dict[str, Any]:
        previous_manager = engine._PERSONAS.get(persona_id, {}).get("manager_id")
        if persona_id == "manager_a" and manager_id:
            engine._PERSONAS[persona_id]["manager_id"] = manager_id
        try:
            envelope, _ = zhiyu.execute(capability, arguments, state, "int035-preflight-" + check_id,
                                        persona_id=persona_id, authorization_mode=authorization_mode)
        finally:
            if persona_id == "manager_a" and manager_id:
                engine._PERSONAS[persona_id]["manager_id"] = previous_manager
        if envelope.get("status") != "SUCCEEDED":
            raise ValueError(f"INT-035 processor preflight failed: {check_id}")
        data = envelope.get("data") or {}
        errors = input_errors(definitions[capability]["output_schema"], data)
        if errors:
            raise ValueError(f"INT-035 output contract preflight failed: {check_id} ({len(errors)} fields)")
        try:
            project_output(definitions[capability], data)
        except (ValueError, TypeError, KeyError):
            raise ValueError(f"INT-035 output projection preflight failed: {check_id}") from None
        check_ids.append(check_id)
        return data

    # Recompute exact metrics from source-aligned rows; never accept zero-filled gaps.
    for case in cases.values():
        if case["id"].startswith("crm-profile-"):
            runtime_customer_id = case["assertions"]["data.customer_id"]
            customer_id = case["source_customer_id"]
            customer = next(row for row in snapshot.get("customers", []) if _text(row.get("customer_id")) == customer_id)
            data = call(case["id"], "zhiyu.customer.profile", {"customer_id": runtime_customer_id},
                        persona_id="manager_a", manager_id=case.get("eval_persona_manager_id"))
            for field in ("customer_id", "deposit", "credit_used", "payroll_count"):
                if data[field] != case["assertions"][f"data.{field}"]:
                    raise ValueError(f"INT-035 source gold mismatch: {case['id']} ({field})")

    search_case = cases.get("crm-customer-search-01")
    if search_case:
        match = re.search(r"名为(.+)的客户", search_case["text"])
        if not match:
            raise ValueError("INT-035 customer-search preflight has no frozen entity")
        data = call(search_case["id"], "zhiyu.customer.search", {"query": match.group(1)},
                    persona_id="manager_a", manager_id=search_case.get("eval_persona_manager_id"))
        if data["customers"][0]["customer_id"] != search_case["assertions"]["data.customers.0.customer_id"]:
            raise ValueError("INT-035 customer-search gold mismatch")

    for case_id, capability, id_path in (
        ("crm-product-search-01", "zhiyu.product.search", "data.products.0.product_id"),
        ("crm-product-detail-01", "zhiyu.product.detail", "data.product_id"),
    ):
        case = cases.get(case_id)
        if not case:
            continue
        product_id = case["assertions"][id_path]
        source_id = case.get("source_product_id")
        source_row = next(row for row in snapshot.get("products", [])
                          if _text(row.get("product_id")) == source_id)
        if capability.endswith("product.search"):
            result = call(case_id, capability, {"query": _text(source_row.get("name"))})
            if not any(row.get("product_id") == product_id for row in result["products"]):
                raise ValueError("INT-035 product-search gold mismatch")
        else:
            result = call(case_id, capability, {"product_id": product_id})
            if result.get("product_id") != product_id:
                raise ValueError("INT-035 product-detail gold mismatch")

    segment_case = cases.get("crm-segment-list-01")
    if segment_case:
        match = re.search(r'查看客群包“(.+)”及客户数量', segment_case["text"])
        if not match:
            raise ValueError("INT-035 segment preflight has no frozen entity")
        data = call(segment_case["id"], "zhiyu.segment.list", {"query": match.group(1)})
        for key, expected in segment_case["assertions"].items():
            if key.startswith("data.") and path_value(data, key[5:]) != expected:
                raise ValueError("INT-035 segment gold mismatch")

    for case_id, suffix, capability, argument_name, assertion_path in (
        ("crm-marketing-list-01", "关联的营销任务。", "zhiyu.marketing.list", "customer_id", "data.tasks.length"),
        ("crm-visit-list-01", "最近的拜访记录。", "zhiyu.visit.list", "customer_id", "data.visits.0.visit_id"),
    ):
        case = cases.get(case_id)
        if not case:
            continue
        source_customer_id = case.get("source_customer_id")
        if not source_customer_id:
            raise ValueError(f"INT-035 {case_id} source entity is not frozen")
        customer = next((row for row in snapshot.get("customers", [])
                         if _text(row.get("customer_id")) == source_customer_id), None)
        if not customer:
            raise ValueError(f"INT-035 {case_id} source customer alias is missing")
        data = call(case_id, capability,
                    {argument_name: source_ids["customers"][_text(customer.get("customer_id"))]},
                    persona_id="manager_a", manager_id=case.get("eval_persona_manager_id"))
        expected = case["assertions"][assertion_path]
        if path_value(data, assertion_path[5:]) != expected:
            raise ValueError(f"INT-035 {case_id} source gold mismatch")

    comparison = cases.get("crm-skill-product-comparison-01")
    if comparison:
        for index, product_id in enumerate(comparison["assertions"]["product_ids"], 1):
            data = call(f"comparison-product-{index}", "zhiyu.product.detail", {"product_id": product_id})
            if data.get("product_id") != product_id:
                raise ValueError("INT-035 comparison product gold mismatch")

    function_owned = cases.get("crm-permission-cross-manager-01")
    if function_owned:
        aliases = [row for row in snapshot.get("customers", [])
                   if _text(row.get("customer_id")) == function_owned.get("source_customer_id")]
        manager_id = function_owned.get("eval_persona_manager_id")
        selected_customer = next((row for row in aliases
                                if source_ids["managers"].get(_text(row.get("manager_id"))) != manager_id), None)
        if not manager_id or not selected_customer:
            raise ValueError("INT-035 function-owned authorization preflight lacks distinct source ownership")
        result = call(function_owned["id"], "zhiyu.customer.search", {
            "query": _text(selected_customer.get("name"))},
            persona_id="manager_a", manager_id=manager_id, authorization_mode="function")
        if result.get("customers"):
            raise ValueError("Function-owned object policy returned another manager's customer")

    return {"status": "passed", "checks": len(check_ids), "check_ids": check_ids,
            "source_case_count": len(cases), "simulator_fill_case_count": len(simulator_fill_cases(snapshot)),
            "production_origin_verified": False, "external_model_calls": 0}
