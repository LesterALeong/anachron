"""Dependency-free unit tests for the Anachron leakage core.

Runs via ``python -m unittest discover -s tests`` with no third-party packages.
"""

import unittest
from datetime import date

from anachron.core.leakage import (
    CorpusItem,
    ToolInteraction,
    is_query_leak,
    is_restatement_leak,
    is_result_leak,
    is_survivorship_leak,
    score_interactions,
)

T = date(2022, 1, 1)


def _item(item_id: str, publish_date: date, **finance) -> CorpusItem:
    """Build a CorpusItem with a default text and optional finance fields."""
    return CorpusItem(id=item_id, text=f"text for {item_id}", publish_date=publish_date, **finance)


def _interaction(returned=None, query_dates=None, query="q", tool="anachron_search") -> ToolInteraction:
    """Build a ToolInteraction with sensible defaults."""
    return ToolInteraction(
        tool=tool,
        query=query,
        query_dates=list(query_dates or []),
        returned_items=list(returned or []),
    )


class TestResultLeakBoundary(unittest.TestCase):
    """The publish_date == as_of boundary and strict-greater rule."""

    def test_strictly_after_leaks(self):
        it = _interaction(returned=[_item("a", date(2022, 1, 2))])
        self.assertTrue(is_result_leak(it, T))

    def test_equal_does_not_leak(self):
        it = _interaction(returned=[_item("a", T)])
        self.assertFalse(is_result_leak(it, T))

    def test_before_does_not_leak(self):
        it = _interaction(returned=[_item("a", date(2021, 12, 31))])
        self.assertFalse(is_result_leak(it, T))

    def test_empty_results_do_not_leak(self):
        self.assertFalse(is_result_leak(_interaction(), T))


class TestScoreTclr(unittest.TestCase):
    """TCLR aggregation over interactions."""

    def test_all_leak(self):
        interactions = [
            _interaction(returned=[_item("a", date(2022, 6, 1))]),
            _interaction(returned=[_item("b", date(2023, 1, 1))]),
        ]
        result = score_interactions(interactions, T)
        self.assertEqual(result.tclr, 1.0)
        self.assertEqual(result.result_leaks, 2)
        self.assertEqual(result.total_interactions, 2)
        self.assertNotIn("no_tool_interactions", result.flags)

    def test_none_leak(self):
        interactions = [
            _interaction(returned=[_item("a", date(2021, 1, 1))]),
            _interaction(returned=[_item("b", T)]),  # equality is not a leak
        ]
        result = score_interactions(interactions, T)
        self.assertEqual(result.tclr, 0.0)
        self.assertEqual(result.result_leaks, 0)
        self.assertEqual(result.offenders, [])

    def test_mixed_half_leak(self):
        interactions = [
            _interaction(returned=[_item("a", date(2022, 2, 1))]),  # leak
            _interaction(returned=[_item("b", date(2021, 2, 1))]),  # clean
            _interaction(returned=[_item("c", date(2030, 1, 1))]),  # leak
            _interaction(returned=[_item("d", T)]),                 # clean (==)
        ]
        result = score_interactions(interactions, T)
        self.assertEqual(result.tclr, 0.5)
        self.assertEqual(result.result_leaks, 2)
        self.assertEqual(result.total_interactions, 4)
        self.assertEqual(len(result.offenders), 2)

    def test_empty_interactions_flagged(self):
        result = score_interactions([], T)
        self.assertEqual(result.tclr, 0.0)
        self.assertEqual(result.total_interactions, 0)
        self.assertEqual(result.result_leaks, 0)
        self.assertIn("no_tool_interactions", result.flags)
        self.assertIsNone(result.survivorship_rate)


class TestQueryLeak(unittest.TestCase):
    """Query/intent leakage is detected separately and excluded from TCLR."""

    def test_query_after_t_is_query_leak(self):
        it = _interaction(query_dates=[date(2022, 5, 1)])
        self.assertTrue(is_query_leak(it, T))

    def test_query_equal_t_not_leak(self):
        it = _interaction(query_dates=[T])
        self.assertFalse(is_query_leak(it, T))

    def test_query_before_t_not_leak(self):
        it = _interaction(query_dates=[date(2021, 5, 1)])
        self.assertFalse(is_query_leak(it, T))

    def test_query_leak_not_counted_in_tclr(self):
        # Query reaches the future, but the returned item is clean.
        interactions = [
            _interaction(
                returned=[_item("a", date(2021, 1, 1))],
                query_dates=[date(2023, 1, 1)],
            )
        ]
        result = score_interactions(interactions, T)
        self.assertEqual(result.tclr, 0.0)
        self.assertEqual(result.result_leaks, 0)
        self.assertEqual(result.query_leaks, 1)
        self.assertTrue(any("query" in off for off in result.offenders))


class TestSurvivorship(unittest.TestCase):
    """Point-in-time entity-validity leakage on the finance slice."""

    def test_valid_entity_does_not_leak(self):
        it = _interaction(returned=[
            _item("a", date(2021, 1, 1), entity="ACME",
                  entity_valid_from=date(2010, 1, 1), entity_valid_to=None)
        ])
        self.assertFalse(is_survivorship_leak(it, T))

    def test_delisted_before_t_leaks(self):
        # Entity ceased to be valid in 2019; as-of 2022 is after that.
        it = _interaction(returned=[
            _item("a", date(2018, 1, 1), entity="BORX",
                  entity_valid_from=date(2008, 1, 1), entity_valid_to=date(2019, 11, 5))
        ])
        self.assertTrue(is_survivorship_leak(it, T))

    def test_not_yet_listed_at_t_leaks(self):
        # Entity first valid in 2023; as-of 2022 predates it.
        it = _interaction(returned=[
            _item("a", date(2023, 3, 1), entity="CYGN",
                  entity_valid_from=date(2023, 2, 9), entity_valid_to=None)
        ])
        self.assertTrue(is_survivorship_leak(it, T))

    def test_item_without_entity_ignored(self):
        it = _interaction(returned=[_item("gen", date(2030, 1, 1))])
        self.assertFalse(is_survivorship_leak(it, T))

    def test_survivorship_rate_and_denominator(self):
        interactions = [
            # finance, valid -> not a survivorship leak
            _interaction(returned=[
                _item("a", date(2021, 1, 1), entity="ACME",
                      entity_valid_from=date(2010, 1, 1), entity_valid_to=None)
            ]),
            # finance, delisted -> survivorship leak
            _interaction(returned=[
                _item("b", date(2018, 1, 1), entity="BORX",
                      entity_valid_from=date(2008, 1, 1), entity_valid_to=date(2019, 11, 5))
            ]),
            # non-finance -> excluded from survivorship denominator
            _interaction(returned=[_item("gen", date(2021, 1, 1))]),
        ]
        result = score_interactions(interactions, T)
        self.assertEqual(result.survivorship_leaks, 1)
        # denominator = 2 finance interactions
        self.assertEqual(result.survivorship_rate, 0.5)

    def test_survivorship_rate_none_when_no_finance_items(self):
        interactions = [
            _interaction(returned=[_item("gen1", date(2021, 1, 1))]),
            _interaction(returned=[_item("gen2", date(2030, 1, 1))]),
        ]
        result = score_interactions(interactions, T)
        self.assertEqual(result.survivorship_leaks, 0)
        self.assertIsNone(result.survivorship_rate)


class TestRestatement(unittest.TestCase):
    """Post-T restatements of earlier items are a distinct, separately-reported leak."""

    def test_post_t_restatement_leaks(self):
        it = _interaction(returned=[
            _item("fin-rev", date(2022, 9, 1), restates_id="fin-orig")
        ])
        self.assertTrue(is_restatement_leak(it, T))

    def test_restatement_on_t_does_not_leak(self):
        # Boundary rule matches the other axes: equality is not a leak.
        it = _interaction(returned=[_item("fin-rev", T, restates_id="fin-orig")])
        self.assertFalse(is_restatement_leak(it, T))

    def test_pre_t_restatement_does_not_leak(self):
        # A revision already published as of T is the legitimate record.
        it = _interaction(returned=[
            _item("fin-rev", date(2021, 6, 1), restates_id="fin-orig")
        ])
        self.assertFalse(is_restatement_leak(it, T))

    def test_post_t_item_without_restates_id_is_not_restatement_leak(self):
        it = _interaction(returned=[_item("fin-news", date(2022, 9, 1))])
        self.assertFalse(is_restatement_leak(it, T))

    def test_restatement_leak_is_subset_of_result_leaks(self):
        # Every restatement leak is by construction also a result leak.
        it = _interaction(returned=[
            _item("fin-rev", date(2022, 9, 1), restates_id="fin-orig")
        ])
        self.assertTrue(is_restatement_leak(it, T))
        self.assertTrue(is_result_leak(it, T))

    def test_score_counts_and_offender_names_both_items(self):
        interactions = [
            _interaction(returned=[
                _item("fin-rev", date(2022, 9, 1), restates_id="fin-orig")
            ]),
            _interaction(returned=[_item("clean", date(2021, 1, 1))]),
        ]
        result = score_interactions(interactions, T)
        self.assertEqual(result.restatement_leaks, 1)
        self.assertEqual(result.result_leaks, 1)  # subset: same interaction
        self.assertLessEqual(result.restatement_leaks, result.result_leaks)
        restatement_offenders = [o for o in result.offenders if "revises" in o]
        self.assertEqual(len(restatement_offenders), 1)
        self.assertIn("fin-rev", restatement_offenders[0])
        self.assertIn("fin-orig", restatement_offenders[0])

    def test_empty_run_reports_zero_restatement_leaks(self):
        result = score_interactions([], T)
        self.assertEqual(result.restatement_leaks, 0)


class TestOffenders(unittest.TestCase):
    """Offender strings name the leaking tool and the offending item."""

    def test_result_offender_mentions_item_and_dates(self):
        interactions = [
            _interaction(tool="anachron_search", returned=[_item("fin-99", date(2030, 1, 1))])
        ]
        result = score_interactions(interactions, T)
        self.assertEqual(len(result.offenders), 1)
        offender = result.offenders[0]
        self.assertIn("anachron_search", offender)
        self.assertIn("fin-99", offender)
        self.assertIn("2030-01-01", offender)


class TestMultiItemInteraction(unittest.TestCase):
    """An interaction is counted once even if several of its items leak."""

    def test_one_leaking_item_counts_interaction_once(self):
        interactions = [
            _interaction(returned=[
                _item("clean", date(2021, 1, 1)),
                _item("leak", date(2023, 1, 1)),
            ]),
            _interaction(returned=[_item("clean2", date(2021, 6, 1))]),
        ]
        result = score_interactions(interactions, T)
        self.assertEqual(result.result_leaks, 1)        # the multi-item interaction counts once
        self.assertEqual(result.total_interactions, 2)
        self.assertEqual(result.tclr, 0.5)
        self.assertEqual(len(result.offenders), 1)      # only the leaking item is named


class TestDoubleAxisLeak(unittest.TestCase):
    """A not-yet-listed item dated after T leaks on BOTH axes independently."""

    def test_future_unlisted_item_is_result_and_survivorship_leak(self):
        interactions = [
            _interaction(returned=[
                _item("fin-cygn", date(2023, 3, 1), entity="CYGN",
                      entity_valid_from=date(2023, 2, 9), entity_valid_to=None)
            ])
        ]
        result = score_interactions(interactions, T)
        # Result-leak axis (folds into TCLR):
        self.assertEqual(result.result_leaks, 1)
        self.assertEqual(result.tclr, 1.0)
        # Survivorship axis (reported separately, not in TCLR):
        self.assertEqual(result.survivorship_leaks, 1)
        self.assertEqual(result.survivorship_rate, 1.0)


class TestAdapterImport(unittest.TestCase):
    """The Inspect adapter imports without inspect_ai; it only fails when used."""

    def test_inspect_subpackage_imports_without_inspect_ai(self):
        import importlib

        import anachron.inspect.scorer as scorer_mod
        importlib.import_module("anachron.inspect.tools")
        importlib.import_module("anachron.inspect.task")
        importlib.import_module("anachron._registry")

        # When inspect_ai is absent, the entry point must raise a clear ImportError
        # rather than silently succeed.
        if not scorer_mod._INSPECT_AVAILABLE:
            with self.assertRaises(ImportError):
                scorer_mod.tool_call_leakage()


if __name__ == "__main__":
    unittest.main()
