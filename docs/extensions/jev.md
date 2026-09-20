# Jev advisory extension

TRACE can optionally ask [TypeSafe System One (Jev)](https://typesafe.ai/) to
classify whether an **existing** decision, annotation, or contribution event may
contain durable provenance worth human review.

The integration is deliberately read-only and advisory. It does not create
events, change a decision disposition, verify evidence, approve an AI's own
proposal, or make an admission/governance claim. A `candidate_for_human_review`
result means only that a human may want to inspect the source event.

## Enable it

Jev is opt-in. Put these values in the project's `.env` and restart TRACE:

For the direct TypeSafe API:

```dotenv
TRACE_JEV_ENABLED=true
TRACE_JEV_PROVIDER=typesafe
TYPESAFE_AI_API_KEY=...
# Optional; default is jev-latest
TRACE_JEV_MODEL=jev-latest
```

For Vercel AI Gateway, select it explicitly and install the pinned bridge
dependencies beside the packaged bridge:

```dotenv
TRACE_JEV_ENABLED=true
TRACE_JEV_PROVIDER=vercel_gateway
AI_GATEWAY_API_KEY=...
TRACE_JEV_MODEL=typesafe-ai/jev
# Optional; false unless explicitly enabled
TRACE_JEV_GATEWAY_ZERO_DATA_RETENTION=true
```

```bash
cd src/trace_mcp/extensions/jev
npm ci --omit=dev
```

The Gateway path uses the official Vercel AI SDK evaluation API
(`experimental_evaluate` with `gateway.evaluationModel`) and is pinned to
`typesafe-ai/jev`. The bridge normalizes Gateway boolean answers to the same
Noul audit shape as the direct provider, preserves TypeSafe per-question
distribution confidence, disables SDK retries, bounds response bytes, and
passes no arbitrary provider metadata back to TRACE. Node.js 22 or newer is
required. The dependency install is provider-specific; the direct provider
does not require Node or npm packages.

A provider key alone activates nothing. Credentials do not fall across
providers: direct mode reads only a TypeSafe key and Gateway mode reads only
`AI_GATEWAY_API_KEY`. `TRACE_LOCAL_ONLY=true`, including a
per-project registry privacy ratchet, blocks the call. Every attempted call is
recorded first in TRACE's egress ledger; if the ledger cannot be written, no
content is sent.

Call `trace_jev_assess_candidate` with a `session_id` and `event_id` (and a
`project` when the server is not pinned). TRACE loads that exact event and sends
only a bounded prose projection. Raw tool input/output and the event's
`decision.confidence` measurement are not sent.

The response preserves:

- selected choice and the full choice-probability distribution;
- Jev's distribution confidence, explicitly named as such;
- independent `bounded` and `durable` Noul probabilities;
- model, question-set and mapping versions, usage, latency, timestamp, and a
  digest of the event projection.

These signals are distinct from TRACE `decision.confidence`. The TRACE field is
a producer-recorded measured effect (estimate, interval, method, sample size,
and evidence); it must never be populated from Jev model confidence.
