# TRACE — onboarding

For someone arriving at this project as a **developer** or as a **user** of the
tool. It assumes no prior context. Read [README.md](../README.md) for the
reference tables and [docs/specification.md](specification.md) for the
authoritative data model; this file is the map that makes those readable.

**Current state**: package `0.5.1`, protocol/schema `0.5.1`, 22 MCP tools, 12
registered invariants. Not published to PyPI (see [Distribution](#distribution)).

---

## 1. What TRACE is

TRACE records **how a piece of work was decided**, not just what the work
produced. When a human and an AI assistant collaborate, the artifacts survive —
the code, the paper, the analysis — and the reasoning evaporates: who proposed
an approach, who accepted or overrode it, what was tried and rejected, who
caught which mistake. TRACE captures that as it happens and stores it as
structured, machine-readable records.

It is an **MCP server** (Model Context Protocol), so an AI assistant that speaks
MCP can call its tools mid-conversation. It is also a **specification**: any
tool producing documents that validate against `schemas/trace-v0.5.json`
implements the standard, with no dependency on MCP, Python, or this
implementation.

Two things it is deliberately not: it is not a transcript recorder (it captures
decisions, not every keystroke), and it does not evaluate or score anybody's
work.

## 2. The mental model

```
   session  ──►  events  ──►  storage  ──►  query / export
   (a unit of      (five        (JSON on      (tools, CLI,
    work)          types)        disk)         PROV-JSON-LD)
```

A **session** is one unit of work — a task, an investigation, a workflow. It is
opened explicitly (`trace_start_session`), accumulates events, and is closed
with a summary (`trace_end_session`). Everything is scoped to a **project**.

**Five event types** carry everything:

| Event | Records | Key fields |
|---|---|---|
| `decision` | A methodological choice | description, rationale, disposition, `revises_event_id` |
| `annotation` | A learning, gotcha, correction, observation, todo, question, or discovery | category, content, `corrects_event_ids` |
| `contribution` | A work product | description, `direction`, `execution`, artifact |
| `tool_call` | An invocation of another tool or agent | server, name, input, output, status, `host`, `parent_event_id` |
| `state_change` | An environment or configuration change | field, old_value, new_value |

## 3. The attribution model

This is the part that makes TRACE different from a log, and the part newcomers
most often get wrong.

**Decisions have a lifecycle.** They are *proposed* before acting and *resolved*
afterwards — accepted, revised, or rejected. Proposer and resolver are recorded
separately, and the rule is that `proposed_by` names whoever authored the
proposal's *content*, not whoever spoke the sentence that settled it. A human
asking "what should we do here?", an AI proposing an approach, and the human
saying "go ahead" is recorded as `proposed_by=ai`, `resolved_by=human`. An AI
never resolves its own proposal.

**Contributions separate direction from execution.** `direction` is who had the
idea; `execution` is who did the work. "The human decided, the AI typed" and
"the AI suggested, the human wrote it" are different facts and are stored
differently.

**Rejections are data.** When an alternative is considered and dropped, that is
logged as its own decision with `disposition: rejected` and a note. A record
showing only what was accepted hides the reasoning that made it the choice.

The absolute rule, stated in every prompt that drives this system: **never
fabricate, falsify, or retroactively alter TRACE data. A sparse honest record
beats a dense fabricated one.**

## 4. How it runs

Each project gets a `.mcp.json` declaring the server:

```json
{"mcpServers": {"trace": {
  "command": "uvx",
  "args": ["--from", "/path/to/TRACE", "--with", "openai", "--with", "numpy",
           "--with", "model2vec", "--refresh-package", "trace-mcp", "trace-mcp"],
  "env": {"TRACE_PROJECT": "your-project-key"}
}}}
```

Three details that are load-bearing, each of which has caused a real outage:

- **The three `--with` extras** are what make this a 22-tool server. Without
  them the knowledge extension does not load and you silently get 17 tools.
- **`--from <path>`** builds from a checkout. `uvx --from trace-mcp` would fetch
  an *unrelated* package of that name from PyPI and execute it.
- **`env.TRACE_PROJECT`** pins the process to one project. Unpinned, session
  creation is refused outright — which a client reports as "TRACE tools
  unavailable".

`trace-mcp-init` writes all of this for you.

## 5. Project identity

A project is identified by a **canonical key**, never by its free-text label.
`My Project`, `my-project`, and `my_project` all reduce to `my-project`, so case
and separator variants cannot split one project into three stores.

Identity lives in three places that must agree, all written by `trace-mcp-init`:

| Site | Read by |
|---|---|
| `.claude/trace.project` | the host hooks (highest precedence) |
| `.mcp.json` → `env.TRACE_PROJECT` | the server process |
| `CLAUDE.md` → `TRACE project name: "..."` | a model reading the repository |

Plus `~/.trace/projects.json`, the alias registry, which maps historical labels
onto keys.

**A mislabelled project is repaired by adding an alias, never by editing a
captured record.** Capture records are append-only in spirit: rewriting history
to make it tidy would destroy the only thing the record is for. `trace-mcp
identity` (`snapshot`, `scan`, `apply`, `check`, `merge-stores`, `adopt`,
`bundle`) exists for exactly this.

## 6. Hooks vs bare

This is a distinction about **how much a project enforces the protocol**, and it
is a real choice, not an oversight.

A project always needs `.mcp.json` — that makes the 22 tools available. On top
of that, `trace-mcp-init` can install four **hook scripts** into
`.claude/hooks/` plus their registrations in `.claude/settings.json`:

| Hook | Fires on | Does |
|---|---|---|
| `session-reminder.sh` | session start | reminds you to open a TRACE session if none is active |
| `prompt-reminder.sh` | each prompt | nudges after several turns with no session (rate-limited) |
| `pretool-guard.sh` | before an edit or write | warns — or blocks, in `strict` mode — when no session is active |
| `decision-audit.sh` | after `trace_end_session` | echoes the attribution audit into the conversation |

- **A hooked project** actively pushes you to record. The assistant is reminded,
  edits without a session are flagged, and the end-of-session attribution audit
  is surfaced where someone will read it.
- **A bare project** has the tools and nothing else. Recording happens only when
  the assistant remembers or you ask. Nothing warns you when it doesn't.

Bare is legitimate — a scratch project, a repo where the noise is not worth it,
a host that is not Claude Code. But it should be a *decision*, because a bare
project's silence is indistinguishable from a hooked project that is working
perfectly. `trace-mcp doctor` reports a bare project's missing hooks as
failures, which is the right default for a fleet where most projects are meant
to be hooked; a deliberately bare project will show as red until that policy is
recorded somewhere.

`TRACE_GUARD` controls the edit guard: `soft` (warn, default), `strict` (block),
`off`.

## 7. The knowledge extension (trace-learn)

Optional by governance, on by default. It extracts learnings from finished
sessions and surfaces relevant ones later, so a lesson learned in one session is
available in the next.

- **Local-first.** Matching uses BM25 or local `model2vec` embeddings. Cloud
  scoring happens only with `TRACE_LLM_ENABLED=true` *and* a key; cloud
  embeddings need an explicit `TRACE_EMBEDDING_BACKEND=openai`. A key sitting on
  the machine activates nothing by itself.
- **The key belongs in the project's own `.env`.** Per-project credentials keep
  a leaked or exhausted key from covering every project on the machine.
  `~/.trace/.env` is a fallback, and TRACE says so at session start when a
  project is borrowing it.
- **`TRACE_LOCAL_ONLY=true`** is a no-egress kill switch, and it ratchets: any
  source can turn it on, none can turn it off.
- Every cloud call is recorded in `~/.trace/egress.jsonl` — the *fact* of the
  call (provider, endpoint, model, purpose, count), never the content.

The core must work with this extension absent; deleting it must leave a fully
functional provenance system (ADR-003).

## 8. Where data lives

Everything is on your machine, under `~/.trace/`:

| Path | Holds |
|---|---|
| `sessions/` | one JSON document per session |
| `knowledge/` | one store per project key, for the learn extension |
| `projects.json` | the alias registry |
| `egress.jsonl` | the cloud-egress ledger |
| `scratchpads/`, `runtime/`, `backups/` | session summaries, hook state, snapshots |

Nothing is uploaded. Override any of it with `TRACE_SESSIONS_DIR`,
`TRACE_KNOWLEDGE_DIR`, `TRACE_REGISTRY_PATH`.

## 9. The conformance layer

The recurring failure in this project has one shape: **the source tree is green
and the deployed system is rotten.** Every instance was found by hand, months
late — a hook matcher that never fired in most projects, hook copies frozen at
an old release, pins that were never minted, a cached wheel serving an old build
under a correct-looking config.

Two commands close that gap by checking a *deployment* against the artifacts the
installed build actually ships:

```bash
trace-mcp doctor [DIR] [--live]        # one project
trace-mcp fleet-check [ROOTS...]       # every project under those roots
```

`doctor` checks the launch config, the hook deployment, and the three pin sites;
`--live` additionally starts the project's own configured server and verifies
the version and tool count it actually serves. `fleet-check` runs that across a
tree and **rolls failures up by check** — the view that turns "23 projects are
broken" into "one defect, 23 times". Exit codes: `0` clean, `1` findings, `2`
usage error.

The expectations are *derived* from shipped assets — hook filenames and their
version stamp read from the adapter's own files, the matcher built from the
server key, the version from `__version__` — because a restated expectation is
one more copy to rot.

## 10. Invariants

[`docs/INVARIANTS.md`](INVARIANTS.md) is the registry of correctness properties,
each with its **exhaustive site-set** and the test that enforces it. Twelve are
registered (INV-1 … INV-12): locked session writes, completed-session
immutability, decision validation, canonical-key scoping, egress attestation,
project/session coherence, registry writes, the knowledge lock, learn-tool
scoping, version-declaration consistency, conformance of a fresh install, and
learning-id uniqueness.

The point is the defect *class*: every serious bug here has been *an invariant
enforced in one place but not uniformly*. `tests/test_invariants.py` fails when
a **new** site appears that isn't registered. When you establish an invariant,
enumerate its sites and add the guard — do not trust reviewers to re-spot it.

## 11. Working on TRACE

```bash
uv pip install -e ".[dev]"
uv run pytest                                  # full suite (1400+ tests)
uv run ruff format --check . && uv run ruff check .   # two SEPARATE CI gates
uv run pyright src/                            # type check
uv run pytest tests/test_invariants.py         # invariant guard
uv build && uv run pytest tests/test_packaging_artifacts.py   # the shipped artifact
```

House rules that are not negotiable:

- **Branch + PR always.** Never push to `main`.
- **TDD.** Failing test first, watch it fail for the right reason.
- **Never weaken a guard to make a test pass.**
- **Test the artifact you ship, in the form you ship it.** The dev launcher
  builds differently than the published wheel.
- **Write self-contained.** Commit messages, PR bodies, comments, and docs must
  make sense to someone with no access to the conversation that produced them.
  No session narrative, no AI-attribution footers.
- **Keep `main` checked out.** Consumers build from this working tree, so a
  half-finished branch left checked out ships to every project on the machine.

Review depth scales with risk: `/code-review` on everything; add
`/security-review` and the built-artifact test when the diff touches a write
path, packaging, or an invariant; a deep multi-agent pass only at inflection
points (pre-release, storage or schema changes).

## 12. What changed recently

Eight merged pull requests, most of them closing the same class of defect.

| PR | Change | Why it mattered |
|---|---|---|
| #49 | Learn tools enforce the project pin; recall stopped returning unranked listings | A pinned server accepted any foreign project label on the knowledge surface — a documented guarantee that was false. Recall silently returned insertion-ordered results when called with the wrong argument name. |
| #50 | The decision-audit hook matcher is derived from the server key | The shipped template used a bare tool name. Hosts match the full namespaced name, so the hook was dead in most deployed projects — and every new install redeployed the bug. |
| #51, #52, #53 | Removed sync-conflict duplicates; the MCP handshake reports TRACE's own version; the dependency-confusion warning moved to Quick Start | Housekeeping with teeth: the handshake had been reporting the MCP library's version, and the PyPI name warning was buried. |
| #54 | `trace-mcp doctor` + INV-11 | First tool that checks a *deployment* rather than the source tree. INV-11 binds the installer's output to the checker's expectations, so template rot fails at PR time. |
| #55 | Per-project OpenAI keys; missing or refused keys are loud | Precedence was inverted, so a key placed in a project was silently ignored in favour of a machine-wide one. A missing key produced keyword-ranked results with nothing saying the semantic path had been skipped. |
| #56 | `trace-mcp fleet-check` | Turns the per-project check into a fleet sweep with a by-check rollup. |

Running `fleet-check` across a real 24-project fleet after all of this: **1
project clean**. Missing identity pins in 23, stale hook copies in 16, the dead
matcher still deployed in 14, no hooks at all in 7. The source tree was healthy
throughout. That gap is the current work.

## 13. Vocabulary

| Term | Meaning |
|---|---|
| **session** | One unit of work; the container for events |
| **event** | One record: decision, annotation, contribution, tool_call, state_change |
| **direction / execution** | Who had the idea / who did the work |
| **proposed / resolved** | A decision's two halves, with separate actors |
| **disposition** | How a decision ended: accepted, revised, rejected |
| **canonical key** | The stable identifier for a project, derived from its label |
| **pin** | A recorded statement of which project a directory belongs to |
| **registry** | `~/.trace/projects.json`, mapping labels and aliases to keys |
| **hooked / bare** | A project with host-side enforcement installed, or without |
| **adapter** | A host integration that installs hooks (Claude Code today) |
| **extension** | Optional add-on loaded at server start; `trace-learn` is the one shipped |
| **egress ledger** | Append-only record of cloud calls — the fact, never the content |
| **invariant** | A correctness property with an enumerated site-set and a guard |
| **conformance** | Whether a *deployment* matches the build's expectations |
| **finding** | One check's outcome: pass, fail, or skip (skip = not evaluated) |
| **ratchet** | A setting any source can tighten and none can loosen |
| **attribution audit** | The end-of-session summary of who contributed what |

## Distribution

Not on PyPI. The name `trace-mcp` there belongs to an unrelated project, so
`uvx --from trace-mcp` would download and run a stranger's code. Install from a
checkout or a git ref. `trace-mcp-init` refuses to write the bare name.

## Where to go next

| You want | Read |
|---|---|
| The data model, normatively | [docs/specification.md](specification.md) |
| Worked logging examples | [docs/examples.md](examples.md) |
| Why a design is the way it is | [docs/adr/](adr/) |
| The correctness properties | [docs/INVARIANTS.md](INVARIANTS.md) |
| The knowledge extension | [docs/extensions/trace-learn.md](extensions/trace-learn.md) |
| A plain-language explanation | [docs/WHAT-IS-TRACE.md](WHAT-IS-TRACE.md) |
