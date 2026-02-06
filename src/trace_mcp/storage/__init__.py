"""TRACE storage backends."""

from trace_mcp.storage.base import TraceStorage
from trace_mcp.storage.json_file import JsonFileStorage

__all__ = ["TraceStorage", "JsonFileStorage"]
