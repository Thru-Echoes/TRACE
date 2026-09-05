# Decision-log fixture — replay mode (legacy log)

A two-part fixture for `trace-mcp import decision-log`. The log and its result
files are **inputs** and never change; only the conversion of them does.

## Files

| File | What it is |
|---|---|
| `decisions.jsonl` | Three decision-log lines written by the producer's gate. |
| `results/**` | The result files the lines' evidence entries name and digest. |
| `expected_session.json` | The log converted by the producer's converter at `5e763e3`, kept frozen as a pre-Milestone-1 document. |
| `expected_session_nullfilled.json` | The same log converted at `9f48fa09e5857a5ea2974c1b5de5b082b727bbe1`. **This is the document the importer must reproduce.** |
| `MANIFEST.sha256` | Pins every file above; `tests/test_importer_decision_log.py` recomputes it. |

## Why two expected documents

`expected_session.json` is the older conversion, retained so the pre-Milestone-1
shape stays readable. The Milestone 1 converter added seven gated-mode keys to
the `confidence` block and fills them with explicit `null` on a log that predates
them. `expected_session_nullfilled.json` is that conversion, and it is what the
parity test compares against — a legacy log must keep importing, with each of the
seven keys present and null rather than absent.

## Provenance of the log

Generated on 2026-09-03 by the producer's gate at `5e763e3`, on the lineage in
that repository's `tests/test_decide.py`: v2 against v1 reverts; v3 against v1 is
provisional; v3 replicated on fresh seeds keeps.

## Regenerating

Regenerate the log and `results/**` only with the producer checked out at exactly
`5e763e3`:

```bash
P=<path to the producer repository>
test "$(git -C "$P" rev-parse --short HEAD)" = "5e763e3"
# Build the result trees, then run the gate three times with a fixed clock:
DECIDE_FIXED_TIMESTAMP=2026-09-03T18:01:00+00:00 python3 "$P/gate/decide.py" --methods "$M" --version v2 --parent v1
DECIDE_FIXED_TIMESTAMP=2026-09-03T18:02:00+00:00 python3 "$P/gate/decide.py" --methods "$M" --version v3 --parent v1
DECIDE_FIXED_TIMESTAMP=2026-09-03T18:03:00+00:00 python3 "$P/gate/decide.py" --methods "$M" --version v3 --parent v1 --replicates v3
```

Regenerate `expected_session_nullfilled.json` by running the converter at the
Milestone 1 commit over the unchanged log:

```bash
python3 <producer gate at 9f48fa0>/trace_from_decisions.py decisions.jsonl \
  --project rsi-exam-provenance --rollout fixture-rollout --task game2048_policy_search \
  --harness claude-code --model claude-opus-5 --output expected_session_nullfilled.json
```

Then rebuild `MANIFEST.sha256`. Never hand-edit any file here: the parity test
compares the importer against these bytes, so an edited fixture asserts the
importer against a guess.
