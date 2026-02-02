# TRACE Quick Setup Guide

Get TRACE running in your project in 5 minutes.

---

## Prerequisites

- Python 3.10+
- Claude Code CLI installed
- pip

---

## Step 1: Copy Files (30 seconds)

```bash
# From the TRACE_template directory
cp trace.json /path/to/your/project/
cp CLAUDE.md /path/to/your/project/
cp .mcp.json /path/to/your/project/
cp -r mcp_server /path/to/your/project/
mkdir -p /path/to/your/project/.claude
cp .claude/settings.local.json /path/to/your/project/.claude/
```

Or as a one-liner:
```bash
cp -r trace.json CLAUDE.md .mcp.json mcp_server .claude /path/to/your/project/
```

---

## Step 2: Install Dependencies (30 seconds)

```bash
pip install mcp anthropic
```

---

## Step 3: Configure Your Project (1 minute)

Edit `trace.json` and update the metadata:

```json
{
  "metadata": {
    "project": "YOUR PROJECT NAME",
    "description": "YOUR PROJECT DESCRIPTION",
    "maintainers": ["YOUR NAME"]
  },
  "context": {
    "goals": "YOUR PROJECT GOALS",
    "current_status": "YOUR CURRENT STATUS"
  }
}
```

---

## Step 4: Verify Setup (1 minute)

```bash
cd /path/to/your/project
claude
```

Then in Claude Code:
```
> Check TRACE context
```

You should see your project information.

---

## Step 5: Start Using TRACE (ongoing)

### At the start of each work session:
```
> Start a TRACE session for [what you're working on]
```

### During work, Claude will automatically:
- Log code contributions
- Track ideas and their origins
- Record errors and who caught them
- Document interventions

### At the end of each session:
```
> End the TRACE session with summary: [what was accomplished]
```

### To see your metrics:
```
> Show TRACE metrics
> Export TRACE report as markdown
```

---

## Common Commands

| Command | What it does |
|---------|-------------|
| `Start a TRACE session for X` | Begin tracking |
| `End TRACE session` | Stop tracking, record summary |
| `Show TRACE metrics` | View computed statistics |
| `Export TRACE report` | Generate markdown report |
| `Log code: file.py, 50 AI lines, 20 human lines` | Manual code logging |
| `Log AI idea: [idea]` | Record an AI-suggested idea |
| `Log error: AI made X, human caught it` | Record error attribution |

---

## Troubleshooting

### "MCP server not starting"
1. Check Python version: `python --version` (need 3.10+)
2. Verify MCP installed: `pip show mcp`
3. Check path in `.mcp.json`

### "TRACE tools not available"
1. Restart Claude Code
2. Check `.claude/settings.local.json` has `mcp__trace__*` in allow list

### "trace.json not saving"
1. Check file permissions
2. Verify `TRACE_PATH` in `.mcp.json`

---

## Next Steps

- Read [USER_GUIDE.md](USER_GUIDE.md) for detailed documentation
- Review [examples/example_trace.json](examples/example_trace.json) to see sample data
- Check [TRACE_PROTOCOL.md](TRACE_PROTOCOL.md) for the formal specification

---

## Support

- Issues: [GitHub Issues]
- Documentation: [USER_GUIDE.md](USER_GUIDE.md)
