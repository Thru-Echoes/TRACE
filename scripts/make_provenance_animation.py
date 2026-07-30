"""Render a real TRACE session as an animated SVG plus a print-quality still.

The figure answers the question a diff cannot: *who decided this, what was
ruled out, and what was later retracted?* It is built from a captured session
file — no event, actor, or attribution value is invented here. Descriptions are
the recorded ones, truncated at a word boundary with an ellipsis; everything
else (type, category, actor, direction/execution, disposition, correction
targets) is copied verbatim.

Outputs:
    <out>/provenance.svg        animated, loops; for the README and a screen demo
    <out>/provenance-still.svg  final frame, no animation; for print/poster

Both are self-contained (no external fonts, scripts, or images) and carry
light- and dark-mode styling, so they render correctly in a GitHub README and
in a browser on either OS theme.

Colors are the validated categorical slots 1-3 of the reference palette
(blue/orange/aqua), which clear every all-pairs check in both modes. Every card
also carries a visible type label, satisfying the relief rule for the aqua slot
and the secondary-encoding requirement.

Usage:
    python3 scripts/make_provenance_animation.py [SESSION_JSON] [-o OUT_DIR]

Side effects: writes two .svg files into OUT_DIR (default docs/).
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

# ---------------------------------------------------------------- geometry ---

W, H = 1200, 640
PAD = 44
LEFT_W = 348
AXIS_X = PAD + LEFT_W + 92
ROW_TOP = 176
ROW_STEP = 58
CYCLE = 14.0  # seconds for one full loop
ROW_IN = 0.62  # seconds a row takes to appear
ROW_GAP = 0.72  # seconds between successive rows
HOLD_PCT = 93.0  # percent of the cycle the finished frame is held

# Validated categorical slots (light, dark). See references/palette.md.
INK = {
    "decision": ("#2a78d6", "#3987e5"),
    "correction": ("#eb6834", "#d95926"),
    "contribution": ("#1baf7a", "#199e70"),
    "note": ("#6b6a63", "#a3a29a"),
}

TYPE_LABEL = {
    "decision": "decision",
    "contribution": "contribution",
    "correction": "correction",
    "note": "annotation",
}


@dataclass
class Row:
    """One rendered event line, all values copied from the capture."""

    eid: str
    kind: str  # key into INK
    sublabel: str  # category or disposition, verbatim
    actor: str  # "ai" | "human"
    attribution: str  # e.g. "direction human · execution ai"
    text: str  # recorded description, truncated
    corrects: list[str]


def clean(s: str) -> str:
    """Collapse whitespace and resolve HTML entities present in the stored text.

    A small number of captured descriptions arrive already containing `&lt;`
    where the author meant `<`. This is not a storage defect — the store round-
    trips raw angle brackets fine (326 string values hold a raw `<` against 30
    holding `&lt;`) — the entities come in that way from the authoring side.
    Resolving them renders the text as written rather than showing the entity.
    """
    return " ".join(html.unescape(s).split())


def truncate(s: str, limit: int) -> str:
    """Cut *s* to <= limit chars on a word boundary, marking the cut."""
    s = clean(s)
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def get_rows(session: dict, text_limit: int = 78) -> list[Row]:
    """Project a session's events into render rows, verbatim except for length.

    Raises ValueError when the session carries no events, rather than emitting
    an empty figure that would read as "nothing was captured".
    """
    rows: list[Row] = []
    for ev in session.get("events", []):
        etype = ev.get("type")
        body = ev.get(etype) or {}
        actor = (ev.get("actor") or {}).get("type", "?")
        if etype == "decision":
            disposition = body.get("disposition", "proposed")
            resolved = body.get("resolved_by")
            rby = resolved.get("type") if isinstance(resolved, dict) else resolved
            attribution = f"proposed by {actor}" + (f" · {disposition} by {rby}" if rby else " · awaiting resolution")
            rows.append(
                Row(
                    ev["id"],
                    "decision",
                    disposition,
                    actor,
                    attribution,
                    truncate(body.get("description", ""), text_limit),
                    [],
                )
            )
        elif etype == "contribution":
            attribution = f"direction {body.get('direction')} · execution {body.get('execution')}"
            rows.append(
                Row(
                    ev["id"],
                    "contribution",
                    "artifact",
                    actor,
                    attribution,
                    truncate(body.get("description", ""), text_limit),
                    [],
                )
            )
        elif etype == "annotation":
            cat = body.get("category", "note")
            kind = "correction" if cat == "correction" else "note"
            corrects = [c for c in (body.get("corrects_event_ids") or []) if c.startswith("evt_")]
            attribution = f"corrects {', '.join(corrects)}" if corrects else f"logged by {actor}"
            rows.append(
                Row(ev["id"], kind, cat, actor, attribution, truncate(body.get("content", ""), text_limit), corrects)
            )
    if not rows:
        raise ValueError("session has no renderable events — refusing to emit an empty figure")
    return rows


# ------------------------------------------------------------------ render ---


def keyframes(name: str, appear_at: float, dx: int = -14) -> str:
    """A full-cycle keyframe so every element loops in lockstep."""
    a = max(0.0, appear_at / CYCLE * 100)
    b = min(HOLD_PCT, (appear_at + ROW_IN) / CYCLE * 100)
    return (
        f"@keyframes {name}{{"
        f"0%,{a:.2f}%{{opacity:0;transform:translateX({dx}px)}}"
        f"{b:.2f}%,{HOLD_PCT:.2f}%{{opacity:1;transform:translateX(0)}}"
        f"100%{{opacity:0;transform:translateX({dx}px)}}}}"
    )


def chip(x: float, y: float, label: str, kind: str, small: bool = False) -> tuple[str, float]:
    """Return (svg, width) for a rounded type chip; width lets callers lay out after it."""
    fs = 11 if small else 12
    w = len(label) * fs * 0.62 + 18
    svg = (
        f'<g><rect x="{x}" y="{y - fs}" width="{w:.1f}" height="{fs + 8}" rx="{(fs + 8) / 2}" '
        f'fill="var(--c-{kind})" fill-opacity="0.14" stroke="var(--c-{kind})" stroke-opacity="0.55" stroke-width="1"/>'
        f'<text x="{x + 9}" y="{y + 0.5}" font-size="{fs}" font-weight="600" fill="var(--c-{kind})">{escape(label)}</text></g>'
    )
    return svg, w


def build(session: dict, rows: list[Row], animated: bool) -> str:
    meta = session.get("metadata", {})
    sid = session.get("id", "")
    project = meta.get("project", "")
    started = (session.get("created") or "")[:10]

    css = [
        ":root{--surface:#fcfcfb;--card:#ffffff;--line:#e4e3de;--ink:#0b0b0b;--ink2:#52514e;--ink3:#78776f;"
        "--c-decision:#2a78d6;--c-correction:#eb6834;--c-contribution:#1baf7a;--c-note:#6b6a63;}",
        "@media (prefers-color-scheme:dark){:root{--surface:#1a1a19;--card:#232322;--line:#3a3a37;"
        "--ink:#ffffff;--ink2:#c3c2b7;--ink3:#93928a;--c-decision:#3987e5;--c-correction:#d95926;"
        "--c-contribution:#199e70;--c-note:#a3a29a;}}",
        "text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;}",
        ".mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}",
    ]

    body: list[str] = []
    body.append(f'<rect width="{W}" height="{H}" fill="var(--surface)"/>')

    # ---- header
    body.append(
        f'<text x="{PAD}" y="{PAD + 30}" font-size="30" font-weight="700" fill="var(--ink)">'
        f"A commit says what changed. This says who decided it.</text>"
    )
    body.append(
        f'<text x="{PAD}" y="{PAD + 58}" font-size="14.5" fill="var(--ink2)">'
        f"Every row below was captured automatically while the work happened — nothing here was written for this figure.</text>"
    )

    # ---- left panel. Content is measured first so the card can be sized to it;
    # a fixed height that matched the timeline left a large dead area below.
    ly = ROW_TOP - 26
    desc = clean(meta.get("description") or "")
    line, lines = "", []
    for word in desc.split():
        if len(line) + len(word) + 1 > 44:
            lines.append(line)
            line = word
            if len(lines) == 5:
                break
        else:
            line = (line + " " + word).strip()
    if len(lines) < 5 and line:
        lines.append(line)
    if len(lines) == 5:
        lines[-1] = truncate(lines[-1], 40)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    kinds = [k for k in ("decision", "contribution", "correction", "note") if k in counts]
    lgy = ly + 108 + len(lines) * 18 + 26
    card_h = (lgy + 38 + len(kinds) * 25) - ly

    body.append(
        f'<rect x="{PAD}" y="{ly}" width="{LEFT_W}" height="{card_h}" rx="14" '
        f'fill="var(--card)" stroke="var(--line)" stroke-width="1.25"/>'
    )
    body.append(
        f'<text x="{PAD + 22}" y="{ly + 30}" font-size="11" font-weight="700" fill="var(--ink3)" letter-spacing="1.2">'
        f"CAPTURED SESSION</text>"
    )
    body.append(
        f'<text x="{PAD + 22}" y="{ly + 55}" font-size="15" font-weight="650" fill="var(--ink)" class="mono">{escape(sid)}</text>'
    )
    body.append(
        f'<text x="{PAD + 22}" y="{ly + 76}" font-size="12.5" fill="var(--ink2)">project {escape(project)} · {started}</text>'
    )

    for i, ln in enumerate(lines):
        body.append(
            f'<text x="{PAD + 22}" y="{ly + 108 + i * 18}" font-size="12.5" fill="var(--ink2)">{escape(ln)}</text>'
        )

    # legend / ledger
    body.append(
        f'<line x1="{PAD + 22}" y1="{lgy - 14}" x2="{PAD + LEFT_W - 22}" y2="{lgy - 14}" stroke="var(--line)" stroke-width="1"/>'
    )
    body.append(
        f'<text x="{PAD + 22}" y="{lgy + 10}" font-size="11" font-weight="700" fill="var(--ink3)" letter-spacing="1.2">WHAT WAS RECORDED</text>'
    )
    for i, kind in enumerate(kinds):
        yy = lgy + 38 + i * 25
        body.append(f'<circle cx="{PAD + 29}" cy="{yy - 4}" r="5.5" fill="var(--c-{kind})"/>')
        body.append(f'<text x="{PAD + 44}" y="{yy}" font-size="13" fill="var(--ink)">{TYPE_LABEL[kind]}</text>')
        body.append(
            f'<text x="{PAD + LEFT_W - 24}" y="{yy}" font-size="13" font-weight="650" text-anchor="end" fill="var(--ink2)" class="mono">{counts[kind]}</text>'
        )

    # ---- timeline
    last_y = ROW_TOP + (len(rows) - 1) * ROW_STEP
    body.append(
        f'<line x1="{AXIS_X}" y1="{ROW_TOP - 26}" x2="{AXIS_X}" y2="{last_y + 20}" stroke="var(--line)" stroke-width="2"/>'
    )

    anim_css: list[str] = []
    y_of: dict[str, float] = {}
    for i, r in enumerate(rows):
        y = ROW_TOP + i * ROW_STEP
        y_of[r.eid] = y
        appear = i * ROW_GAP
        name = f"r{i}"
        anim_css.append(keyframes(name, appear))
        style = f' style="animation:{name} {CYCLE}s linear infinite"' if animated else ""

        g = [f"<g{style}>"]
        g.append(
            f'<circle cx="{AXIS_X}" cy="{y - 5}" r="7" fill="var(--surface)" stroke="var(--c-{r.kind})" stroke-width="3"/>'
        )
        tx = AXIS_X + 26
        c, cw = chip(tx, y, TYPE_LABEL[r.kind], r.kind)
        g.append(c)
        g.append(
            f'<text x="{tx + cw + 10}" y="{y}" font-size="12" fill="var(--ink3)" class="mono">{escape(r.eid)}</text>'
        )
        g.append(f'<text x="{tx + cw + 78}" y="{y}" font-size="12" fill="var(--ink3)">{escape(r.sublabel)}</text>')
        g.append(
            f'<text x="{W - PAD}" y="{y}" font-size="12" font-weight="600" text-anchor="end" fill="var(--ink2)">{escape(r.attribution)}</text>'
        )
        g.append(f'<text x="{tx}" y="{y + 20}" font-size="12.5" fill="var(--ink)">{escape(r.text)}</text>')
        g.append("</g>")
        body.append("".join(g))

    # correction arrow(s), drawn after the row that carries them
    for i, r in enumerate(rows):
        for target in r.corrects:
            if target not in y_of:
                continue
            y0, y1 = y_of[r.eid] - 5, y_of[target] - 5
            bow = AXIS_X - 46
            appear = i * ROW_GAP + 0.35
            name = f"a{i}"
            anim_css.append(keyframes(name, appear, dx=0))
            style = f' style="animation:{name} {CYCLE}s linear infinite"' if animated else ""
            body.append(
                f'<g{style}><path d="M {AXIS_X - 9} {y0} C {bow} {y0}, {bow} {y1}, {AXIS_X - 9} {y1}" '
                f'fill="none" stroke="var(--c-correction)" stroke-width="2" stroke-dasharray="4 3" '
                f'marker-end="url(#arw)"/>'
                f'<text x="{bow - 8}" y="{(y0 + y1) / 2 + 4}" font-size="11" font-weight="700" text-anchor="end" '
                f'fill="var(--c-correction)">retracts</text></g>'
            )

    # ---- footer
    body.append(
        f'<text x="{PAD}" y="{H - 26}" font-size="12" fill="var(--ink3)">'
        f"Unedited capture from {escape(sid)} · descriptions truncated at a word boundary · "
        f"attribution, categories and correction targets verbatim</text>"
    )

    defs = (
        '<defs><marker id="arw" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
        'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="var(--c-correction)"/></marker></defs>'
    )
    style_block = "<style>" + "".join(css) + ("".join(anim_css) if animated else "") + "</style>"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="Timeline of a captured TRACE session showing decisions, contributions and a correction">'
        f"{defs}{style_block}" + "".join(body) + "</svg>"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", nargs="?", default=None, help="path to a TRACE session JSON")
    ap.add_argument("-o", "--out", default="docs", help="output directory (default: docs)")
    ns = ap.parse_args()

    if ns.session is None:
        print("error: pass a session JSON path", file=sys.stderr)
        return 2
    session = json.loads(Path(ns.session).read_text())
    rows = get_rows(session)

    out = Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "provenance.svg").write_text(build(session, rows, animated=True))
    (out / "provenance-still.svg").write_text(build(session, rows, animated=False))
    print(f"wrote {out / 'provenance.svg'} and {out / 'provenance-still.svg'} ({len(rows)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
