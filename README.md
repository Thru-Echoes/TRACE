# TRACE - Transparent Research AI Collaboration Environment

> A protocol for documenting AI-human collaboration in scientific research

[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by/4.0/)
[![Protocol Version](https://img.shields.io/badge/Protocol-v1.0-blue.svg)]()

---

## Overview

**TRACE** (Transparent Research AI Collaboration Environment) is a protocol and toolset for systematically documenting AI contributions to research. As AI assistants become integral to scientific workflows, TRACE provides the infrastructure for:

- **Attribution**: Track who contributed what (AI vs. human)
- **Reproducibility**: Document AI-assisted methods for replication
- **Accountability**: Ensure appropriate human oversight
- **Transparency**: Meet disclosure requirements for publication

---

## Key Features

| Feature | What it tracks | Why it matters |
|---------|---------------|----------------|
| **Code Authorship** | Lines written/improved by AI vs. human | Attribution & reproducibility |
| **Idea Provenance** | Origin of ideas (AI/human/collaborative) | Intellectual contribution credit |
| **Error Attribution** | Who made and who caught errors | Quality assurance metrics |
| **Interventions** | Human modifications to AI output | Oversight documentation |
| **Sessions** | Work periods with AI assistance | Time tracking & workflow analysis |
| **Automatic Metrics** | Computed statistics | Publication-ready disclosures |

---

## Quick Start

### 1. Copy to your project

```bash
cp -r TRACE_template/* your_project/
```

### 2. Install dependencies

```bash
pip install mcp anthropic
```

### 3. Start using TRACE

With Claude Code:
```
> Start a TRACE session for implementing the data analysis
> ... do your work ...
> End the current TRACE session
> Show me the TRACE metrics
```

---

## Directory Structure

```
TRACE_template/
├── README.md                 # This file
├── CLAUDE.md                 # Instructions for Claude Code
├── USER_GUIDE.md             # Detailed user guide
├── TRACE_PROTOCOL.md         # Formal protocol specification
├── trace.json                # Template TRACE data file
├── .mcp.json                 # MCP server configuration
├── .claude/
│   └── settings.local.json   # Claude Code permissions
├── mcp_server/
│   ├── server.py             # TRACE MCP server
│   └── analysis.py           # Analysis utilities
└── examples/
    └── example_trace.json    # Example with sample data
```

---

## Core Concepts

### Sessions
Bounded work periods with AI assistance. Track purpose, scientific stage, duration, and reflection.

### Code Contributions
File-level records with line-by-line authorship:
- `ai_authored_lines`: Written by AI
- `human_authored_lines`: Written by human
- `human_improved_ai_lines`: AI code modified by human
- `ai_improved_lines`: Human code improved by AI

### Ideas
Every significant idea with origin tracking:
- `ai_suggested`: AI proposed it
- `human`: Human proposed it
- `collaborative`: Emerged together

### Errors
Attribution for both creation and detection:
- Who made the error (AI or human)
- Who caught the error (AI, human, or automated test)

### Interventions
Human modifications to AI output:
- Corrections (fixing errors)
- Overrides (changing valid suggestions)
- Rejections (not using suggestions)
- Refinements (improving suggestions)

---

## Computed Metrics

TRACE automatically computes:

| Category | Metrics |
|----------|---------|
| **Code** | AI authorship %, acceptance rate, modification rate |
| **Ideas** | AI contribution %, acceptance rate, rejection rate |
| **Errors** | AI error rate, human catch rate, AI catch rate |
| **Interventions** | Total count, rate, breakdown by type |
| **Sessions** | Count, total time, average duration |

---

## MCP Server Tools

The TRACE MCP server provides these tool categories:

| Category | Tools |
|----------|-------|
| Session | `trace_start_session`, `trace_end_session` |
| Code | `trace_log_code` |
| Ideas | `trace_log_idea`, `trace_evaluate_idea` |
| Errors | `trace_log_error` |
| Interventions | `trace_log_intervention` |
| Knowledge | `trace_add_decision`, `trace_add_learning`, `trace_add_gotcha` |
| Metrics | `trace_get_metrics`, `trace_compute_metrics` |
| Export | `trace_export_report` |

---

## For Publication

### AI Disclosure Statement

```
This research utilized AI assistance documented via the TRACE protocol (v1.0).
Over [N] sessions totaling [X] hours, the AI assistant (Claude, Anthropic)
contributed [Y]% of code ([Z] lines) and proposed [W] ideas ([V]% acceptance rate).
Human researchers caught [A] AI-generated errors and made [B] interventions.
Full TRACE logs are available in the supplementary materials.
```

### Supplementary Materials

Include:
- `trace.json` - Full TRACE data
- `trace_report.md` - Generated analysis report
- Protocol version reference

---

## Analysis Tools

Generate reports:
```bash
python mcp_server/analysis.py trace.json --report markdown -o report.md
```

Export to CSV:
```bash
python mcp_server/analysis.py trace.json --export csv -o exports/
```

View specific metrics:
```bash
python mcp_server/analysis.py trace.json --metrics code
python mcp_server/analysis.py trace.json --metrics ideas
python mcp_server/analysis.py trace.json --metrics errors
```

---

## Documentation

- **[USER_GUIDE.md](USER_GUIDE.md)** - Complete usage guide
- **[TRACE_PROTOCOL.md](TRACE_PROTOCOL.md)** - Formal protocol specification
- **[CLAUDE.md](CLAUDE.md)** - Instructions for Claude Code
- **[examples/](examples/)** - Example TRACE data

---

## Contributing

Contributions welcome! Please see the protocol specification for schema details.

---

## Citation

If you use TRACE in your research, please cite:

```bibtex
@misc{trace2026,
  title={TRACE: Transparent Research AI Collaboration Environment},
  author={[Your Name]},
  year={2026},
  note={Protocol specification v1.0}
}
```

---

## License

This protocol specification and reference implementation are released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

---

## Acknowledgments

Developed at [UC Berkeley] for transparent AI-assisted research.
