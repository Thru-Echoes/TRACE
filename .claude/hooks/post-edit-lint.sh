#!/bin/bash
# Post-edit hook: run ruff check on edited Python files
# Input: JSON on stdin with tool_input.file_path
# Output: lint warnings (shown as system message in conversation)
# Exit 0: always allow (informational only, never blocks)

FILE=$(python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

if [[ "$FILE" == *.py ]] && [[ -f "$FILE" ]]; then
    OUTPUT=$(ruff check --select E,F,W "$FILE" 2>&1)
    if [[ -n "$OUTPUT" ]]; then
        echo "Ruff found issues in $FILE:"
        echo "$OUTPUT" | head -15
    fi
fi

exit 0
