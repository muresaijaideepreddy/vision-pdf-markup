"""Provider-independent edit format and strict local response validation."""

from __future__ import annotations

import re

EDIT_PROPERTIES = {
    "id": {"type": "string"},
    "block_id": {"type": "string"},
    "operation": {"type": "string", "enum": ["replace", "delete", "insert"]},
    "old_text": {"type": "string"},
    "new_text": {"type": "string"},
    "context_before": {"type": "string"},
    "context_after": {"type": "string"},
    "page": {"type": "integer", "minimum": 1},
    "reason": {"type": "string"},
    "needs_review": {"type": "boolean"},
}
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": EDIT_PROPERTIES,
                "required": list(EDIT_PROPERTIES),
                "additionalProperties": False,
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["edits", "warnings"],
    "additionalProperties": False,
}


def validate_response(value: object, page_count: int | None = None) -> dict:
    if not isinstance(value, dict) or set(value) != {"edits", "warnings"}:
        raise ValueError("Model response must contain exactly edits and warnings.")
    if not isinstance(value["warnings"], list) or any(
        not isinstance(x, str) for x in value["warnings"]
    ):
        raise ValueError("warnings must be a list of strings.")
    if not isinstance(value["edits"], list) or len(value["edits"]) > 2000:
        raise ValueError("edits must be a list of at most 2000 changes.")
    seen = set()
    for edit in value["edits"]:
        if not isinstance(edit, dict) or set(edit) != set(EDIT_PROPERTIES):
            raise ValueError("An edit has missing or unexpected fields.")
        for key in EDIT_PROPERTIES:
            expected = bool if key == "needs_review" else int if key == "page" else str
            if type(edit[key]) is not expected:
                raise ValueError(f"Edit field {key} must be {expected.__name__}.")
        if not edit["id"] or edit["id"] in seen:
            raise ValueError("Every edit needs a unique, nonempty id.")
        seen.add(edit["id"])
        if edit["page"] < 1 or (page_count is not None and edit["page"] > page_count):
            raise ValueError(f"Edit {edit['id']} has an invalid PDF page number.")
        if edit["operation"] not in {"replace", "delete", "insert"}:
            raise ValueError(f"Edit {edit['id']} has an unsupported operation.")
        if not edit["block_id"] and not edit["needs_review"]:
            raise ValueError("Unlocated marks must have needs_review=true.")
    return value


def validate_plan(value: object) -> dict:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Only edit plan schema_version 1 is supported.")
    if not isinstance(value.get("source_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["source_sha256"]):
        raise ValueError("Edit plan needs a valid source_sha256 fingerprint.")
    validate_response({"edits": value.get("edits"), "warnings": value.get("warnings")})
    return value
