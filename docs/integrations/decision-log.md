# Importing a decision-gate log

`trace-mcp import decision-log` builds a TRACE session from an RSI-Exam
decision-gate log. The producer is an evaluation gate: it compares a candidate
method against its parent on a paired bootstrap, decides **keep**, **revert**, or
**provisional**, and appends one JSON object per decision to `decisions.jsonl`
under the schema id `rsi-exam-decision-log/v1`.

```bash
trace-mcp import decision-log decisions.jsonl \
  --project my-project \
  --rollout <rollout id> \
  --task <task> \
  --harness <agent harness> \
  --model <model> \
  --output session.json
```

The document goes to stdout, or to `--output`. **It is never written into the
session store.** An imported record should be reviewable before it becomes part of
a project's provenance, so persisting it is a separate, deliberate act.

## The contract's home is the producer

The format is defined by the producer, in its own `docs/decision-log-contract.md`.
That document is authoritative; this page describes only what TRACE does with it.
Its section 2 defines the `confidence` block and the event mapping:

> Keys in this order: `interval`, `method`, `sample_size`, `evidence_digests` […]
> then `contract` (this schema id), `statistic`, `unit`, `direction`, `estimate`,
> `min_effect`, `verdict`, `evidence`, `holdout`, `confirm_policy`,
> `profile_sha256`, `look_index`, `parent_method_tree_sha256`,
> `candidate_method_tree_sha256`, `sizing`, `suite`. […] The rule-state keys
> (`min_effect`, `verdict`, `holdout`) and the gated keys […] remain an identified
> extension that TRACE preserves but does not interpret.
>
> Event mapping: one `decision` event per line; `proposed_by` = the rollout agent
> […]; `resolved_by` = the gate […]; `keep` = `accepted`; `revert` = `rejected`
> […]; `provisional` stays `proposed` until the replication event revises it
> (`revises_event_id`), after which the original is resolved with "Resolved by
> replication evt_NNN."

## What the importer checks

Only what is needed to build the event graph:

- **The schema id.** A log that does not declare `rsi-exam-decision-log/v1` is
  refused. The check is repeated inside `import_decision_log`, so a caller that
  bypasses the loader cannot import a foreign log either.
- **Line ordering.** Reading a file, each line's `line` field must equal its
  1-based physical position, and blank lines are refused — the log is appended,
  never edited. A library caller passing a *slice* of a log is held only to
  strictly increasing line numbers, since a slice legitimately starts partway in.
- **Three structural lineage rules.** A replication must name its own version, must
  resolve a currently open provisional with the same parent, and must not itself be
  provisional; a second provisional cannot be opened while one is pending; and a
  line cannot build on a version whose provisional decision is unresolved.

These are structural rather than policy: violate them and the events cannot be
linked into a coherent chain at all, so the import is refused rather than a broken
graph emitted.

## What it deliberately does not check

The verdict, the minimum effect, the held-out result, the direction, the locator
syntax, the evidence role vocabulary, and whether the disposition follows from the
verdict. **The producer's gate checks these on write and its verifier owns them.**
Re-implementing them here would give the same log two authorities that can
disagree, and would refuse a producer whose rule had legitimately moved on.

For the same reason the seven gated-mode keys are typed by JSON type only — no hex
pattern on a digest, no enumerated policy name, no positive-integer bound on the
look index. A pattern here breaks the importer the next time the producer widens a
vocabulary.

## What it never does

- **Write to the session store.** A test reads the module's own source and fails if
  it so much as references `trace_mcp.storage`.
- **Extract learnings.** An imported record is another system's data, not
  conversation. The knowledge extractor skips any session whose metadata carries
  both a source and an importer, and skips machine-measured decisions wherever they
  appear.
- **Synthesize a decision that is not in the log.** Every decision event comes from
  a line.

## An unknown key is refused, loudly

A line carrying a top-level key outside the contract's set fails with exit code 2,
naming the key. The alternatives are worse: copying it breaks parity with the
producer's own converter, which does not copy it either, and dropping it silently
loses provenance this importer exists to preserve.

When this fires, it is not a bug in the importer — it means the producer's format
moved and the importer needs re-deriving against the new contract.

## `decision_log_sha256`

The CLI reads the log once as bytes, computes SHA-256 over exactly those bytes, and
records it in `metadata.custom.decision_log_sha256`. Through the library API it is
a parameter: the caller's assertion about the log it read. TRACE does not re-verify
it, and it is not an attestation that any particular party produced those bytes.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The document was produced. |
| 1 | Something else failed: a missing file, an unwritable output, a reserved project label. |
| 2 | The input is not importable: a foreign schema id, an unknown key, or a lineage violation. |

## Fixtures

Two, in `tests/fixtures/`, because one log cannot cover both halves of the
contract:

- **`decision_log_v1/`** — a replay-mode log written before the gated-mode keys
  existed. It proves an older log keeps importing, with all seven projected as
  explicit nulls rather than omitted.
- **`decision_log_profile/`** — a gated run under a task profile, the only fixture
  with those keys populated, with receipt evidence, and with a provisional decision
  that a replication resolves.

Neither was composed by hand. Both were generated by the producer's own gate and
converted by the producer's own converter, and the parity test compares this
importer against those documents. Each fixture's `README.md` records exactly how it
was generated and how to regenerate it; a `MANIFEST.sha256` pins every file, and a
test recomputes it, so a fixture cannot be edited silently.
