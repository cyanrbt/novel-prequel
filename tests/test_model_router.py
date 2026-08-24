import unittest

from scripts.prequel.errors import ProviderError
from scripts.prequel.model_router import ResolvedModelSettings, StageModelRouter


class StubProvider:
    model = "embedded-test"
    reasoning_effort = "none"

    def generate(self, prompt, output_schema=None):
        return prompt


class ModelRouterTests(unittest.TestCase):
    def test_single_in_process_provider_serves_every_stage(self):
        provider = StubProvider()
        router = StageModelRouter.single(provider)
        self.assertIs(router.provider_for("planner"), provider)
        self.assertIs(router.provider_for("selector"), provider)
        self.assertEqual(router.settings_for("planner").model, "embedded-test")

    def test_explicit_in_process_routes_are_supported(self):
        planner = StubProvider()
        writer = StubProvider()
        router = StageModelRouter(
            {"planner": planner, "writer": writer},
            {"planner": "planner", "candidate_writer": "writer"},
            {
                "planner": ResolvedModelSettings("planner", "embedded-a", "none"),
                "writer": ResolvedModelSettings("writer", "embedded-b", "none"),
            },
        )
        self.assertIs(router.provider_for("planner"), planner)
        self.assertIs(router.provider_for("candidate_writer"), writer)

    def test_unknown_in_process_route_is_rejected(self):
        router = StageModelRouter({"default": StubProvider()}, {"writer": "missing"})
        with self.assertRaises(ProviderError):
            router.provider_for("writer")


if __name__ == "__main__":
    unittest.main()
