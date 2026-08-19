import unittest

from scripts.prequel.evidence_hierarchy import (
    detect_evidence_hierarchy_escalations,
    extract_pending_propositions,
)


ATTEMPT49_EVIDENCE_EXCERPT = """
隔着左边院门的半尺缝，张洞先看见一只沾着蓝色染料的手。那只手里夹着一张盖了红印的收据，下面还攥着几枚钱。
门外的人说：“钱在这里，顶针也在包里。你们把门卡着做什么？”
张洞摸到木楔尾端一道新起的白茬：门外再推几下，它会顺着木纹纵裂；把木栓落槽，又会将外面的人彻底关死。
木楔既退不出，也不能再吃深。张洞又捡起斜靠在院门后的木栓，横抱在胸前，没有将它插回槽里。
他盯着门缝里的手：“先把顶针递进来。”
""".strip()


def findings(draft: str, **claims: str):
    return detect_evidence_hierarchy_escalations(
        draft,
        [
            {"field_path": path, "claim": claim}
            for path, claim in claims.items()
        ],
    )


class EvidenceHierarchyTests(unittest.TestCase):
    def test_attempt49_replay_finds_precise_reader_and_state_paths(self):
        result = findings(
            ATTEMPT49_EVIDENCE_EXCERPT,
            **{
                "reader_recap.current_goal": "门外持有顶针和收据的人仍在门外。",
                "reader_recap.causal_chain": "门外随后出现持有收据、钱和顶针的人。",
                "adversarial_checks.ordinary_explanations[0]": "门外持顶针、收据和钱的人仍可能是第三人。",
                "reader_visible_summary.core": "屋内布包与门外收据、顶针相冲突。",
                "hook.content": "张洞封住门缝并要求递入顶针。",
            },
        )
        self.assertEqual(
            [(item["code"], item["field_path"]) for item in result],
            [
                ("REPORT_EVIDENCE_LEVEL_ESCALATION", "reader_recap.current_goal"),
                ("REPORT_EVIDENCE_LEVEL_ESCALATION", "reader_recap.causal_chain"),
                (
                    "REPORT_EVIDENCE_LEVEL_ESCALATION",
                    "adversarial_checks.ordinary_explanations[0]",
                ),
                ("REPORT_EVIDENCE_LEVEL_ESCALATION", "reader_visible_summary.core"),
                ("REPORT_BOUNDARY_STATE_ESCALATION", "hook.content"),
            ],
        )
        self.assertTrue(all("claim" in item for item in result))

    def test_attempt49_safe_fact_levels_do_not_trigger(self):
        result = findings(
            ATTEMPT49_EVIDENCE_EXCERPT,
            **{
                "reader_recap.current_goal": "门外亮出收据和钱，并声称顶针在包里。",
                "reader_recap.causal_chain": "张洞要求先递入顶针，尚未收到或核验。",
                "reader_visible_summary.core": "门外可见收据和钱，顶针仍只是说法。",
                "hook.content": "张洞加固木楔并维持半尺门缝。",
            },
        )
        self.assertEqual(result, [])

    def test_object_is_dynamic_and_requires_claim_plus_pending_request(self):
        for object_name in ("银簪", "祖传铜钥匙", "祖上传下来的黄铜小钥匙"):
            with self.subTest(object_name=object_name):
                draft = (
                    f"院外有人说：“{object_name}就在包里。”\n"
                    f"张洞说：“先把{object_name}递进来。”"
                )
                pending = extract_pending_propositions(draft)
                self.assertEqual([item["object"] for item in pending], [object_name])
                result = findings(draft, x=f"门外持有{object_name}的人")
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["object"], object_name)

        claim_only = "院外有人说：“银簪在包里。”"
        self.assertEqual(extract_pending_propositions(claim_only), [])

    def test_modal_negative_and_direct_completion_are_fail_closed(self):
        draft = "院外有人说：“银簪在包里。”\n张洞说：“先把银簪递进来。”"
        safe = (
            "门外可能持有银簪的人",
            "门外声称持有银簪的人",
            "门外没有银簪",
            "门外要求把银簪递进来",
            "银簪尚未递入",
            "张洞是否收到银簪",
        )
        for claim in safe:
            with self.subTest(claim=claim):
                self.assertEqual(findings(draft, x=claim), [])
        for claim in ("银簪已经递入", "张洞已经收到银簪", "张洞完成了银簪核验"):
            with self.subTest(claim=claim):
                self.assertEqual(len(findings(draft, x=claim)), 1)

        observed = draft + "\n张洞看见门外那只手里攥着银簪。"
        self.assertEqual(extract_pending_propositions(observed), [])

    def test_question_scope_and_passive_negation_survive_comma_splitting(self):
        draft = "院外有人说：\u201c银簪在包里。\u201d\n张洞说：\u201c先把银簪递进来。\u201d"
        safe = (
            "门外人能否在木楔失效前，把银簪递入院内？",
            "门外人是否还能靠近，先把银簪递入院内？",
            "文本结束时银簪仍未被递入或接收。",
            "银簪没有被递入，也未被张洞接收。",
        )
        for claim in safe:
            with self.subTest(claim=claim):
                self.assertEqual(findings(draft, x=claim), [])

        completed_after_reset = (
            "门外人能否递入银簪尚不清楚，但后来银簪已经递入。"
        )
        result = findings(draft, x=completed_after_reset)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["clause"], "但后来银簪已经递入")

        explicit_completion_after_question = (
            "门外人能否递入银簪尚不清楚，银簪已经递入。"
        )
        result = findings(draft, x=explicit_completion_after_question)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["clause"], "银簪已经递入")

        for claim in (
            "银簪已被递入。",
            "张洞已经收到银簪。",
            "门外人能否递入银簪尚不清楚，银簪已递入。",
            "门外人能否递入银簪尚不清楚，张洞已收到银簪。",
            "门外人能否递入银簪尚不清楚，银簪递入了。",
            "银簪仍未被递入却已经收到银簪。",
        ):
            with self.subTest(claim=claim):
                self.assertEqual(len(findings(draft, x=claim)), 1)

        for claim in (
            "张洞能否确认，银簪已经递入院内？",
            "张洞是否知道，银簪已经递入院内？",
            "银簪已经递入？",
            "张洞已经收到银簪？",
            "银簪递入了？",
            "张洞怎么才能确认，银簪已经递入院内？",
            "谁能确认，银簪已经递入院内？",
            "若他所言属实，银簪已经递入院内。",
            "如果记录无误，张洞已经收到银簪。",
            "门外人声称，银簪已经递入院内。",
        ):
            with self.subTest(question=claim):
                self.assertEqual(findings(draft, x=claim), [])

        for claim in (
            "银簪已经递入，张洞难道还不知道吗？",
            "银簪已递入院内，张洞能否确认？",
            "威胁若有若无，银簪已经递入。",
            "神色自若，张洞已经收到银簪。",
            "证据若干，银簪递入了。",
            "门外人的说法不可信；银簪已经递入。",
        ):
            with self.subTest(assertion_before_question=claim):
                result = findings(draft, x=claim)
                self.assertEqual(len(result), 1)
                self.assertIn("银簪", result[0]["clause"])

        attempt54_safe = (
            "门外人能否在木楔失效或屋内人找到越过父亲的机会前，"
            "把顶针递入院内，而张洞又该据此如何处置两名尚未核验的来人？",
            "张洞只要求递入，文本结束时顶针仍未被递入或接收。",
        )
        for claim in attempt54_safe:
            with self.subTest(attempt54_claim=claim):
                self.assertEqual(
                    findings(ATTEMPT49_EVIDENCE_EXCERPT, x=claim), []
                )

    def test_open_boundary_rejects_only_completed_state_not_intent_or_negation(self):
        draft = (
            "左边院门仍留半尺缝。"
            "张洞抱着木栓，没有将它插回槽里。"
        )
        self.assertEqual(len(findings(draft, x="张洞封住门缝")), 1)
        for claim in ("张洞并未封住门缝", "张洞将封住门缝", "张洞必须封住门缝"):
            with self.subTest(claim=claim):
                self.assertEqual(findings(draft, x=claim), [])

    def test_meta_negation_does_not_become_a_completed_boundary_state(self):
        draft = "左边院门仍留半尺缝。张洞抱着木栓，没有将它插回槽里。"
        safe = (
            "这是要求，不被概括成院门已经关死。",
            "这并非把院门写成已经关死。",
            "院门未被视为已经关死。",
            "这里没有被认作院门已经关死。",
        )
        for claim in safe:
            with self.subTest(claim=claim):
                self.assertEqual(findings(draft, x=claim), [])
        self.assertEqual(len(findings(draft, x="院门已经关死")), 1)

    def test_attempt51_meta_negation_and_future_condition_are_not_completion(self):
        draft = (
            "院外有人说：“顶针在包里。”"
            "张洞说：“先把顶针递进来。”"
            + "左边院门仍留半尺缝。张洞抱着木栓，没有将它插回槽里。"
        )
        safe = (
            "这是门内人的要求，叙述未称木栓已落槽。",
            "动作目标具体，未误称顶针已递入。",
            "木栓落下便会把对方关死。",
            "若木栓落下，就会把对方关死。",
            "张洞正确区分木楔失效与木栓落槽后的封门后果。",
            "这是屋内人的要求，不能等同于木栓已经落槽。",
            "木栓尚未落槽，不等同于院门已经关死。",
        )
        for claim in safe:
            with self.subTest(claim=claim):
                self.assertEqual(findings(draft, x=claim), [])

        for claim in (
            "院门已经关死。",
            "木栓已经落槽。",
            "木栓落槽后院门已经关死。",
            "顶针已递入。",
            "张洞已经收到顶针。",
        ):
            with self.subTest(claim=claim):
                self.assertEqual(len(findings(draft, x=claim)), 1)


if __name__ == "__main__":
    unittest.main()
