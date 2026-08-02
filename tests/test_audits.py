import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.audits import AuditRunner, due_audits
from scripts.prequel.model_router import ResolvedModelSettings
from scripts.prequel.errors import ArtifactValidationError


class FakeProvider:
    def __init__(self, report):
        self.report = report

    def generate(self, prompt, output_schema=None):
        return json.dumps(self.report, ensure_ascii=False)


class FakeRouter:
    def __init__(self, report):
        self.provider = FakeProvider(report)

    def provider_for(self, stage):
        return self.provider

    def settings_for(self, stage):
        return ResolvedModelSettings("terra_high", "gpt-5.6-terra", "high")


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "novel/chapters/vol_01").mkdir(parents=True)
        (self.root / "novel/knowledge").mkdir(parents=True)
        (self.root / "agents").mkdir()
        (self.root / "schemas").mkdir()
        for number in range(1, 21):
            (self.root / f"novel/chapters/vol_01/chapter_{number:03d}.txt").write_text(
                f"第{number}章：记录\n\n张洞检查第{number}道门。", encoding="utf-8"
            )
        for name, field in (
            ("memory_index", "entries"),
            ("quality_lessons", "lessons"),
            ("creative_debts", "debts"),
        ):
            (self.root / f"novel/knowledge/{name}.json").write_text(
                json.dumps({"schema": name, field: []}), encoding="utf-8"
            )
        (self.root / "agents/arc_reviewer.md").write_text(
            "只输出审计JSON", encoding="utf-8"
        )
        (self.root / "schemas/audit.schema.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, quote="张洞检查第20道门"):
        return {
            "audit_type": "health",
            "through_chapter": 20,
            "findings": [
                {
                    "code": "HOOK_REPEAT",
                    "severity": "P2",
                    "chapters": [20],
                    "evidence": [{"chapter": 20, "quote": quote}],
                    "explanation": "结尾同构",
                }
            ],
            "debts": [
                {
                    "id": "DEBT-HOOK-20",
                    "priority": "P2",
                    "scope": "future",
                    "instruction": "改变钩子类型",
                    "acceptance": "未来三章不重复",
                }
            ],
            "summary": "需要改变节奏",
        }

    def test_due_intervals(self):
        self.assertEqual(due_audits(9), {"health": False, "arc": False})
        self.assertEqual(due_audits(10), {"health": True, "arc": False})
        self.assertEqual(due_audits(20), {"health": True, "arc": True})

    def test_report_updates_debts_without_changing_formal_chapters(self):
        paths = sorted((self.root / "novel/chapters").glob("vol_*/chapter_*.txt"))
        original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        report_path = AuditRunner(self.root, FakeRouter(self.report())).run_health(20)
        self.assertTrue(report_path.exists())
        self.assertEqual(
            {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
            original,
        )
        debts = json.loads(
            (self.root / "novel/knowledge/creative_debts.json").read_text(encoding="utf-8")
        )
        self.assertTrue(debts["debts"])
        manifest = json.loads(report_path.with_suffix(".run.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["budget"]["limit"], 1)
        self.assertEqual(manifest["budget"]["spent"], 1)

    def test_false_quote_is_rejected(self):
        original = (self.root / "novel/knowledge/creative_debts.json").read_bytes()
        with self.assertRaises(ArtifactValidationError):
            AuditRunner(self.root, FakeRouter(self.report("不存在的引文"))).run_health(20)
        self.assertEqual(
            (self.root / "novel/knowledge/creative_debts.json").read_bytes(), original
        )


if __name__ == "__main__":
    unittest.main()
