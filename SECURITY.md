# Security Policy

## Supported versions

TRACE follows [Semantic Versioning](https://semver.org/). Security fixes are
applied to the latest released minor version. We do not backport fixes to
older minor lines.

| Version | Supported          |
|---------|--------------------|
| 0.4.x   | :white_check_mark: |
| 0.3.x   | :x: (upgrade — 0.4.x is wire-compatible with 0.3.x sessions) |
| < 0.3   | :x:                |

## Reporting a vulnerability

**Please report security issues privately. Do not open a public GitHub issue
for a suspected vulnerability.**

Two private channels are available:

1. **GitHub private security advisory** (preferred): open a draft advisory at
   <https://github.com/Thru-Echoes/TRACE/security/advisories/new>. This keeps
   the report private until a fix is coordinated.
2. **Email**: `omuellerklein@berkeley.edu` with a subject line beginning
   `[TRACE SECURITY]`.

Please include:

- A description of the issue and its potential impact.
- The TRACE version (`trace-mcp --version` or the `version` in
  `pyproject.toml`) and your Python version and OS.
- Minimal steps to reproduce, ideally without any private or confidential
  data in the report.

### Response expectations

This is a research-stage, single-maintainer project, so timelines are
best-effort rather than contractual:

- **Acknowledgement** of a report within **5 business days**.
- An initial **assessment** (confirmed / needs-more-info / not-applicable)
  within **10 business days**.
- For confirmed issues, a coordinated fix and disclosure timeline agreed with
  the reporter. We aim to ship a fix or mitigation within **30 days** of
  confirmation for high-severity issues.

We will credit reporters in the release notes unless anonymity is requested.

## Scope and threat model

TRACE is a **local-first** MCP server. Understanding what it does and does not
do is the fastest way to reason about its attack surface.

### What TRACE stores, and where

- Provenance data is written as one self-contained, pretty-printed JSON file
  per session under `~/.trace/sessions/` (overridable via environment).
- The optional `trace-learn` extension persists cross-session knowledge under
  `~/.trace/knowledge/` (overridable via `TRACE_KNOWLEDGE_DIR`).
- All writes use an atomic temp-file-plus-`os.replace` pattern. There is no
  database server and no listening network socket — TRACE speaks the MCP
  **stdio** transport only, driven by the parent AI client process.

### Network behavior

- **Core makes no outbound network calls.** The core runtime depends only on
  `mcp` and `pydantic`.
- The optional `trace-learn` extension will call the **OpenAI API** for
  LLM-assisted matching/extraction **only if** the LLM backend is explicitly
  configured and an API key is present. With no key configured, it falls back
  to local lexical matching (BM25 / Jaccard) and makes no network calls.
- If you enable the LLM backend, session text (annotations, decisions,
  contributions) may be sent to OpenAI. Do not enable it for sessions that
  contain confidential or regulated data you cannot share with a third party.

### In scope

- Path traversal or write-escape from the `~/.trace/` storage roots.
- Deserialization, schema-validation, or injection issues when loading
  session/knowledge JSON.
- Unintended data exfiltration from the core runtime (core must remain
  network-silent).
- Privilege or sandbox escapes via the `trace-mcp-init` adapter installer
  (it writes hook scripts and config into a consumer project).
- Secrets handling for the optional OpenAI integration.

### Out of scope

- Security of the AI client (e.g. Claude Code, Cursor, Codex) that hosts the
  MCP connection, and of any other domain MCP servers running alongside TRACE.
- The OpenAI API itself and its data-handling policies.
- Trust in the provenance content: TRACE records what the client logs. It is
  an honest-record tool, not a tamper-evident attestation system, and does not
  cryptographically prove that a logged event reflects reality.
- Vulnerabilities in third-party dependencies, which should be reported
  upstream (we will still track and bump affected pins).

## Handling sensitive data in reports

Provenance sessions can contain research content. When reporting an issue,
redact or synthesize any confidential, personal, or business data — a
structural description of the bug is preferred over a real session dump.
