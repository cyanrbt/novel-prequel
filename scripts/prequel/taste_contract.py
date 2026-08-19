from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError


CONTRACT_SCHEMA = "novel-user-taste-contract"
REQUIRED_ROOT_FIELDS = {
    "schema",
    "version",
    "updated_at",
    "target_experience",
    "hard_constraints",
    "rejected_patterns",
    "deterministic_checks",
    "review_policy",
}
REQUIRED_CONSTRAINT_FIELDS = {"id", "category", "rule"}


def validate_taste_contract(contract: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(contract, dict):
        return ["偏好契约根节点必须是object"]
    missing = REQUIRED_ROOT_FIELDS - contract.keys()
    if missing:
        errors.append(f"偏好契约缺失字段: {sorted(missing)}")
    if contract.get("schema") != CONTRACT_SCHEMA:
        errors.append(f"偏好契约schema必须为{CONTRACT_SCHEMA}")
    if not isinstance(contract.get("version"), int) or contract.get("version", 0) < 1:
        errors.append("偏好契约version必须是正整数")
    constraints = contract.get("hard_constraints")
    if not isinstance(constraints, list) or not constraints:
        errors.append("偏好契约hard_constraints必须是非空数组")
    else:
        ids: list[str] = []
        for item in constraints:
            if not isinstance(item, dict) or set(item) != REQUIRED_CONSTRAINT_FIELDS:
                errors.append(f"偏好硬约束结构无效: {item!r}")
                continue
            if any(not isinstance(item[key], str) or not item[key].strip() for key in REQUIRED_CONSTRAINT_FIELDS):
                errors.append(f"偏好硬约束字段不得为空: {item!r}")
            ids.append(item.get("id", ""))
        if len(ids) != len(set(ids)):
            errors.append("偏好硬约束id不得重复")
    rejected = contract.get("rejected_patterns")
    if not isinstance(rejected, list) or not all(
        isinstance(item, str) and item.strip() for item in rejected
    ):
        errors.append("rejected_patterns必须是非空字符串数组")
    checks = contract.get("deterministic_checks")
    if not isinstance(checks, dict):
        errors.append("deterministic_checks必须是object")
    else:
        for field in (
            "forbidden_tokens",
            "forbidden_address_tokens",
            "forbidden_pov_phrases",
        ):
            value = checks.get(field)
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                errors.append(f"deterministic_checks.{field}必须是字符串数组")
        staccato = checks.get("warn_staccato_run")
        if isinstance(staccato, bool) or not isinstance(staccato, int) or staccato < 2:
            errors.append("deterministic_checks.warn_staccato_run必须是至少2的整数")
        if not isinstance(checks.get("check_obstructed_identification"), bool):
            errors.append("deterministic_checks.check_obstructed_identification必须是布尔值")
    if not isinstance(contract.get("review_policy"), dict):
        errors.append("review_policy必须是object")
    return errors


def load_taste_contract(project_root: Path) -> dict[str, Any]:
    path = project_root / "novel/style/user_taste_contract.json"
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"无法读取用户偏好契约: {exc}") from exc
    errors = validate_taste_contract(contract)
    if errors:
        raise ArtifactValidationError("用户偏好契约无效: " + "；".join(errors))
    return contract


def taste_contract_sha256(contract: dict[str, Any]) -> str:
    encoded = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
