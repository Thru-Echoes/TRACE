# TRACE Audit Protocol Skill

## When to Activate
Activate this skill for ANY scientific workflow, experiment, data analysis,
literature analysis, or multi-step technical research task.

## Required Behavior

### Session Management
- At the START of any workflow, call `trace_start_session` with:
  - project name (match existing project names when continuing work)
  - experiment ID if known
  - brief description of the goal
  - participant list (include yourself as AI assistant and the researcher)
- At the END of any workflow, call `trace_end_session` with a summary
  of what was accomplished, key findings, and any open questions.

### Tool Call Logging
- AFTER every tool call to another MCP server, call `trace_log_tool_call`
  with the server name, tool name, inputs, outputs, status, and duration.
- Include a brief `reasoning` note explaining why you called this tool.
- For failed tool calls, always log them with status "error" and the
  error message — failures are often the most valuable audit data.

### Decision Logging
- Before making any SIGNIFICANT methodological choice, call
  `trace_propose_decision` with:
  - Clear description of what you're deciding
  - Specific, technical rationale (not "this seemed best")
  - Yourself as the proposer
- Significant choices include: which method/algorithm to use, which
  parameters or thresholds to set, which data to include or exclude,
  how to handle missing or messy data, how to preprocess text,
  which model or embedding to use, how to interpret ambiguous results,
  when to stop iterating.
- WAIT for human confirmation before proceeding with proposed decisions
  when the decision is consequential (affects results, costs significant
  compute time, or is hard to reverse).
- When the human responds, call `trace_resolve_decision` with their
  disposition (accepted/revised/rejected) and their reasoning.

### Annotations
- When you encounter UNEXPECTED results or behavior, log a "gotcha"
  annotation. Common in environmental/ecology data: encoding issues,
  inconsistent date formats, missing metadata, changed APIs.
- When you learn something that would help in future sessions, log a
  "learning" annotation. Include enough context to be useful months later.
- When you notice something interesting but not immediately actionable,
  log an "observation" annotation.
- When something needs follow-up, log a "todo" annotation.

### State Changes
- Log any changes to models, embeddings, preprocessing pipelines,
  API versions, corpora, or analysis parameters using
  `trace_log_state_change`. Include the old and new values.

## Principles
- Be thorough but not noisy. Log methodology decisions, not trivial ones
  (e.g., don't log "chose to print a status message").
- Decision rationales should be specific and technical:
    BAD: "this seemed like a good threshold"
    GOOD: "0.80 cosine similarity gives F1=0.78 on our 30-pair validation
           set; lowering to 0.75 adds 40% more hits but drops precision to 0.61"
- Tag events with relevant domain terms for later searchability
  (e.g., "ipcc", "embeddings", "preprocessing", "unicode").
- When in doubt, log it. It's easier to filter logs than to reconstruct
  what happened from memory.
- For messy data situations (common in environmental science), always log
  the data quality issue as a gotcha AND the decision about how to handle it.
