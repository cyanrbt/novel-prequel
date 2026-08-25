from __future__ import annotations

import json
from pathlib import Path
from typing import Any


STANDARD_PATHS = {
    "state": "novel/state/current.json",
    "canon_registry": "novel/knowledge/canon_registry.json",
    "arc_registry": "novel/knowledge/arc_registry.json",
    "foreshadow_registry": "novel/knowledge/foreshadow_registry.json",
    "creative_debts": "novel/knowledge/creative_debts.json",
    "quality_lessons": "novel/knowledge/quality_lessons.json",
    "memory_index": "novel/knowledge/memory_index.json",
    "knowledge_dir": "novel/knowledge",
    "plots_dir": "novel/plots",
    "series_architecture": "novel/plots/series_architecture.md",
    "opening_blueprint": "novel/plots/opening_blueprint.md",
    "characters_dir": "novel/characters",
    "protagonist_card": "novel/characters/protagonist.md",
    "character_voice_fallbacks": "novel/characters/runtime_voice_fallbacks.json",
    "chapters_dir": "novel/chapters",
    "chapter_meta_dir": "novel/chapters/meta",
    "work_dir": "novel/work",
    "full_novel": "novel/full_novel.txt",
    "reviews_dir": "novel/reviews",
    "rulebook": "novel/rules/rulebook.md",
    "setting_whitelist": "novel/rules/setting_whitelist.md",
    "setting_blacklist": "novel/rules/setting_blacklist.md",
    "compact_style": "novel/style/compact_style.yaml",
    "reference_voice_profile": "novel/style/reference_voice_profile.md",
    "user_taste_contract": "novel/style/user_taste_contract.json",
    "style_anchors": "novel/style/style_anchors.txt",
    "opening_benchmarks": "novel/benchmarks/opening_compulsion.md",
}


def write_project_manifest(
    root: Path,
    *,
    engine_config: dict[str, Any] | None = None,
    profiles: list[str] | None = None,
) -> None:
    config_path = root / "config/engine.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(engine_config or {}, ensure_ascii=False), encoding="utf-8"
    )
    manifest = {
        "schema": "creative-project/1",
        "project_id": "test-project",
        "title": "《测试项目》",
        "engine_config": "config/engine.json",
        "story_config": "config/engine.json",
        "profiles": profiles or [],
        "paths": STANDARD_PATHS,
    }
    (root / "project.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
