"""Shadow comparison period for provenance marks (ADR 008).

During the period, the learn extension computes a second, *marked* arm beside
today's behaviour: a nearest-50 extraction whose proposals stay worded as
proposals, and a record of what recall would have shown with marks. Nothing a
session sees changes. Everything the period writes lives under the shadow root
(``$TRACE_SHADOW_DIR``, default ``~/.trace/shadow``), and every shadow step
runs after the live step it shadows, inside its own exception boundary.

Modules: ``state`` (paths, on/off state, append-only records), ``words`` (the
person's words saved by the host hooks), ``extract`` (the session-end shadow
step), ``recall`` (the recall record), ``report`` and ``cli`` (the read-only
report and the ``trace-mcp shadow`` subcommand), ``host_transcript`` (the
capture comparison).
"""
