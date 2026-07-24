"""Integrity checks for the synthetic corpus and its naive retrieval.

Exact, by-construction leakage detection only holds if the corpus itself is
internally consistent: unique ids, resolvable restatement links that point
backward in time, coherent entity-validity windows, and an enforcement filter
that honors the publish_date == T boundary. These tests pin those invariants
so corpus growth cannot silently break the metric.
"""

import unittest
from datetime import date

from anachron.data.corpus import get_corpus, search


class TestCorpusIntegrity(unittest.TestCase):
    def setUp(self):
        self.corpus = get_corpus()
        self.by_id = {item.id: item for item in self.corpus}

    def test_ids_are_unique(self):
        ids = [item.id for item in self.corpus]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_restates_id_resolves(self):
        for item in self.corpus:
            if item.restates_id is not None:
                self.assertIn(
                    item.restates_id, self.by_id,
                    f"{item.id} restates unknown item {item.restates_id!r}",
                )

    def test_restatements_are_published_strictly_after_their_originals(self):
        for item in self.corpus:
            if item.restates_id is not None:
                original = self.by_id[item.restates_id]
                self.assertGreater(
                    item.publish_date, original.publish_date,
                    f"restatement {item.id} must postdate its original {original.id}",
                )

    def test_no_self_restatement(self):
        for item in self.corpus:
            if item.restates_id is not None:
                self.assertNotEqual(item.restates_id, item.id)

    def test_entity_validity_windows_are_ordered(self):
        for item in self.corpus:
            if item.entity_valid_from is not None and item.entity_valid_to is not None:
                self.assertLessEqual(
                    item.entity_valid_from, item.entity_valid_to,
                    f"{item.id} has an inverted entity-validity window",
                )

    def test_both_slices_contain_a_restatement_pair(self):
        finance = [i for i in self.corpus if i.restates_id and i.entity is not None]
        general = [i for i in self.corpus if i.restates_id and i.entity is None]
        self.assertGreaterEqual(len(finance), 1)
        self.assertGreaterEqual(len(general), 1)

    def test_get_corpus_returns_a_fresh_list(self):
        first = get_corpus()
        first.clear()
        self.assertGreater(len(get_corpus()), 0)


class TestSearchEnforcement(unittest.TestCase):
    """The Mode B filter drops strictly-after items and keeps the boundary."""

    def test_enforce_drops_items_published_after_the_date(self):
        results = search("delta pharma revenue", enforce_as_of=date(2021, 6, 1))
        ids = {item.id for item in results}
        self.assertIn("fin-008", ids)       # original, published 2021-02-04
        self.assertNotIn("fin-009", ids)    # restatement, published 2021-09-17

    def test_enforce_keeps_items_published_on_the_boundary_date(self):
        # publish_date == enforce_as_of is not filtered (matches the leak rule).
        results = search("delta pharma revenue", enforce_as_of=date(2021, 2, 4))
        ids = {item.id for item in results}
        self.assertIn("fin-008", ids)

    def test_unenforced_search_returns_the_restatement(self):
        results = search("delta pharma revenue")
        ids = {item.id for item in results}
        self.assertIn("fin-008", ids)
        self.assertIn("fin-009", ids)


if __name__ == "__main__":
    unittest.main()
