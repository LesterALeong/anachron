"""Contract checks for the isolated v3 sample and corpus closure."""

from __future__ import annotations

import unittest

from anachron.data.v0_samples import get_v0_samples
from anachron.data.v3_corpus import get_v3_corpus, search_v3
from anachron.data.v3_samples import get_v3_samples, v3_samples_by_id


class TestV3Samples(unittest.TestCase):
    def test_original_v0_fields_are_copied_exactly_into_the_isolated_v3_registry(self):
        v3 = {sample.id: sample for sample in get_v3_samples()}
        self.assertEqual(len(get_v0_samples()), 27)
        for sample in get_v0_samples():
            self.assertEqual(
                (v3[sample.id].as_of, v3[sample.id].instruction, v3[sample.id].expected_mechanism, v3[sample.id].target),
                (sample.as_of, sample.instruction, sample.expected_mechanism, sample.target),
                sample.id,
            )

    def test_registry_is_an_independent_28_case_panel(self):
        samples = get_v3_samples()
        self.assertEqual(len(samples), 28)
        self.assertEqual(len(v3_samples_by_id()), 28)
        self.assertIn("fin-equinox-2021-06-delisted-before-cutoff", v3_samples_by_id())

    def test_equinox_has_the_required_entity_validity_window(self):
        equinox = next(item for item in get_v3_corpus() if item.id == "fin-010")
        self.assertEqual(equinox.entity, "EQRX")
        self.assertEqual(equinox.entity_valid_from.isoformat(), "2011-05-06")
        self.assertEqual(equinox.entity_valid_to.isoformat(), "2020-02-14")

    def test_enforced_equinox_retrieval_remains_a_residual_survivorship_case(self):
        item = search_v3("Equinox", v3_samples_by_id()["fin-equinox-2021-06-delisted-before-cutoff"].as_of)
        self.assertEqual([row.id for row in item], ["fin-010"])


if __name__ == "__main__":
    unittest.main()
