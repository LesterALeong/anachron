# Claim-evidence map: Anachron Routes v1

This map separates the claims that are already supported by repository artifacts from claims that remain unavailable until the frozen study completes.

| Claim in manuscript | Evidence | Status |
| --- | --- | --- |
| The study is restricted to English Wikipedia revisions. | `research/routes-v1/contract.json`, `research/routes-v1/PROTOCOL.md` | Frozen design |
| Strict and misdated retrieval use fixed pre-cutoff and cutoff-plus-365-day endpoints. | `research/routes-v1/contract.json`, `anachron/routes/sources.py` | Frozen design |
| Candidate registry has 20 pilot and 40 confirmatory topics. | `research/routes-v1/contract.json` | Frozen design |
| Current screening leaves 18 pilot and 36 confirmatory executable pairs. | `research/routes-v1/curation/pilot.draft.json`, `research/routes-v1/curation/full.draft.json` | Pending human source validation |
| The contingent schedule is 108 pilot and 432 confirmatory trajectories. | Arithmetic on approved executable-pair counts and frozen conditions/seeds/models in `contract.json` | Planning calculation only |
| A post-cutoff source changes model answer routing. | Generated sealed manifest, run records, blinded labels, analysis receipt | Not available |
| Any model leaks, resists leakage, or generalizes beyond the evaluated setup. | A completed study with appropriately scoped results | Not available; do not claim |
| Two Qwen generations establish model-family generality. | None | Never claim from this design |

The preprint must remain a pre-results protocol until the missing artifacts are generated and independently reviewed.
