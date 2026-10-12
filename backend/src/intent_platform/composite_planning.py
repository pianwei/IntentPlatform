"""Expand explicitly selected one-level functions into bounded immutable steps."""
from copy import deepcopy


def expand(spec, projection):
    composites = projection.get("composites", {})
    result, outputs, expanded_ids, groups = [], {}, {}, []
    from .planning import visible_results
    visible = visible_results(spec)

    def reference(value):
        value = deepcopy(value)
        if not isinstance(value, dict) or "$step" not in value:
            return value
        sid, path = value["$step"], value["path"]
        if sid not in outputs:
            return value
        parts = path.split(".")
        if len(parts) < 2 or parts[1] not in outputs[sid]:
            raise ValueError("Composite reference must select a mapped public field")
        target = outputs[sid][parts[1]]
        return {"$step": target["$step"], "path": target["path"] + ("." + ".".join(parts[2:]) if len(parts) > 2 else "")}

    def translated(step):
        step = deepcopy(step)
        step["arguments"] = {k: reference(v) for k, v in step["arguments"].items()}
        step["depends_on"] = list(dict.fromkeys(x for d in step.get("depends_on", []) for x in expanded_ids.get(d, [d])))
        for key in ("when", "foreach"):
            if step.get(key):
                ref = reference({"$step": step[key]["step"], "path": step[key]["path"]})
                step[key].update(step=ref["$step"], path=ref["path"])
        return step

    for index, original in enumerate(spec["steps"]):
        outer = translated(original)
        comp = composites.get(outer["capability_id"])
        if not comp:
            result.append(outer)
            expanded_ids[outer["step_id"]] = [outer["step_id"]]
            continue
        if outer.get("foreach") or outer.get("when"):
            raise ValueError("Batch/conditional wrappers are unsupported; compose bounded rule steps instead")
        prefix = "c" + str(index) + "_"
        ids = {s["step_id"]: prefix + s["step_id"] for s in comp["plan"]["steps"]}
        if any(len(v) > 40 for v in ids.values()):
            raise ValueError("Composite expanded step identifier exceeds limit")
        for source in comp["plan"]["steps"]:
            step = deepcopy(source)
            step["step_id"] = ids[step["step_id"]]
            step["depends_on"] = list(dict.fromkeys(outer["depends_on"] + [ids[d] for d in step["depends_on"]]))
            args = {}
            step["parameter_units"] = dict(step.get("parameter_units", {}))
            for field, value in step["arguments"].items():
                if isinstance(value, dict) and "$input" in value:
                    if value["$input"] not in outer["arguments"]:
                        raise ValueError("Composite input is missing")
                    args[field] = deepcopy(outer["arguments"][value["$input"]])
                    if value["$input"] in outer.get("parameter_units", {}):
                        step["parameter_units"][field] = outer["parameter_units"][value["$input"]]
                elif isinstance(value, dict) and "$step" in value:
                    args[field] = {**value, "$step": ids[value["$step"]]}
                else:
                    args[field] = value
            step["arguments"] = args
            for key in ("when", "foreach"):
                if step.get(key): step[key]["step"] = ids[step[key]["step"]]
            result.append(step)
        expanded_ids[outer["step_id"]] = list(ids.values())
        outputs[outer["step_id"]] = {field: {**ref, "$step": ids[ref["$step"]]} for field, ref in comp["final_output_mapping"].items()}
        groups.append({"function_id": outer["capability_id"], "step_ids": list(ids.values()),
                       "visible": original["step_id"] in visible, "mapping": outputs[outer["step_id"]]})
    return {"inputs": spec["inputs"], "steps": result,
            "result_steps": [sid for original in spec["result_steps"] for sid in expanded_ids[original]] if spec.get("result_steps") is not None else None}, groups
