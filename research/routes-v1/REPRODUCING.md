# Reproducing Anachron Routes v1

> **BLOCKED and unexecuted.** This document is historical provenance, not an
> execution authorization. The Routes v1 intervention confounds distinct source
> contents with presentation date. Do not execute any command below; use the
> schema-incompatible v2 protocol instead.

This is the operational runbook for the frozen Routes v1 contract. It is deliberately gated. A command may validate or prepare evidence without authorizing model execution, human approval, a paper claim, or an arXiv submission.

Run commands from the repository root. All raw source snapshots, runner ledgers, review packets, and analysis outputs below live under research/routes-v1/artifacts/, which is ignored by Git. Do not commit raw Wikipedia revision text or an unreviewed model-response ledger.

## Fixed inputs and accounting

- Contract: research/routes-v1/contract.json
- Pinned sampling frame: research/routes-v1/sampling_frame.json
- Pilot source-audit candidates: 20; curated pairs pending human validation: 18
- Extension source-audit candidates: 40; curated pairs pending human validation: 36
- Maximum schedules after a sealed manifest: 108 pilot trajectories and 432 confirmatory trajectories, never pooled
- Local paper-build pin: Tectonic 0.17.0 ZIP SHA-256 f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f

The Tectonic pin records the local reproduction input only. It is not evidence that an arXiv build will succeed.

## 1. Rebuild the pinned sampling frame

This is a read-only network check of exact pinned upstream bytes. It must write a new ignored artifact rather than overwrite the committed frame.

~~~powershell
New-Item -ItemType Directory -Force research/routes-v1/artifacts/rebuild | Out-Null
python -m anachron.routes.sources build-frame --contract research/routes-v1/contract.json --output research/routes-v1/artifacts/rebuild/sampling_frame.json
~~~

Compare the rebuilt frame with the committed one before any further action. A different raw hash, redirect receipt, schema, or title-year membership is a stop condition.

## 2. Discover one declared revision pair

Discovery is source collection only. It does not curate a question, approve a pair, or call a model. Use only a title and phase already declared in the contract.

~~~powershell
New-Item -ItemType Directory -Force research/routes-v1/artifacts/discovery/pilot | Out-Null
python -m anachron.routes.sources discover --contract research/routes-v1/contract.json --sampling-frame research/routes-v1/sampling_frame.json --phase pilot --title YouTube --output research/routes-v1/artifacts/discovery/pilot/youtube.json
~~~

For the extension phase, use --phase full and an output below artifacts/discovery/full/. A SourceIneligibleError is a source rejection, not a reason to substitute a different revision.

## 3. Rebuild the pending curation drafts

These commands recompute exact anchor snippets from ignored discovery artifacts. They do not set human validation.

~~~powershell
New-Item -ItemType Directory -Force research/routes-v1/artifacts/curation | Out-Null
python -m anachron.routes.manifest prepare-draft --contract research/routes-v1/contract.json --sampling-frame research/routes-v1/sampling_frame.json --input research/routes-v1/curation/pilot.inputs.json --discovery-directory research/routes-v1/artifacts/discovery/pilot --output research/routes-v1/artifacts/curation/pilot.draft.rebuilt.json
python -m anachron.routes.manifest prepare-draft --contract research/routes-v1/contract.json --sampling-frame research/routes-v1/sampling_frame.json --input research/routes-v1/curation/full.inputs.json --discovery-directory research/routes-v1/artifacts/discovery/full --output research/routes-v1/artifacts/curation/full.draft.rebuilt.json
~~~

Each rebuilt draft must exactly match its corresponding pending draft in research/routes-v1/curation/. The builder refuses missing candidates, duplicate anchors, anchors that occur in the opposite snapshot, missing source artifacts, and edited provenance.

## 4. Human source-curation review - required human action

The following command only renders the pilot review packet and decision template. It does not approve any pair.

~~~powershell
python -m anachron.routes.manifest review-packet --draft research/routes-v1/curation/pilot.draft.json --output research/routes-v1/artifacts/curation/pilot.review.md
python -m anachron.routes.manifest decision-template --draft research/routes-v1/curation/pilot.draft.json --output research/routes-v1/artifacts/curation/pilot.decisions.template.json
~~~

Repeat with full in the paths for the extension review. A human reviewer must personally inspect the immutable pre/post revision links and complete every PASS decision and rejection acknowledgement. Do not ask an automated agent to supply a validator identity, timestamp, certification, or PASS decision.

Only after that human action, apply the completed decision file to create a separately reviewed draft:

~~~powershell
python -m anachron.routes.manifest apply-human-decisions --contract research/routes-v1/contract.json --sampling-frame research/routes-v1/sampling_frame.json --curation-input research/routes-v1/curation/pilot.inputs.json --discovery-directory research/routes-v1/artifacts/discovery/pilot --draft research/routes-v1/curation/pilot.draft.json --decisions research/routes-v1/artifacts/curation/pilot.decisions.json --output research/routes-v1/artifacts/curation/pilot.reviewed.json
~~~

This step requires a real human decision and is not to be executed by an automated agent.

## 5. Seal and validate - only after human source curation

A pending draft cannot be sealed. After the reviewed draft exists, this offline command re-verifies its source artifacts, then emits a manifest without raw revision text or local artifact filenames.

~~~powershell
New-Item -ItemType Directory -Force research/routes-v1/artifacts/manifests | Out-Null
python -m anachron.routes.manifest seal --contract research/routes-v1/contract.json --sampling-frame research/routes-v1/sampling_frame.json --draft research/routes-v1/artifacts/curation/pilot.reviewed.json --discovery-directory research/routes-v1/artifacts/discovery/pilot --output research/routes-v1/artifacts/manifests/pilot.json
python -m anachron.routes.manifest validate-manifest --contract research/routes-v1/contract.json --sampling-frame research/routes-v1/sampling_frame.json --manifest research/routes-v1/artifacts/manifests/pilot.json --discovery-directory research/routes-v1/artifacts/discovery/pilot
~~~

Seal the full phase only after the pilot gate authorizes it. The validation command is stricter than runner preflight because it re-reads the local ignored discovery artifacts.

## 6. Dry run - safe after sealing

A dry run validates the sealed manifest and prints the deterministic schedule without constructing or calling an Ollama client.

~~~powershell
New-Item -ItemType Directory -Force research/routes-v1/artifacts/runs | Out-Null
python -m anachron.routes.runner --contract research/routes-v1/contract.json --sampling-frame research/routes-v1/sampling_frame.json --manifest research/routes-v1/artifacts/manifests/pilot.json --phase pilot --ledger research/routes-v1/artifacts/runs/pilot.jsonl --dry-run
~~~

The expected pilot count is 108 only when the manifest has all 18 accepted pilot pairs. The expected full count is 432 only when the manifest has all 36 accepted extension pairs.

## 7. Synthetic calibration - required before any scientific trajectory

Calibration checks the pinned local model inventory and sends one synthetic
non-scientific request to each phase-declared model. It has no source pair, no
sealed manifest, and no place in the scientific runner ledger, blinded packet,
or analysis. It still calls the local models, so do not run it until the local
model action is authorized.

~~~powershell
python -m anachron.routes.runner --contract research/routes-v1/contract.json --phase pilot --calibration | Tee-Object research/routes-v1/artifacts/runs/pilot.calibration.json
~~~

## 8. Pilot execution - requires explicit authorization

Do not run this command merely because dry run succeeds. It dispatches local model requests and creates an append-only outcome ledger.

~~~powershell
python -m anachron.routes.runner --contract research/routes-v1/contract.json --sampling-frame research/routes-v1/sampling_frame.json --manifest research/routes-v1/artifacts/manifests/pilot.json --phase pilot --ledger research/routes-v1/artifacts/runs/pilot.jsonl
~~~

The runner uses the pinned model digest, think:false, temperature 0.2, num_predict 160, seeds 17 and 29, and a 120-second timeout. It sends strict JSON with exactly answer and citation_ids. The runner resumes only an incomplete transport-before-response attempt and writes at most one retry. Timeouts after dispatch, malformed responses, returned errors, and invalid outputs remain in the ledger and are never replaced.

## 9. Blinded two-rater audit - required human action after a complete phase

After the phase ledger is complete, render a condition-blinded packet. This command neither assigns labels nor analyzes outcomes.

~~~powershell
python -c "import json; from pathlib import Path; from anachron.routes.analysis import build_runner_blinded_audit_packet; from anachron.routes import load_contract; root=Path('research/routes-v1'); records=[json.loads(line) for line in (root/'artifacts'/'runs'/'pilot.jsonl').read_text(encoding='utf-8').splitlines()]; packet=build_runner_blinded_audit_packet(load_contract(root/'contract.json'), json.loads((root/'sampling_frame.json').read_text(encoding='utf-8')), json.loads((root/'artifacts'/'manifests'/'pilot.json').read_text(encoding='utf-8')), records, phase='pilot'); Path(root/'artifacts'/'analysis').mkdir(parents=True, exist_ok=True); Path(root/'artifacts'/'analysis'/'pilot.blinded.json').write_text(json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')"
~~~

Exactly two independent raters must label every packet item with the frozen labels and bind each label to the packet's audit_id and response hash. They must not infer route, topic, condition, or model. Their Cohen's kappa and every disagreement are reported as audit evidence. They do not replace, adjudicate, or alter the deterministic exact primary label. Pilot requires Cohen's kappa of at least 0.70.

## 10. Analyze a completed, independently labelled phase

This command requires a complete runner ledger and the two-rater audit-label JSONL file. It is a result-producing command and must not be run before the human audit is complete.

~~~powershell
python -c "import json; from pathlib import Path; from anachron.routes import load_contract; from anachron.routes.analysis import analyze_runner_phase, build_runner_blinded_audit_packet, write_analysis_artifacts; root=Path('research/routes-v1'); phase='pilot'; runner_records=[json.loads(line) for line in (root/'artifacts'/'runs'/f'{phase}.jsonl').read_text(encoding='utf-8').splitlines()]; audit_labels=[json.loads(line) for line in (root/'artifacts'/'analysis'/f'{phase}.audit-labels.jsonl').read_text(encoding='utf-8').splitlines()]; contract=load_contract(root/'contract.json'); frame=json.loads((root/'sampling_frame.json').read_text(encoding='utf-8')); manifest=json.loads((root/'artifacts'/'manifests'/f'{phase}.json').read_text(encoding='utf-8')); result=analyze_runner_phase(contract, frame, manifest, runner_records, audit_labels, phase=phase); packet=build_runner_blinded_audit_packet(contract, frame, manifest, runner_records, phase=phase); write_analysis_artifacts(root/'artifacts'/'analysis'/phase, result, packet)"
~~~

Analyze pilot and full independently. The confirmatory primary interval must exclude pilot data. Do not change the contract, source roster, gate values, or analysis settings after seeing an outcome.
