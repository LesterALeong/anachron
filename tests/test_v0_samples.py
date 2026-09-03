"""Contract tests for Anachron's canonical synthetic v0 registry."""

import hashlib
import unittest

from anachron.data.v0_samples import get_v0_samples, v0_samples_by_id


class TestV0Samples(unittest.TestCase):
    def test_registry_contains_the_stable_27_sample_panel(self):
        samples = get_v0_samples()
        self.assertEqual(len(samples), 27)
        self.assertEqual(len({sample.id for sample in samples}), 27)
        self.assertEqual(
            {sample.id for sample in samples},
            set(v0_samples_by_id()),
        )

    def test_falsifier_mechanisms_are_present(self):
        ids = set(v0_samples_by_id())
        self.assertTrue({
            "fin-acme-2021-01-future",
            "fin-borealis-2020-06-survivorship",
            "fin-cygnus-2022-06-future-survivorship",
            "fin-delta-2021-06-restatement",
            "gen-eclipse-2017-01-future",
            "gen-industrial-2023-04-restatement",
        }.issubset(ids))

    def test_prompt_carries_the_sample_as_of_date_and_one_call_contract(self):
        sample = v0_samples_by_id()["fin-delta-2021-06-restatement"]
        prompt = sample.prompt()
        self.assertIn("2021-06-01", prompt)
        self.assertIn("exactly once", prompt)

    def test_every_registry_sample_has_a_nonempty_original_target(self):
        self.assertTrue(all(sample.target for sample in get_v0_samples()))

    def test_full_date_instruction_target_snapshot_is_stable(self):
        snapshot = "\n".join(
            f"{sample.id}|{sample.as_of.isoformat()}|{sample.instruction}|{sample.target}"
            for sample in get_v0_samples()
        ).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(snapshot).hexdigest(),
            "855c438aaa472e069ac7b80103c14ef486a681581ef560f32d1ee1bfe07e88d1",
        )


if __name__ == "__main__":
    unittest.main()
