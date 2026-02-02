# TRACE User Guide

> **TRACE**: Transparent Research AI Collaboration Environment
> A protocol for documenting AI-human collaboration in scientific research

---

## Table of Contents

1. [Introduction](#introduction)
2. [Quick Start](#quick-start)
3. [Installation](#installation)
4. [Core Concepts](#core-concepts)
5. [Using TRACE](#using-trace)
6. [Understanding Metrics](#understanding-metrics)
7. [Best Practices](#best-practices)
8. [Exporting for Publication](#exporting-for-publication)
9. [Troubleshooting](#troubleshooting)

---

## Introduction

### What is TRACE?

TRACE (Transparent Research AI Collaboration Environment) is a protocol and toolset for documenting AI-human collaboration in research. It provides:

- **Audit Trail**: Complete log of all AI interactions and contributions
- **Attribution Metrics**: Quantifiable measures of who contributed what
- **Reproducibility Support**: Documentation for replicating AI-assisted workflows
- **Publication-Ready Reports**: Automatic generation of AI disclosure statements

### Why Use TRACE?

As AI becomes integral to scientific workflows, journals, funding agencies, and institutions increasingly require:

1. Disclosure of AI use in research
2. Clear attribution of AI vs. human contributions
3. Documentation supporting reproducibility
4. Evidence of human oversight and validation

TRACE provides the infrastructure to meet these requirements systematically.

### Key Features

| Feature | Description |
|---------|-------------|
| Session Tracking | Log work sessions with AI assistance |
| Code Attribution | Line-by-line tracking of AI vs. human authorship |
| Idea Provenance | Track origin of ideas (AI/human/collaborative) |
| Error Analysis | Monitor who makes and catches errors |
| Intervention Logging | Record human modifications to AI output |
| Automatic Metrics | Computed statistics for publication |

---

## Quick Start

### 5-Minute Setup

1. **Copy the template files** to your project:
   ```bash
   cp -r TRACE_template/mcp_server your_project/
   cp TRACE_template/trace.json your_project/
   cp TRACE_template/CLAUDE.md your_project/
   ```

2. **Configure MCP** in your project (create `.mcp.json`):
   ```json
   {
     "mcpServers": {
       "trace": {
         "command": "python",
         "args": ["mcp_server/server.py"],
         "env": {
           "TRACE_PATH": "./trace.json"
         }
       }
     }
   }
   ```

3. **Enable permissions** in `.claude/settings.local.json`:
   ```json
   {
     "permissions": {
       "allow": ["mcp__trace__*"]
     }
   }
   ```

4. **Start using TRACE** with Claude Code:
   - Start a session: "Start a TRACE session for data analysis"
   - Log contributions: Claude will automatically use TRACE tools
   - End session: "End the current TRACE session"
   - Get metrics: "Show me the TRACE metrics"

---

## Installation

### Requirements

- Python 3.10+
- MCP package: `pip install mcp anthropic`
- Claude Code CLI

### Full Installation Steps

1. **Install dependencies**:
   ```bash
   pip install mcp anthropic
   ```

2. **Set up directory structure**:
   ```
   your_project/
   ├── mcp_server/
   │   └── server.py      # TRACE MCP server
   ├── trace.json          # TRACE data file
   ├── CLAUDE.md           # Claude instructions
   ├── .mcp.json           # MCP configuration
   └── .claude/
       └── settings.local.json  # Permissions
   ```

3. **Configure `.mcp.json`**:
   ```json
   {
     "mcpServers": {
       "trace": {
         "command": "python",
         "args": ["mcp_server/server.py"],
         "env": {
           "TRACE_PATH": "./trace.json"
         }
       }
     }
   }
   ```

4. **Configure permissions** in `.claude/settings.local.json`:
   ```json
   {
     "permissions": {
       "allow": [
         "Bash(python:*)",
         "mcp__trace__*"
       ]
     }
   }
   ```

5. **Initialize `trace.json`** (or copy the template):
   - Edit project name, maintainers, etc.

6. **Verify installation**:
   ```bash
   cd your_project
   claude
   > "Check TRACE context"  # Should show project info
   ```

---

## Core Concepts

### Sessions

A **session** represents a work period with AI assistance. Sessions track:
- Start/end times
- Purpose and scientific stage
- Summary of accomplishments
- Reflection on AI helpfulness

**Scientific stages** align with the scientific method:
- `exploration` - Understanding problem/data
- `hypothesis` - Forming testable claims
- `data_collection` - Gathering data
- `analysis` - Running analyses
- `interpretation` - Making sense of results
- `validation` - Verifying findings
- `writing` - Documentation/papers

### Code Contributions

Track authorship at the line level:

| Type | Description |
|------|-------------|
| `ai_authored_lines` | Lines written entirely by AI |
| `human_authored_lines` | Lines written entirely by human |
| `ai_improved_lines` | Lines where AI improved human code |
| `human_improved_ai_lines` | Lines where human improved AI code |
| `collaborative_lines` | Lines developed jointly |

### Ideas

Track the origin of every significant idea:

| Source | Description |
|--------|-------------|
| `ai_suggested` | AI proposed the idea |
| `human` | Human proposed the idea |
| `collaborative` | Emerged from discussion |

Ideas are then evaluated:
- **Accepted**: Used as proposed
- **Modified**: Used with changes
- **Rejected**: Not used (with reason)

### Errors

Track both the source and detection of errors:

| Metric | What it measures |
|--------|------------------|
| AI errors, human-caught | Mistakes AI made that human found |
| Human errors, AI-caught | Mistakes human made that AI found |
| Error source rate | Who makes more errors |
| Catch rate | Who is better at finding errors |

### Interventions

Record when humans modify AI output:

| Type | Description |
|------|-------------|
| `correction` | Fixed an AI error |
| `override` | Changed a valid AI suggestion |
| `rejection` | Didn't use AI suggestion at all |
| `refinement` | Improved AI output |

---

## Using TRACE

### Starting a Session

At the beginning of each work period:

```
"Start a TRACE session for [purpose], we're in the [stage] stage"
```

Example:
```
"Start a TRACE session for implementing the topic model, we're in the analysis stage"
```

### During Work

#### Logging Code

After writing or modifying code:
```
"Log this code contribution:
- File: analysis.py
- Type: creation
- AI wrote about 80 lines, I wrote about 20
- Description: Topic modeling analysis functions"
```

#### Logging Ideas

When significant ideas emerge:
```
"Log an AI idea: Use coherence scores to select optimal topic count"
```

Or for human ideas:
```
"Log a human idea: Focus on articles from major art publications only"
```

#### Logging Errors

When errors are found:
```
"Log an error: The preprocessing didn't handle Unicode correctly.
AI made the error, I caught it during testing."
```

#### Logging Interventions

When modifying AI output:
```
"Log an intervention: I modified the AI's clustering approach
to use a different distance metric based on my domain knowledge."
```

### Ending a Session

At the end of each work period:
```
"End the TRACE session. We completed the preprocessing pipeline.
AI helpfulness: 4/5"
```

### Getting Metrics

At any time:
```
"Show me the TRACE metrics"
"What's the AI code acceptance rate?"
"How many AI ideas have been rejected?"
```

---

## Understanding Metrics

### Code Metrics

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| AI authorship % | ai_lines / total_lines | Portion of code written by AI |
| AI acceptance rate | accepted / (accepted + modified + rejected) | How often AI code is used as-is |
| AI modification rate | modified / total_ai_code | How often AI code needs changes |

### Error Metrics

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| AI error rate | ai_errors / total_errors | Portion of errors from AI |
| Human catch rate | ai_errors_caught_by_human / ai_errors | How well human reviews AI |
| AI catch rate | human_errors_caught_by_ai / human_errors | How well AI reviews human |

### Idea Metrics

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| AI idea acceptance | accepted / total_ai_ideas | How often AI ideas are used |
| AI idea rejection | rejected / total_ai_ideas | How often AI ideas are dropped |
| Idea contribution ratio | ai_ideas / total_ideas | AI's share of ideation |

### Intervention Metrics

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| Intervention rate | interventions / interactions | How often human overrides AI |
| Correction rate | corrections / interventions | Portion of interventions fixing errors |

---

## Best Practices

### 1. Log Consistently

- Start a session at the beginning of EVERY work period
- Log code contributions after EVERY significant change
- Log ideas as they emerge, not retroactively

### 2. Be Accurate About Attribution

- Don't inflate AI or human contributions
- If unsure, use "collaborative"
- Track modifications honestly

### 3. Track Rejections

- Rejected AI ideas are valuable data
- Always log WHY something was rejected
- This helps understand human-AI alignment

### 4. Use Scientific Stages

- Accurately reflect where you are in the research process
- This enables analysis of AI contribution by stage

### 5. Regular Metric Checks

- Run `trace_compute_metrics` periodically
- Review metrics to understand collaboration patterns
- Adjust workflow based on insights

---

## Exporting for Publication

### Generating Reports

```
"Export a TRACE report in markdown format"
```

This produces a publication-ready summary including:
- Executive summary
- Code authorship breakdown
- Idea provenance analysis
- Error analysis
- Intervention summary
- Detailed logs

### AI Disclosure Statement

Use this template for your methods section:

> **AI Assistance Disclosure**
>
> This research utilized AI assistance documented via the TRACE protocol (v1.0).
> Over [N] sessions totaling [X] hours, the AI assistant (Claude, Anthropic)
> contributed [Y]% of code ([Z] lines) and proposed [W] ideas ([V]% acceptance rate).
> Human researchers caught [A] AI-generated errors and made [B] interventions
> (corrections, overrides, or rejections) to AI output. Full TRACE logs are
> available in the supplementary materials.

### Supplementary Materials

Include in your repository:
- `trace.json` - Full TRACE data
- `trace_report.md` - Generated report
- Link to TRACE protocol documentation

---

## Troubleshooting

### MCP Server Not Starting

1. Check Python version: `python --version` (need 3.10+)
2. Verify MCP installed: `pip show mcp`
3. Check `.mcp.json` path is correct
4. Look at Claude Code logs for errors

### TRACE Tools Not Available

1. Verify permissions in `.claude/settings.local.json`
2. Restart Claude Code after config changes
3. Check MCP server is running

### Data Not Saving

1. Verify `TRACE_PATH` environment variable
2. Check file permissions on `trace.json`
3. Ensure directory exists

### Metrics Show Null/Zero

1. Make sure to log contributions before computing metrics
2. Run `trace_compute_metrics` to update
3. Check that logged entries have required fields

---

## Additional Resources

- **TRACE Protocol Paper**: [link to your publication]
- **GitHub Repository**: [link to repo]
- **Claude Code Documentation**: https://claude.com/claude-code
- **MCP Documentation**: https://modelcontextprotocol.io

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-29 | Initial release |

---

## Contact

For questions, issues, or contributions:
- GitHub Issues: [your repo]
- Email: [your email]
