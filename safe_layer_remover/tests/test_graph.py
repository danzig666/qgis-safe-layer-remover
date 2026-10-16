"""Unit tests of the pure dependency graph."""

import unittest

import utilities  # noqa: F401

from safe_layer_remover.core.graph import DependencyGraph
from safe_layer_remover.core.model import Certainty, DepKind, Dependency, Severity, Status


def edge(required, dependent, certainty=Certainty.HIGH, severity=Severity.BLOCK, kind=DepKind.JOIN):
    return Dependency(required, dependent, dependent or "obj", kind, severity, certainty, "", "test")


class GraphTest(unittest.TestCase):
    def test_direct(self):
        g = DependencyGraph([edge("B", "A")], ["A", "B"])
        self.assertEqual(g.evaluate({"B"}).status(), Status.BLOCKED)
        self.assertEqual(g.evaluate({"A"}).status(), Status.SAFE)

    def test_set_is_safe_together(self):
        g = DependencyGraph([edge("B", "A")], ["A", "B"])
        self.assertTrue(g.evaluate({"A", "B"}).is_safe())
        self.assertFalse(g.evaluate({"B"}).is_safe())

    def test_transitive_chain(self):
        g = DependencyGraph([edge("B", "A"), edge("A", "C")], ["A", "B", "C"])
        res = g.evaluate({"B"})
        self.assertEqual(set(res.victims), {"A", "C"})
        self.assertTrue(res.victims["A"].direct)
        self.assertFalse(res.victims["C"].direct)
        self.assertEqual([d.dependent_id for d in res.victims["C"].chain], ["A", "C"])
        self.assertEqual(g.closure("B"), frozenset({"A", "B", "C"}))
        self.assertTrue(g.evaluate(g.closure("B")).is_safe())

    def test_cycle(self):
        g = DependencyGraph([edge("A", "B"), edge("B", "A")], ["A", "B"])
        self.assertEqual(set(g.evaluate({"A"}).victims), {"B"})
        self.assertTrue(g.evaluate({"A", "B"}).is_safe())
        self.assertEqual(g.closure("A"), frozenset({"A", "B"}))

    def test_low_certainty_is_uncertain(self):
        g = DependencyGraph([edge("B", "A", Certainty.LOW)], ["A", "B"])
        res = g.evaluate({"B"})
        self.assertEqual(res.status(), Status.UNCERTAIN)
        self.assertFalse(res.is_safe(uncertain_is_blocked=True))
        self.assertTrue(res.is_safe(uncertain_is_blocked=False))

    def test_low_chain_with_certain_alternative(self):
        g = DependencyGraph([edge("B", "A", Certainty.LOW), edge("B", "C"), edge("C", "A")], ["A", "B", "C"])
        res = g.evaluate({"B"})
        self.assertTrue(res.victims["A"].certain)

    def test_warnings_and_objects(self):
        deps = [
            edge("A", None, severity=Severity.WARN, kind=DepKind.MEMORY_DATA_LOSS),
            edge("B", None, severity=Severity.BLOCK, kind=DepKind.ATLAS),
            edge("C", None, severity=Severity.INFO, kind=DepKind.MAP_THEME),
        ]
        g = DependencyGraph(deps, ["A", "B", "C"])
        self.assertEqual(g.evaluate({"A"}).status(), Status.WARN)
        self.assertEqual(g.evaluate({"B"}).status(), Status.BLOCKED)
        self.assertEqual(g.evaluate({"C"}).status(), Status.SAFE)

    def test_warn_edge_to_removed_layer_dropped(self):
        g = DependencyGraph([edge("B", "A", severity=Severity.WARN, kind=DepKind.MASKING)], ["A", "B"])
        self.assertEqual(g.evaluate({"B"}).status(), Status.WARN)
        self.assertEqual(g.evaluate({"A", "B"}).status(), Status.SAFE)

    def test_locked_and_error(self):
        g = DependencyGraph([], ["A", "B"], locked_ids=["A"], error_ids=["B"])
        self.assertEqual(g.evaluate({"A"}).status(), Status.LOCKED)
        self.assertEqual(g.evaluate({"B"}).status(), Status.ERROR)
        self.assertFalse(g.evaluate({"A"}).is_safe())
        self.assertFalse(g.evaluate({"B"}).is_safe())


if __name__ == "__main__":
    unittest.main()
