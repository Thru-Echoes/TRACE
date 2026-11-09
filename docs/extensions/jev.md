# Jev advisory extension (experimental)

TRACE can optionally ask [TypeSafe System One (Jev)](https://typesafe.ai/) to
classify whether a **logged** decision, annotation, or contribution event may
contain durable provenance worth human review.

The integration is experimental and advisory. Its accuracy on TRACE events has not
been measured, and its thresholds are not calibrated. It is off by default and
nothing runs it automatically. It does not create events, change a decision
disposition, verify evidence, approve an AI's own proposal, or make an
admission or governance claim. A `candidate_for_human_review` result means only
that a human may want to inspect the source event.

It assesses what was logged. It cannot find a decision nobody recorded, and it does
not decide what gets logged.

## Before enabling it

Enabling it sends event text to TypeSafe, or to Vercel and TypeSafe. Do not enable
it for a project whose sessions hold client, student, personal, or health
information. `TRACE_LOCAL_ONLY=true`, including a per-project registry privacy
ratchet, blocks every call.

## Enable it

Put these values in the project's `.env` and restart TRACE.

For the direct TypeSafe API:

```dotenv
TRACE_JEV_ENABLED=true
TRACE_JEV_PROVIDER=typesafe
TYPESAFE_AI_API_KEY=...
# Optional; the default is a pinned version
TRACE_JEV_MODEL=jev-1.13.0
```

For Vercel AI Gateway:

```dotenv
TRACE_JEV_ENABLED=true
TRACE_JEV_PROVIDER=vercel_gateway
AI_GATEWAY_API_KEY=...
TRACE_JEV_MODEL=typesafe-ai/jev
# Optional; false unless explicitly enabled
TRACE_JEV_GATEWAY_ZERO_DATA_RETENTION=true
```

The Gateway route needs Node.js 22 or newer on the server's PATH and one setup
command, which installs pinned dependencies in `~/.trace/jev-bridge`:

```bash
trace-mcp jev setup
```

Run it again after upgrading TRACE if a call reports that the bridge is out of date.
The direct route does not need Node or npm packages.

The Gateway route uses the Vercel AI SDK evaluation API
(`experimental_evaluate` with `gateway.evaluationModel`) and is pinned to
`typesafe-ai/jev`. That route id does not name an immutable model version, so its
results carry a warning, as does `jev-latest` on the direct route. Do not compare
results across a change in the recorded `response_model`.

A provider key alone activates nothing. Credentials do not fall across providers:
direct mode reads only a TypeSafe key and Gateway mode reads only
`AI_GATEWAY_API_KEY`. Every attempted call is recorded first in TRACE's egress
ledger; if the ledger cannot be written, no content is sent.

## Use it

For one event, call `trace_jev_assess_candidate` with a `session_id` and `event_id`
(and a `project` when the server is not pinned).

For a whole session, run this from the project directory:

```bash
trace-mcp jev review trace_20260101_abcdef --dry-run
trace-mcp jev review trace_20260101_abcdef --max-events 50
```

The command must run from the directory of the session's own project, so that
project's `.env` and privacy settings apply. A session from another project is
refused.

`--dry-run` reports how many events would be sent and whether a real run is blocked,
and sends nothing. Events with a saved result for the same model are not sent again,
so repeating the command continues through a long session. An event that can never be
sent is skipped and listed. Any other failure stops the review, which reports what it
completed.

Only decisions a person has resolved are accepted. One still awaiting a person is
refused because seeing the advice first would sway them. When comparing Jev with human judgment, record
the human answers before looking at Jev's.

TRACE sends only a bounded prose projection of the event. Raw tool input and output
and the event's `decision.confidence` measurement are not sent.

## What a result contains

- selected choice and the full choice-probability distribution;
- Jev's distribution confidence, explicitly named as such;
- independent `bounded` and `durable` Noul probabilities;
- model, question-set and mapping versions, usage, latency, timestamp, and a digest
  of the event projection.

Each result is appended to `~/.trace/jev/<project_key>.jsonl`, so the advice can be
checked later. The record holds answers, digests, and event ids, not event text.
Session files are never changed.

These signals are distinct from TRACE `decision.confidence`. The TRACE field is a
producer-recorded measured effect (estimate, interval, method, sample size, and
evidence); it must never be populated from Jev model confidence.
