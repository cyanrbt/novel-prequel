from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .project import load_project_spec


GENERIC_AUDIT_PROFILE: dict[str, Any] = {
    "schema": "creative-audit-profile/1",
    "profile_id": "generic",
    "pov_terms": [
        "看见", "看清", "看出", "认出", "听出", "知道", "确定", "明白",
        "意识到", "察觉", "发现", "记得", "想起", "断定", "猜到", "以为", "觉得",
    ],
    "identity_subjects": [],
    "boundary_nouns": ["门", "窗"],
    "boundary_actions": ["开", "关", "推", "拉", "进", "出", "退", "站", "走"],
    "shock_terms": ["死了", "死亡", "尸体"],
    "evidence_hierarchy": {"enabled": False},
}


def _merge_profile(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(value, list) and isinstance(current, list):
            merged[key] = list(dict.fromkeys([*current, *value]))
        elif isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _merge_profile(current, value)
        else:
            merged[key] = value
    return merged


def load_audit_profile(repository_root: Path) -> dict[str, Any]:
    """Compose generic audit semantics with each profile selected by a story."""
    spec = load_project_spec(repository_root)
    merged = dict(GENERIC_AUDIT_PROFILE)
    loaded: list[str] = []
    for profile_id in spec.profiles:
        path = spec.repository_root / "profiles" / profile_id / "audit_profile.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError(f"创作类型审计配置无效 {profile_id}: {exc}") from exc
        if not isinstance(value, dict):
            raise ArtifactValidationError(f"创作类型审计配置必须是object: {profile_id}")
        merged = _merge_profile(merged, value)
        loaded.append(profile_id)
    merged["active_profiles"] = loaded
    return merged
