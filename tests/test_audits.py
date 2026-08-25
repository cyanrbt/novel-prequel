import unittest

from scripts.prequel.audits import due_audits


class AuditTests(unittest.TestCase):
    def test_due_intervals(self):
        self.assertEqual(
            due_audits(20),
            {"health": True, "arc": True},
        )
        self.assertEqual(
            due_audits(10),
            {"health": True, "arc": False},
        )
        self.assertEqual(
            due_audits(7),
            {"health": False, "arc": False},
        )


if __name__ == "__main__":
    unittest.main()
