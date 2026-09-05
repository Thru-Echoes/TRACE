# Decision-log fixture — gated mode (under a task profile)

The companion to `../decision_log_v1/`. That fixture proves a log written before
the gated-mode keys existed still imports, with all seven projected as explicit
nulls. This one is the only way to cover the other half of the contract:
**populated** values for all seven, receipt evidence, and a derived suite.

## Files

| File | What it is |
|---|---|
| `decisions.jsonl` | Two lines: a gated provisional screening, then the confirmation that reverts it. |
| `results/**` | The result and receipt files the evidence entries name and digest, plus the derived seed suite. |
| `gate/profile.json` | The task profile the run was written under; its digest is the `profile_sha256` on both lines. |
| `expected_session.json` | The log converted by the producer's converter at `9f48fa09e5857a5ea2974c1b5de5b082b727bbe1`. |
| `MANIFEST.sha256` | Pins every file above; `tests/test_importer_decision_log.py` recomputes it. |

## What it covers that the legacy fixture cannot

- All seven gated-mode keys populated: `confirm_policy` (`always`),
  `profile_sha256`, `look_index`, `parent_method_tree_sha256`,
  `candidate_method_tree_sha256`, `sizing`, and `suite`.
- Four evidence roles — `parent`, `candidate`, `receipt-parent`,
  `receipt-candidate` — where the legacy log carries two.
- A provisional decision that is later **resolved by a replication**: the
  importer must rebuild the earlier event with the gate as resolver and the note
  `Resolved by replication evt_002.`, and give the replication event
  `revises_event_id` pointing back at it.

## Provenance

Not composed by hand. It is the output of the producer's own end-to-end test at
the Milestone 1 commit,
`tests/test_gated_rollout.py :: test_an_identical_candidate_is_provisional_then_reverted`,
which runs the real evaluator, the gate in gated mode, and the converter. The
numbers are the deterministic outcome of the fixture policies on the fixture
seeds.

## Regenerating

Run that test from a clean export of the producer at the Milestone 1 commit, copy
the temporary `methods/` tree out before its teardown deletes it, keep
`decisions.jsonl`, `results/**` and `gate/profile.json`, then convert with the
same commit's converter:

```bash
python3 <producer gate at 9f48fa0>/trace_from_decisions.py decisions.jsonl \
  --project rsi-exam-provenance --rollout e2e-rollout --task game2048_policy_search \
  --harness claude-code --model claude-opus-5 --output expected_session.json
```

`--rollout` must be `e2e-rollout`: the suite records the rollout it was derived
for, and the converter refuses a conversion whose rollout does not match it.

Then rebuild `MANIFEST.sha256`. Never hand-edit any file here.
