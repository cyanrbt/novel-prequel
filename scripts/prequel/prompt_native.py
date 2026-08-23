from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .run_manifest import fingerprint


EXECUTION_KEYS = {"provider", "model_profiles", "stage_routes"}
REQUIRED_PROTOCOL_FILES = (
    "WORKFLOW.md",
    "workflows/status-check.md",
    "workflows/next-chapter.md",
    "workflows/accept-candidate.md",
    "workflows/protocol-smoke-test.md",
    "schemas/task_envelope.schema.json",
    "schemas/agent_result.schema.json",
    "tests/fixtures/prompt_native_task.json",
    "tests/fixtures/prompt_native_result.json",
    "config/execution.example.json",
)
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"通用工作流 JSON 无效 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"通用工作流 JSON 根节点必须是object: {path}")
    return value


def _require_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields - set(value))
    if missing:
        raise ArtifactValidationError(f"{label}缺少字段: {', '.join(missing)}")


def validate_task_envelope(project_root: Path, task: dict[str, Any]) -> list[str]:
    _require_fields(
        task,
        {
            "protocol",
            "task_id",
            "stage",
            "role_file",
            "instruction",
            "inputs",
            "input_fingerprint",
            "output_contract",
            "capabilities",
        },
        "任务信封",
    )
    if task["protocol"] != "prequel-task/1":
        raise ArtifactValidationError("任务信封协议必须是 prequel-task/1")
    for field in ("task_id", "stage", "instruction"):
        if not isinstance(task[field], str) or not task[field].strip():
            raise ArtifactValidationError(f"任务信封 {field} 必须是非空字符串")
    if not isinstance(task["inputs"], dict):
        raise ArtifactValidationError("任务信封 inputs 必须是object")
    expected_fingerprint = fingerprint(task["inputs"])
    if task["input_fingerprint"] != expected_fingerprint:
        raise ArtifactValidationError(
            "任务信封 input_fingerprint 与 inputs 不一致"
        )
    role_file = task["role_file"]
    if not isinstance(role_file, str) or not role_file.startswith("agents/"):
        raise ArtifactValidationError("任务信封 role_file 必须位于 agents/")
    if not (project_root / role_file).is_file():
        raise ArtifactValidationError(f"任务角色文件不存在: {role_file}")
    contract = task["output_contract"]
    if not isinstance(contract, dict) or contract.get("format") not in {"text", "json"}:
        raise ArtifactValidationError("任务输出格式必须是 text 或 json")
    schema = contract.get("schema")
    if contract.get("format") == "json":
        if not isinstance(schema, str) or not (project_root / schema).is_file():
            raise ArtifactValidationError("JSON任务必须引用存在的输出 Schema")
    elif schema is not None:
        raise ArtifactValidationError("text任务的输出 Schema 必须为null")
    capabilities = task["capabilities"]
    if not isinstance(capabilities, dict):
        raise ArtifactValidationError("任务 capabilities 必须是object")
    _require_fields(
        capabilities,
        {"filesystem", "subagents", "structured_output"},
        "任务能力声明",
    )
    return [
        f"task envelope validated: {task['task_id']}",
        f"role file exists: {role_file}",
        f"input fingerprint validated: {expected_fingerprint}",
    ]


def validate_agent_result(task: dict[str, Any], result: dict[str, Any]) -> list[str]:
    _require_fields(
        result,
        {"protocol", "task_id", "status", "input_fingerprint", "artifact", "error"},
        "Agent结果",
    )
    if result["protocol"] != "prequel-result/1":
        raise ArtifactValidationError("Agent结果协议必须是 prequel-result/1")
    if result["task_id"] != task["task_id"]:
        raise ArtifactValidationError("Agent结果 task_id 与任务不一致")
    if result["input_fingerprint"] != task["input_fingerprint"]:
        raise ArtifactValidationError("Agent结果引用了不同的输入指纹")
    status = result["status"]
    if status not in {"COMPLETED", "BLOCKED", "FAILED"}:
        raise ArtifactValidationError("Agent结果状态无效")
    error = result["error"]
    if status == "COMPLETED" and error is not None:
        raise ArtifactValidationError("COMPLETED结果不得包含错误")
    if status != "COMPLETED" and (not isinstance(error, str) or not error.strip()):
        raise ArtifactValidationError("BLOCKED或FAILED结果必须包含错误说明")
    if not HEX_64.fullmatch(str(result["input_fingerprint"])):
        raise ArtifactValidationError("Agent结果输入指纹格式无效")
    return [
        f"agent result validated: {result['task_id']}",
        f"task/result binding validated: {result['input_fingerprint']}",
    ]


def validate_prompt_native_project(project_root: Path) -> list[str]:
    root = project_root.resolve()
    missing = [path for path in REQUIRED_PROTOCOL_FILES if not (root / path).is_file()]
    if missing:
        raise ArtifactValidationError(
            "通用工作流缺少文件: " + ", ".join(missing)
        )

    core_config = _read_object(root / "config/prequel_config.json")
    coupled = sorted(EXECUTION_KEYS & set(core_config))
    if coupled:
        raise ArtifactValidationError(
            "核心创作配置仍包含执行后端字段: " + ", ".join(coupled)
        )

    execution_example = _read_object(root / "config/execution.example.json")
    missing_execution = sorted(EXECUTION_KEYS - set(execution_example))
    if missing_execution:
        raise ArtifactValidationError(
            "执行后端示例缺少字段: " + ", ".join(missing_execution)
        )

    for schema_path in (
        "schemas/task_envelope.schema.json",
        "schemas/agent_result.schema.json",
    ):
        schema = _read_object(root / schema_path)
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ArtifactValidationError(f"协议 Schema 版本无效: {schema_path}")

    task = _read_object(root / "tests/fixtures/prompt_native_task.json")
    result = _read_object(root / "tests/fixtures/prompt_native_result.json")
    checks = [
        "prompt-native workflow files exist",
        "core story config is execution-backend agnostic",
        "optional execution backend example validated",
        "protocol schemas loaded",
    ]
    checks.extend(validate_task_envelope(root, task))
    checks.extend(validate_agent_result(task, result))
    return checks
