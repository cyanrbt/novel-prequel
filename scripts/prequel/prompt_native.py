from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .project import load_project_spec, role_paths
from .run_manifest import fingerprint


EXECUTION_KEYS = {"provider", "model_profiles", "stage_routes"}
REQUIRED_PROTOCOL_FILES = (
    "WORKFLOW.md",
    "workflows/status-check.md",
    "workflows/style-calibration.md",
    "workflows/scene-generation-experiment.md",
    "workflows/next-chapter.md",
    "workflows/accept-candidate.md",
    "workflows/protocol-smoke-test.md",
    "schemas/task_envelope.schema.json",
    "schemas/agent_result.schema.json",
    "schemas/protocol_smoke_artifact.schema.json",
    "schemas/style_comparison.schema.json",
    "schemas/scene_experiment.schema.json",
    "schemas/contract_scene_plan.schema.json",
    "schemas/character_intention.schema.json",
    "schemas/world_resolution.schema.json",
    "schemas/pov_event_trace.schema.json",
    "schemas/rolling_horizon.schema.json",
    "schemas/scene_experiment_comparison.schema.json",
    "agents/prose_director.md",
    "agents/reference_style_reviewer.md",
    "agents/scene_contract_planner.md",
    "agents/scene_contract_writer.md",
    "agents/character_actor.md",
    "agents/world_resolver.md",
    "agents/rolling_scene_planner.md",
    "agents/event_renderer.md",
    "agents/scene_experiment_reader.md",
    "tests/fixtures/prompt_native_task.json",
    "tests/fixtures/prompt_native_result.json",
)
RETIRED_EXECUTION_FILES = (
    "config/execution.example.json",
    "config/prequel_config.json",
    "scripts/benchmark_pipeline.py",
    "scripts/prequel/cli_capabilities.py",
    "scripts/prequel/audit_manifest.py",
    "scripts/prequel/call_budget.py",
    "scripts/prequel/evolution.py",
    "scripts/prequel/metrics.py",
    "scripts/prequel/model_calls.py",
    "scripts/prequel/model_router.py",
    "scripts/prequel/progress.py",
    "scripts/prequel/provider.py",
    "scripts/scene_generation_experiment.py",
    "scripts/provider_style_benchmark.py",
    "scripts/provider_style_benchmark_supplement.py",
)
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_SCHEMA_KEYS = {
    "$schema",
    "$id",
    "$defs",
    "$ref",
    "type",
    "const",
    "enum",
    "allOf",
    "anyOf",
    "oneOf",
    "if",
    "then",
    "else",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "title",
    "description",
    "default",
    "examples",
}


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"通用工作流 JSON 无效 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"通用工作流 JSON 根节点必须是object: {path}")
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _assert_supported_schema(schema: Any, path: str = "$schema") -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ArtifactValidationError(f"Schema 节点必须是 object 或 boolean: {path}")
    unsupported = set(schema) - SUPPORTED_SCHEMA_KEYS
    if unsupported:
        raise ArtifactValidationError(
            f"Schema 使用了未支持且不能安全忽略的关键字 {path}: "
            + ", ".join(sorted(unsupported))
        )
    for key in ("properties", "$defs"):
        mapping = schema.get(key, {})
        if not isinstance(mapping, dict):
            raise ArtifactValidationError(f"Schema {path}.{key} 必须是 object")
        for name, child in mapping.items():
            _assert_supported_schema(child, f"{path}.{key}.{name}")
    for key in ("allOf", "anyOf", "oneOf"):
        if key not in schema:
            continue
        branches = schema[key]
        if not isinstance(branches, list) or not branches:
            raise ArtifactValidationError(f"Schema {path}.{key} 必须是非空数组")
        for index, child in enumerate(branches):
            _assert_supported_schema(child, f"{path}.{key}[{index}]")
    for key in ("items", "additionalProperties", "if", "then", "else"):
        if key in schema:
            _assert_supported_schema(schema[key], f"{path}.{key}")
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list)
        or not all(isinstance(field, str) for field in required)
    ):
        raise ArtifactValidationError(f"Schema {path}.required 必须是字符串数组")
    enum = schema.get("enum")
    if enum is not None and not isinstance(enum, list):
        raise ArtifactValidationError(f"Schema {path}.enum 必须是数组")


def _resolve_local_ref(root_schema: dict[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ArtifactValidationError(f"只支持当前文件内的 Schema 引用: {reference}")
    value: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise ArtifactValidationError(f"Schema 引用不存在: {reference}")
        value = value[part]
    return value


def _schema_errors(
    value: Any,
    schema: Any,
    root_schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    if schema is True:
        return []
    if schema is False:
        return [f"{path} 被 Schema 拒绝"]
    if not isinstance(schema, dict):
        raise ArtifactValidationError(f"Schema 节点必须是 object 或 boolean: {path}")

    errors: list[str] = []
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str):
            raise ArtifactValidationError(f"Schema $ref 必须是字符串: {path}")
        errors.extend(
            _schema_errors(value, _resolve_local_ref(root_schema, reference), root_schema, path)
        )

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} 必须等于 {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} 不在允许值中")

    expected_types = schema.get("type")
    if expected_types is not None:
        types = [expected_types] if isinstance(expected_types, str) else expected_types
        if not isinstance(types, list) or not all(isinstance(item, str) for item in types):
            raise ArtifactValidationError(f"Schema type 无效: {path}")
        if not any(_matches_type(value, item) for item in types):
            errors.append(f"{path} 类型必须是 {'/'.join(types)}")
            return errors

    for branch in schema.get("allOf", []):
        errors.extend(_schema_errors(value, branch, root_schema, path))
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and not any(
        not _schema_errors(value, branch, root_schema, path) for branch in any_of
    ):
        errors.append(f"{path} 不满足 anyOf 中的任何分支")
    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = sum(
            not _schema_errors(value, branch, root_schema, path) for branch in one_of
        )
        if matches != 1:
            errors.append(f"{path} 必须且只能满足 oneOf 中的一个分支")
    condition = schema.get("if")
    if isinstance(condition, dict):
        branch_name = "then" if not _schema_errors(value, condition, root_schema, path) else "else"
        branch = schema.get(branch_name)
        if branch is not None:
            errors.extend(_schema_errors(value, branch, root_schema, path))

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for field in required:
                if field not in value:
                    errors.append(f"{path} 缺少字段 {field}")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for field, child_schema in properties.items():
                if field in value:
                    errors.extend(
                        _schema_errors(
                            value[field], child_schema, root_schema, f"{path}.{field}"
                        )
                    )
            extras = set(value) - set(properties)
            additional = schema.get("additionalProperties", True)
            if additional is False and extras:
                errors.append(f"{path} 包含未声明字段: {', '.join(sorted(extras))}")
            elif isinstance(additional, dict):
                for field in sorted(extras):
                    errors.extend(
                        _schema_errors(
                            value[field], additional, root_schema, f"{path}.{field}"
                        )
                    )

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path} 至少需要 {minimum} 项")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path} 最多允许 {maximum} 项")
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if any(item == previous for previous in value[:index]):
                    errors.append(f"{path} 不允许重复项")
                    break
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                errors.extend(
                    _schema_errors(item, item_schema, root_schema, f"{path}[{index}]")
                )

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path} 长度不得小于 {minimum}")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path} 长度不得大于 {maximum}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matched = re.search(pattern, value)
            except re.error as exc:
                raise ArtifactValidationError(f"Schema 正则无效 {path}: {exc}") from exc
            if matched is None:
                errors.append(f"{path} 不符合格式 {pattern}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path} 不得小于 {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} 不得大于 {maximum}")
    return errors


def _project_file(
    project_root: Path,
    relative: str,
    *,
    directory: str,
    suffix: str,
    label: str,
) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ArtifactValidationError(f"{label}路径必须是非空字符串")
    root = project_root.resolve()
    boundary = (root / directory).resolve()
    requested = Path(relative)
    if requested.is_absolute():
        raise ArtifactValidationError(f"{label}必须使用仓库内相对路径")
    path = (root / requested).resolve()
    if not path.is_relative_to(boundary) or path.suffix != suffix:
        raise ArtifactValidationError(f"{label}必须位于 {directory}/ 且后缀为 {suffix}")
    if not path.is_file():
        raise ArtifactValidationError(f"{label}不存在: {relative}")
    return path


def validate_schema_instance(
    project_root: Path,
    schema_path: str,
    value: Any,
    *,
    label: str,
) -> list[str]:
    path = _project_file(
        project_root,
        schema_path,
        directory="schemas",
        suffix=".json",
        label="输出 Schema",
    )
    schema = _read_object(path)
    _assert_supported_schema(schema)
    errors = _schema_errors(value, schema, schema)
    if errors:
        raise ArtifactValidationError(f"{label}不符合 {schema_path}: " + "；".join(errors[:8]))
    return [f"{label} schema validated: {schema_path}"]


def validate_task_envelope(project_root: Path, task: dict[str, Any]) -> list[str]:
    checks = validate_schema_instance(
        project_root,
        "schemas/task_envelope.schema.json",
        task,
        label="任务信封",
    )
    expected_fingerprint = fingerprint(task["inputs"])
    if task["input_fingerprint"] != expected_fingerprint:
        raise ArtifactValidationError(
            "任务信封 input_fingerprint 与 inputs 不一致"
        )
    role_file = task["role_file"]
    for index, declared_role in enumerate(
        [role_file, *task.get("role_overlays", [])]
    ):
        _project_file(
            project_root,
            declared_role,
            directory=".",
            suffix=".md",
            label="任务角色文件" if index == 0 else "任务角色覆盖文件",
        )
    contract = task["output_contract"]
    schema = contract.get("schema")
    if contract.get("format") == "json":
        _project_file(
            project_root,
            schema,
            directory="schemas",
            suffix=".json",
            label="输出 Schema",
        )
    elif schema is not None:
        raise ArtifactValidationError("text任务的输出 Schema 必须为null")
    return checks + [
        f"task envelope validated: {task['task_id']}",
        f"role file exists: {role_file}",
        f"input fingerprint validated: {expected_fingerprint}",
    ]


def validate_agent_result(
    task: dict[str, Any],
    result: dict[str, Any],
    project_root: Path | None = None,
) -> list[str]:
    root = (project_root or Path.cwd()).resolve()
    validate_task_envelope(root, task)
    checks = validate_schema_instance(
        root,
        "schemas/agent_result.schema.json",
        result,
        label="Agent结果信封",
    )
    if result["task_id"] != task["task_id"]:
        raise ArtifactValidationError("Agent结果 task_id 与任务不一致")
    expected_protocol = {
        "creative-task/1": "creative-result/1",
        "prequel-task/1": "prequel-result/1",
    }.get(task["protocol"])
    if result["protocol"] != expected_protocol:
        raise ArtifactValidationError("Agent结果协议与任务协议不匹配")
    if result["input_fingerprint"] != task["input_fingerprint"]:
        raise ArtifactValidationError("Agent结果引用了不同的输入指纹")
    if not HEX_64.fullmatch(str(result["input_fingerprint"])):
        raise ArtifactValidationError("Agent结果输入指纹格式无效")
    if result["status"] == "COMPLETED":
        contract = task["output_contract"]
        artifact = result["artifact"]
        if contract["format"] == "text":
            if not isinstance(artifact, str) or not artifact.strip():
                raise ArtifactValidationError("text任务的完成态 artifact 必须是非空字符串")
            checks.append("completed text artifact validated")
        else:
            checks.extend(
                validate_schema_instance(
                    root,
                    contract["schema"],
                    artifact,
                    label="Agent artifact",
                )
            )
    return checks + [
        f"agent result validated: {result['task_id']}",
        f"task/result binding validated: {result['input_fingerprint']}",
    ]


def validate_style_comparison(
    project_root: Path,
    comparison: dict[str, Any],
    candidates: dict[str, str],
    *,
    source_fingerprint: str | None = None,
    calibration_id: str | None = None,
) -> list[str]:
    checks = validate_schema_instance(
        project_root,
        "schemas/style_comparison.schema.json",
        comparison,
        label="文风比较报告",
    )
    labels = {"A", "B", "C"}
    if not isinstance(candidates, dict) or set(candidates) != labels or not all(
        isinstance(text, str) and text.strip() for text in candidates.values()
    ):
        raise ArtifactValidationError("盲评候选必须恰好包含非空的 A、B、C 正文")
    if source_fingerprint is not None:
        if not HEX_64.fullmatch(source_fingerprint):
            raise ArtifactValidationError("源场景指纹格式无效")
        if comparison["source_fingerprint"] != source_fingerprint:
            raise ArtifactValidationError("文风比较报告引用了不同的源场景指纹")
    if calibration_id is not None and comparison["calibration_id"] != calibration_id:
        raise ArtifactValidationError("文风比较报告 calibration_id 与任务不一致")
    expected_hashes = {
        label: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for label, text in candidates.items()
    }
    if comparison["candidate_fingerprints"] != expected_hashes:
        raise ArtifactValidationError("文风比较报告候选指纹与当前 A/B/C 正文不一致")
    ranking = comparison["ranking"]
    if set(ranking) != labels:
        raise ArtifactValidationError("文风比较 ranking 必须恰好覆盖 A、B、C")
    if comparison["preferred_candidate"] != ranking[0]:
        raise ArtifactValidationError("preferred_candidate 必须等于 ranking 第一项")
    findings = comparison["candidate_findings"]
    if {item["candidate"] for item in findings} != labels:
        raise ArtifactValidationError("candidate_findings 必须恰好覆盖 A、B、C")
    for item in findings:
        label = item["candidate"]
        observations = item["strengths"] + item["gaps"]
        if not observations:
            raise ArtifactValidationError(f"候选 {label} 至少需要一项带引用的观察")
        for observation in observations:
            if observation["quote"] not in candidates[label]:
                raise ArtifactValidationError(
                    f"候选 {label} 的观察引用不在当前正文中: {observation['quote']!r}"
                )
    return checks + [
        "style comparison covers blind candidates A/B/C",
        "style comparison fingerprints and verbatim quotes validated",
    ]


def validate_prompt_native_project(project_root: Path) -> list[str]:
    root = project_root.resolve()
    spec = load_project_spec(root)
    missing = [path for path in REQUIRED_PROTOCOL_FILES if not (root / path).is_file()]
    if missing:
        raise ArtifactValidationError(
            "通用工作流缺少文件: " + ", ".join(missing)
        )

    core_config = spec.load_config()
    coupled = sorted(EXECUTION_KEYS & set(core_config))
    if coupled:
        raise ArtifactValidationError(
            "核心创作配置仍包含执行后端字段: " + ", ".join(coupled)
        )

    retained_execution = [
        path for path in RETIRED_EXECUTION_FILES if (root / path).exists()
    ]
    if retained_execution:
        raise ArtifactValidationError(
            "仓库仍保留已淘汰的模型执行层: "
            + ", ".join(retained_execution)
        )
    execution_markers = (
        "co" + "dex exec",
        "Codex" + "CliProvider",
        "Agy" + "CliProvider",
        "OpenCode" + "CliProvider",
        "Grok" + "CliProvider",
        "Model" + "Provider",
        "Stage" + "ModelRouter",
        "Model" + "CallExecutor",
        "Quality" + "EvolutionEngine",
        "Writing" + "Pipeline",
        ".gen" + "erate(",
        "import sub" + "process",
    )
    leaked: list[str] = []
    for path in (root / "scripts").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if any(marker in source for marker in execution_markers):
            leaked.append(str(path.relative_to(root)))
    if leaked:
        raise ArtifactValidationError(
            "活动脚本重新引入了仓库内模型执行: "
            + ", ".join(sorted(leaked))
        )

    for schema_path in (
        "schemas/task_envelope.schema.json",
        "schemas/agent_result.schema.json",
        "schemas/protocol_smoke_artifact.schema.json",
        "schemas/style_comparison.schema.json",
        "schemas/scene_experiment.schema.json",
        "schemas/contract_scene_plan.schema.json",
        "schemas/character_intention.schema.json",
        "schemas/world_resolution.schema.json",
        "schemas/pov_event_trace.schema.json",
        "schemas/rolling_horizon.schema.json",
        "schemas/scene_experiment_comparison.schema.json",
    ):
        schema = _read_object(root / schema_path)
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ArtifactValidationError(f"协议 Schema 版本无效: {schema_path}")

    style_schema = _read_object(root / "schemas/style_comparison.schema.json")
    if style_schema.get("properties", {}).get("schema", {}).get("const") != (
        "novel-style-comparison"
    ):
        raise ArtifactValidationError("文风比较 Schema 标识无效")
    ranking = style_schema.get("properties", {}).get("ranking", {})
    if ranking.get("minItems") != 3 or not ranking.get("uniqueItems"):
        raise ArtifactValidationError("文风比较必须对三个不同候选排序")

    profile = spec.path("reference_voice_profile").read_text(
        encoding="utf-8"
    )
    for marker in (
        "schema: novel-reference-voice-profile",
        "## 八项正向原则",
    ):
        if marker not in profile:
            raise ArtifactValidationError(f"正向文风画像缺少标记: {marker}")
    if not re.search(r"(?m)^calibration_status: (?:CALIBRATING|READY)$", profile):
        raise ArtifactValidationError("正向文风画像校准状态无效")
    agent_paths = core_config.get("agents", {})
    expected_agents = {
        "prose_director": "agents/prose_director.md",
        "reference_style_reviewer": "agents/reference_style_reviewer.md",
        "scene_contract_planner": "agents/scene_contract_planner.md",
        "scene_contract_writer": "agents/scene_contract_writer.md",
        "character_actor": "agents/character_actor.md",
        "world_resolver": "agents/world_resolver.md",
        "rolling_scene_planner": "agents/rolling_scene_planner.md",
        "event_renderer": "agents/event_renderer.md",
        "scene_experiment_reader": "agents/scene_experiment_reader.md",
    }
    for name, path in expected_agents.items():
        if agent_paths.get(name) != path:
            raise ArtifactValidationError(f"核心配置缺少文风角色: {name}")
        role_paths(root, name)

    task = _read_object(root / "tests/fixtures/prompt_native_task.json")
    result = _read_object(root / "tests/fixtures/prompt_native_result.json")
    checks = [
        "prompt-native workflow files exist",
        "core story config is execution-backend agnostic",
        "repository model execution layer is absent",
        "protocol schemas loaded",
        "style calibration workflow and positive voice profile loaded",
    ]
    checks.extend(validate_task_envelope(root, task))
    checks.extend(validate_agent_result(task, result, root))
    return checks
