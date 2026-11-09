# Writing to the session store from outside TRACE

The MCP tools are the normal way to record a session. A pipeline that writes session
files directly shares a store with every running TRACE server and has to follow the same
rules, because TRACE cannot enforce them on code it does not run.

The guarantees in `docs/INVARIANTS.md` cover writes made through the storage backend. A
writer that breaks these rules can still destroy a record.

## Creating a session

Publish a new session so that it can never replace an existing file:

1. Write the complete JSON to a temporary file in the sessions directory.
2. `fsync` the file.
3. Publish it with `os.link(temp, target)`. This fails if `target` exists.
4. If the name is taken, mint a new id and start again. Never fall back to a replace.
5. Remove the temporary file, then `fsync` the sessions directory.

```python
fd, temp = tempfile.mkstemp(dir=sessions_dir, suffix=".tmp")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.link(temp, sessions_dir / f"{session_id}.json")  # FileExistsError: mint a new id
finally:
    os.unlink(temp)
trace_mcp.durability.fsync_dir(sessions_dir)
```

## Naming a session

- Name the file `<id>.json`, with an id that `trace_mcp.storage.json_file.sanitize_name`
  returns unchanged. The safest form is `trace_<YYYYMMDD>_<hex>`. TRACE rewrites any other
  id, including one containing `..`, so it would look for a different file.
- Start the id with `trace_<YYYYMMDD>_`. Listings and summaries include only `trace_*.json`, and
  the date orders sessions by day. Within a day, TRACE reads the most recently written
  files first, and ranks the ones it reads by the `created` field.

## Changing an existing session

Do not rewrite a session file from outside TRACE, in place or with a replace. Every
update has to happen under that session's lock after re-reading the file, or concurrent
writers lose events. Python code should use
`trace_mcp.storage.locked.locked_disk_session`. Anything else should write through the
MCP tools.

To fix a wrong project label, add an alias to the registry with `trace-mcp identity`.
Captured records are not rewritten.

## Reading while TRACE is writing

Every file published as above is complete when it appears. A store on a filesystem
without hard links is the exception: TRACE then creates the file before writing to it, so
a reader can see partial JSON and should skip a file that does not parse.
