"""Guards Inspect task discovery.

Inspect discovers tasks by AST-scanning only **module-top-level** function defs
decorated with ``@task``. If the ``anachron`` / ``anachron_enforced`` builders
are ever nested (e.g. inside an ``if`` block), discovery silently finds nothing
and ``inspect eval anachron/inspect/task.py@anachron`` fails with
"Task not found." This test mirrors that discovery rule with stdlib ``ast`` so
it runs without inspect_ai and catches the regression directly.
"""

import ast
import pathlib
import unittest

_TASK_FILE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "anachron"
    / "inspect"
    / "task.py"
)


def _top_level_task_defs(tree: ast.Module) -> set[str]:
    """Names of top-level functions decorated with ``@task`` (Inspect's rule)."""
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                dec_name = dec.id if isinstance(dec, ast.Name) else getattr(dec, "attr", None)
                if dec_name == "task":
                    names.add(node.name)
    return names


class TestTaskDiscovery(unittest.TestCase):
    def test_tasks_are_top_level_for_inspect_discovery(self):
        tree = ast.parse(_TASK_FILE.read_text(encoding="utf-8"))
        names = _top_level_task_defs(tree)
        self.assertIn("anachron", names)
        self.assertIn("anachron_enforced", names)


try:
    import inspect_ai  # noqa: F401

    _HAVE_INSPECT = True
except ImportError:
    _HAVE_INSPECT = False


@unittest.skipUnless(_HAVE_INSPECT, "inspect_ai not installed")
class TestTaskBuild(unittest.TestCase):
    def test_both_tasks_build_with_expected_sample_count(self):
        from anachron.inspect.task import anachron, anachron_enforced

        unrestricted = anachron().dataset
        enforced = anachron_enforced().dataset
        self.assertEqual(len(unrestricted), 27)
        self.assertEqual(len(enforced), 27)
        self.assertEqual(unrestricted[0].id, "fin-acme-2021-01-future")
        self.assertEqual(enforced[-1].id, "gen-industrial-2024-01-restatement-current")
        self.assertEqual(
            unrestricted[0].target,
            "Nothing valid on or before this date; the earliest Acme item (2021-04-28) is in the future.",
        )
        self.assertEqual(
            enforced[-1].target,
            "1.4 percent, as revised (2023-07-14).",
        )


if __name__ == "__main__":
    unittest.main()
