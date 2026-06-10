---
title: 'TRACE: An MCP protocol for decision-level provenance in AI-assisted research'
tags:
  - Python
  - provenance
  - artificial intelligence
  - reproducibility
  - Model Context Protocol
  - research integrity
authors:
  - name: Oliver Muellerklein
    orcid: <ORCID-TBD>
    affiliation: 1
affiliations:
  - name: Department of Environmental Science, Policy, and Management, University of California, Berkeley
    index: 1
date: 4 June 2026
bibliography: paper.bib
---

# Summary

`TRACE` (Transparent Recording of AI-assisted Collaboration Experiments) is a
Model Context Protocol (MCP) [@anthropic_mcp] server that produces a
standardized, human-readable audit trail for AI-assisted research and software
workflows. As researchers increasingly delegate methodological choices —
selecting a statistical method, setting hyperparameters, deciding how to handle
messy data — to large-language-model agents, the scientific record loses the
ability to distinguish a researcher's deliberate methodology from an AI's
default suggestion that was never critically examined. `TRACE` records
**decision-level provenance**: who *proposed* each step, who *accepted, revised,
or rejected* it, the rationale, and how the approach evolved over a session.

`TRACE` runs as a sidecar alongside a workflow's domain MCP servers. It does not
proxy or intercept calls; the AI client explicitly logs five event types — tool
calls, decisions, annotations, contributions, and state changes — to one
self-contained, git-diffable JSON file per session. Decisions form a provenance
DAG rather than a flat log: every decision carries an actor, a disposition
(proposed → accepted / revised / rejected), a `suggestion_type`
(proactive / requested / collaborative), and an optional link to the prior
decision it revises. Contributions separate *intellectual direction* (who had
the idea) from *execution* (who did the work), a distinction existing attribution
norms cannot express. Sessions export to W3C PROV [@w3c_prov] as JSON-LD
(PROV-LD), so records interoperate with established provenance tooling.
The core is implemented in Python 3.11+ with Pydantic v2 [@pydantic] and depends
only on `mcp` and `pydantic`.

# Statement of need

Existing AI-observability stacks — LangSmith [@langsmith], Langfuse
[@langfuse], MLflow [@mlflow], and the OpenTelemetry GenAI semantic conventions
[@otel_genai] — capture **call-level** traces: what tool an agent invoked, with
what inputs, and what came back. They do not capture **decision-level
provenance**: who proposed each analytical step, whether a human reviewed it,
and what alternatives were rejected. In a preliminary rubric audit of agentic-AI
deployments in environmental science, analytical-decision provenance scored
markedly lower than basic workflow description, and several recently published
papers showed discrepancies such as model details that did not match the cited
models, or analyses that could not be reproduced from the reported description.

The need is also moving from norm to regulation. The EU AI Act
[@eu_ai_act], California's SB 942 Transparency in Frontier Artificial
Intelligence Act [@ca_sb942], the U.S. FDA's predetermined change control plan
guidance [@fda_pccp], the NIST AI Risk Management Framework [@nist_ai_rmf], and
ISO/IEC 42001:2023 [@iso_42001] each require some form of decision-process
documentation for AI systems. `TRACE` is designed so that this documentation is
a workflow byproduct rather than an after-the-fact reconstruction.

`TRACE` targets researchers and research engineers who use AI agents in
scientific and data-analysis workflows and who must later defend, reproduce, or
audit the resulting record. Across an early four-week deployment it was used in
five research workflows spanning computational art, corporate-sustainability
disclosure, and environmental discourse analysis, recording hundreds of events
and decisions; the dominant collaboration pattern observed was human direction
with AI execution — precisely the pattern that flat call logs and conventional
authorship statements fail to describe.

# Functionality and state of the field

`TRACE` is technology-agnostic at the specification level — its data model
defines *what* to record, not *how* to collect it — while shipping a reference
MCP implementation. It complements rather than replaces call-level observability:
where LangSmith [@langsmith], Langfuse [@langfuse], and OpenTelemetry
[@otel_genai] answer "what did the agent call?", `TRACE` answers "who decided
this, and why?". Where workflow- and experiment-provenance systems such as MLflow
[@mlflow] track artifacts, parameters, and runs, they do not model the
proposal–resolution–revision lifecycle of a human–AI decision or the
direction-versus-execution split of a contribution. By mapping its model onto
W3C PROV [@w3c_prov], `TRACE` keeps these records portable to the broader
provenance ecosystem. The result is a layer that sits above instrumentation: a
provenance DAG of decisions, corrections, and attributions that a future reader —
a co-author, a reviewer, or an auditor — can reconstruct from a single
human-readable file.

# Acknowledgements

We thank collaborators and early adopters across the deploying research
workflows for feedback during development. <PLACEHOLDER: add specific
acknowledgements, advisors, and any funding/grant numbers.>

# References
