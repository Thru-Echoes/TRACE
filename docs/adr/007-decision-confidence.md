# ADR 007: Decision confidence — a typed measurement behind a decision

**Status:** Accepted
**Date:** 2026-09-03
**Context:** A decision made on a measured effect had nowhere to record that
measurement. Producers put it in free-text `rationale`, where it is unreadable
by a consumer, or in an untyped extra key, where nothing checks it. The first
producer to need it is an evaluation gate that decides whether to keep or
revert a candidate method based on a paired bootstrap interval, and writes a
decision log this project imports. The question is what a general provenance
protocol should type, given that the gate's decision rule is specific to that
producer and expected to change.

## Summary

A decision event may carry an optional `confidence` object: the
producer-recorded measurement that motivated it. The object types the
measurement and nothing else. A producer's decision rule travels with it as
preserved extra keys, identified by a `contract` key, which this project stores
untouched and never interprets.

## Decisions

### D1. A field on `DecisionData`, not a new event type

The measurement is an attribute of a decision, not an event in its own right.
It has no independent timestamp, actor or lifecycle, and a consumer reading a
decision wants it in hand rather than by a join.

*Rejected:* a `measurement` event type (invents a second thing to correlate),
an annotation category (annotations are prose and carry no structure), and an
extension (the core would then be unable to render or validate a field that
appears in core documents).

### D2. Type the measurement, not the rule

Typed: `interval`, `method`, `sample_size`, `statistic`, `unit`, `direction`,
`estimate`, `evidence`, `evidence_digests`, `contract`. Not typed: anything
that expresses whether the measurement was good enough.

*Rejected:* typing the first producer's `verdict` and `min_effect`. That
encodes one producer's policy in a generic protocol: a second producer with a
different rule would either be refused or forced to fake the fields, and every
evolution of the first producer's rule would need a protocol release. A typed
held-out result was rejected for the same reason plus a concrete one, the
producer expects that object's shape to change.

### D3. The producer's nested shape, as it is

`interval` is an object with bounds and coverage; `method` is an object with a
name and its parameters. This is the shape the first producer already writes,
so its records validate without a translation step, and its converter's real
output serves as the golden that pins the round trip.

*Rejected:* flattening to `interval_lower`, `interval_upper`,
`confidence_level`, `method_name`. That is a second spelling of one object, and
every producer and consumer would have to know both.

### D4. `direction` records the raw metric's sense; the producer orients the estimate

`direction` is `higher` or `lower`, the native sense of the raw metric the
statistic came from. Producers orient the statistic so that a positive
`estimate` favours the option the decision describes, whatever `direction`
says. Nothing here flips a sign.

*Rejected:* a prose sign convention with no field (not checkable, and a reader
cannot recover the raw metric's sense), and flipping the sign on read when
`direction` is `lower` (the first producer's gate already negates its paired
deltas, so a second negation would invert every such record).

### D5. `estimate` is recorded, not asserted to lie inside `interval`

*Rejected:* a validation rule requiring containment. It is false for percentile
bootstrap intervals, which the first producer uses: the point estimate can
fall outside the resampled bounds. A rule that refuses valid records is worse
than no rule.

### D6. Structural validation only

Construction checks finiteness, bound ordering, coverage in the open unit
interval, a sample size of at least one, digest shape, the digest map against
the evidence entries, and the absence of control characters in identifier
fields. The published JSON Schema carries every per-field constraint so a
producer validating against the file alone gets them.

*Rejected:* any check that reads the rule state, including comparing a verdict
against the interval. The rule belongs to the producer's verifier.

### D7. PROV as scalar literals plus evidence entities

The measurement projects as `trace:confidence*` literals on the decision
activity. The two bounds are separate literals, not one array, because a bare
JSON-LD array is a set and a processor may reorder it. Each evidence file is a
`prov:Entity` the decision `prov:used`, with an identifier derived from a
content hash of role, locator and digest, so two locators for the same bytes
stay distinct and a reordered list keeps its identities. As with tool inputs,
`prov:used` records the producer's assertion; no digest is verified here.

*Deferred:* reifying the measurement as its own entity with qualified
attribution. It buys nothing until a consumer needs to talk about the
measurement independently of the decision.

### D8. Schema 0.5.1 under the unchanged file name

The wire version moves to 0.5.1 while the package version does not; the two are
independent, and the package moves at its own release. The schema file keeps
its `trace-v0.5.json` name, as every 0.5.x revision does. This is a schema
addition with one narrowing: `confidence` was a name any document could
previously use for anything, and a document that used it for something else no
longer loads. Section 7.3 requires consumers to preserve unknown fields, which
is what makes the narrowing the only compatibility cost.

*Rejected:* keeping an immutable 0.5.0 schema beside a 0.5.1 one and
dispatching validation on the document's exact version. This project has
published one schema file per minor version since 0.4.1; the only narrowing is
the `confidence` name, and a 0.5.0 document that fails does so on that field, by
name, with the field path in the error. The cost of a second published artifact
and a dispatch table is not repaid by that.

*Noted:* the first producer keeps stamping `trace_version` `0.5.0` until its
own downstream consumer accepts 0.5.1. Those documents load correctly, because
the storage layer's version-skew check warns only when a file is newer than the
reader.

## Consequences

- Documents written before 0.5.1 load unchanged. A document that used
  `decision.confidence` as an untyped extra with a different shape fails
  closed, with the field named in the error.
- Exporters render the block only when it is present; a decision without one is
  rendered exactly as before.
- The knowledge-extraction extension skips imported records and
  machine-measured decisions, so a gate's mechanical keep-or-revert records do
  not become recalled learnings. Human annotations beside such a decision still
  extract.
- A `confidence` argument on the decision-proposal tool is deferred until the
  library-API work lands, since it is a tool-signature change.
- Under the append-only journal design of ADR 005, the block is captured at
  proposal time and is not rewritten; a later measurement of the same choice is
  a new decision that revises the first.
- The markdown export shows locators as recorded, so it is not share-safe when
  a producer records absolute paths. The specification asks producers for
  relative locators for exactly this reason.

**Prior art considered.** No documented survey of existing decision-provenance
or statistical-metadata vocabularies was performed for this decision, and no
novelty is claimed. The design follows the first producer's contract and the
existing PROV mapping in this specification.

## References

- Specification §3.6.1 (the object), §4.6 (validation), §6 (PROV terms),
  §7.3 (unknown-field preservation).
- ADR 002 (protocol-addition practice), ADR 005 (capture-time integrity),
  ADR 006 (project identity).
