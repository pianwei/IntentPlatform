"""Reviewed Markdown authority for the six synthetic composite goals."""
from copy import deepcopy
from pathlib import Path
from .skills import business_skill_templates, composite_definition

TEST_INPUTS = {
    "product_comparison": {"product_a": "ZY-P001", "product_b": "ZY-P002"},
    "visit_preparation": {"customer_id": "ZY-C001"},
    "visit_roleplay": {"customer_id": "ZY-C001"},
    "marketing_creation": {"customer_id": "ZY-C001", "title": "拜访跟进", "due_on": "2026-10-20"},
    "diligence_report": {"customer_id": "ZY-C001"},
    "postloan_overview": {"customer_id": "ZY-C001"},
}


def business_skill_documents():
    result = []
    for template in business_skill_templates():
        suffix = template["key"].rsplit(".", 1)[-1]
        value = {k: deepcopy(v) for k, v in template.items() if k not in {"steps", "version"}}
        value.update(mode="document", steps=[], document=(Path(__file__).parent / "skill_documents" / (suffix + ".md")).read_text(encoding="utf-8"))
        result.append(value)
    return result


def document_composite_definition(skill, skill_version_id, dependencies, domain_ref):
    template = next(s for s in business_skill_templates() if s["key"] == skill["key"])
    value = composite_definition(template, skill_version_id, dependencies, domain_ref)
    value["authoring_version"] = "function-v2"
    value["composite"].update(mode="document", final_output_mapping={})
    return value
