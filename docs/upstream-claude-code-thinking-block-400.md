# Upstream bug report: Claude Code 400 on large interleaved-thinking assistant turns

> Public-safe report intended for the Anthropic / Claude Code team. It
> describes a crash observed while developing TRACE (an MCP server) but
> contains no transcript content, identities, or project data — only the
> structural shape of the failing request.

## Summary

The Claude Code CLI hard-fails with:

```
API Error: 400 messages.N.content.M: `thinking` or `redacted_thinking`
blocks in the latest assistant message cannot be modified
```

when a single assistant turn running under **interleaved extended thinking**
accumulates a large number of content blocks (many thinking blocks
interleaved with many `tool_use` blocks). Once the error fires, the session is
effectively **bricked**: every retry re-sends the same corrupted latest
assistant message, so the 400 reproduces deterministically and the session
cannot make forward progress.

## Environment

- **Client**: Claude Code CLI, observed on `2.1.152` and `2.1.154`.
- **Models**: `opus-4-7` and `opus-4-8` (both reproduce).
- **OS**: macOS.
- **Mode**: interleaved extended thinking enabled.
- **Workload shape**: an MCP-heavy turn that batches many tool calls (and at
  least one parallel subagent dispatch) within a single assistant turn.

## Minimal reproduction (structural)

The trigger is **block count in one interleaved-thinking assistant turn**, not
the semantic content of any block.

1. Run Claude Code with interleaved extended thinking on.
2. Issue a prompt that causes the assistant to emit, in **one** turn, an eager
   "bootstrap" that batches a large number of MCP `tool_use` blocks plus a
   parallel dispatch — i.e. the model interleaves thinking and tool calls
   heavily before yielding.
3. Observed shape at failure: roughly **17 `thinking` blocks + ~34 `tool_use`
   blocks ≈ 52 content blocks** in a single assistant message.
4. On the next request assembly, the API returns the 400 above.

Crash position varied across occurrences: in one case the error pointed at
**content index 17**, in another at an accumulated **index 100**. This
variance is consistent with the failure being tied to the volume / re-handling
of signed thinking blocks rather than to a fixed offset.

### What was ruled out

- **Out-of-order tool results are NOT the cause.** The `tool_result` blocks
  were verified to be returned in the same order their corresponding
  `tool_use` blocks were issued. Resolution ordering is correct.
- The controlling variable is the **sheer number of content blocks** (in
  particular signed `thinking` blocks) in the single latest interleaved-thinking
  assistant turn.

## Hypothesised mechanism

Extended-thinking blocks are returned **signed**, and the API requires the
`thinking` / `redacted_thinking` blocks of the latest assistant message to be
passed back **byte-for-byte unmodified**. The hypothesis is that, when
assembling the next request, the client **re-serializes and/or re-orders** the
content blocks of the latest assistant message (e.g. normalizing JSON,
regrouping blocks, or merging adjacent fragments). At high block counts this
perturbs at least one signed thinking block enough to invalidate its
signature, and the server rejects the whole message with the "cannot be
modified" 400. Because the perturbation is in the persisted message, every
resend reproduces it.

## Impact

- **Session-bricking.** The failure is not transient; the corrupted latest
  assistant message is replayed on every retry, so the only recovery is to
  abandon or surgically edit the session.
- Most likely to bite **MCP-heavy / agentic** workflows, which naturally
  produce many tool calls and interleaved thinking blocks in a single turn —
  exactly the regime this protocol's users operate in.

## Ask

1. **Validate and preserve byte-fidelity** of `thinking` /
   `redacted_thinking` blocks across large multi-block assistant turns when
   the client re-assembles the request — do not re-serialize or re-order
   signed blocks of the latest assistant message.
2. If a signed block genuinely cannot be preserved, **surface a recoverable
   error** (or auto-strip/auto-recover the offending turn) instead of a hard,
   session-bricking 400.
3. Optionally, **cap or chunk** the number of interleaved thinking blocks
   emitted in a single turn so the request stays within a known-good size.
