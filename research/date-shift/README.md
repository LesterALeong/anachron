# Date-shift study

This directory is the pre-outcome research record for the short paper provisionally titled *Backdating Post-Cutoff Documents: A Controlled Study of Qwen2.5-7B and Qwen3-14B Temporal Answer Acceptance*.

The study asks whether two exact local model builds treat byte-identical post-cutoff evidence differently when only its displayed date changes. It begins with 60 predeclared ExAnte-derived Wikipedia title/year candidates, retains six source exclusions, and mechanically proposes 54 mappings. The 54 are not yet study items: the author must audit each mapping using the readable workbook and record a bound ACCEPT/REJECT decision before the final sample size and model-call count exist.

Current state: pre-outcome construction. No number is a study result until the frozen journal is complete and the reducer has produced a replay-verified analysis receipt.

Planned artifact order:

1. `proposed_frame.json` and `proposed_items.json`: the 60 candidates and 54 mechanically proposed mappings.
2. `author_audit_workbook.md` and `author_audit.template.json`: readable evidence and the editable, author-owned decision record.
3. `AI_PREAUDIT.md`: nonbinding AI screening notes, surfaced in the workbook but never converted into author decisions.
4. `execution_plan.json`: static model identities, decoding, calibration shape, and analysis rules. It has no runtime placeholders and cannot execute the proposals.
5. A reviewed, pushed, tag-pinned audit scaffold. `audit_scaffold_release.json` is a mechanically generated release descriptor: it declares the expected tag, origin, and exact closure, but is not release evidence until that tag exists locally and on origin at the clean checkout HEAD.
6. Your completed personal author audit plus a create-only runtime preflight, both outside Git.
7. One create-only external sealed execution bundle containing audited frame/items, contract, deterministic schedule, audit, runtime evidence, and manifest.
8. One new-run-only append-only journal with calibration and loaded-backend evidence before scientific calls.
9. Replay-derived analysis tables, then a complete manuscript, PDF, and arXiv source archive.

The scaffold has deliberately no command that accepts proposed frame/items as model input. The only runner accepts `--bundle-dir` and a fresh `--run-dir`; bundle sealing is blocked until the personal audit and released-tag provenance both validate.

## Audit-scaffold release order

After the review and QA gates pass, use one previously unused annotated-tag name. The descriptor must be generated after every source and documentation change and before the scaffold commit. It is create-only: if the target exists, stop and select a new release/tag rather than overwriting historical policy.

```text
python -m tools.build_date_shift_audit_scaffold_release --repository . --tag <new-tag> --output research/date-shift/audit_scaffold_release.json
git add anachron/date_shift.py anachron/date_shift_bundle.py anachron/date_shift_provenance.py tools/build_date_shift_items.py tools/build_date_shift_audit_scaffold_release.py tools/finalize_date_shift_audit.py tools/capture_date_shift_runtime.py tools/seal_date_shift_execution_bundle.py tools/run_date_shift.py tools/analyze_date_shift.py tools/audit_date_shift_other_outputs.py tests/test_date_shift.py research/date-shift paper/date_shift .gitattributes
git diff --cached --check
git commit -m "Add date-shift audit scaffold"
git push origin HEAD:refs/heads/master
git tag -a <new-tag> -m "Date-shift audit scaffold"
git push origin refs/tags/<new-tag>
git checkout --detach <new-tag>
```

Only after the last command succeeds may runtime capture begin. The admission gate independently verifies the clean detached checkout, exact annotated local and remote tag, descriptor recomputation, closure digest, and every committed governed blob.

From the clean detached checkout, invoke the package entrypoints as modules so Python resolves only the admitted checkout package:

```text
python -m tools.capture_date_shift_runtime --repository . --endpoint http://127.0.0.1:11434 --context-tokens 8192 --output <external>/runtime_preflight.json
python -m tools.finalize_date_shift_audit --repository . --input <external>/author_audit.completed.editable.json --output <external>/author_audit.completed.json
python -m tools.seal_date_shift_execution_bundle --repository . --author-audit <external>/author_audit.completed.json --runtime-preflight <external>/runtime_preflight.json --bundle-dir <external>/date-shift-bundle
python -m tools.run_date_shift --repository . --bundle-dir <external>/date-shift-bundle --run-dir <external>/date-shift-run
```

See `PROTOCOL.md` for the scientific design and `ACCEPTANCE_MATRIX.md` for the release gates. Routes v1 remains blocked historical provenance; Routes v2 remains a separate, larger pre-outcome program.
