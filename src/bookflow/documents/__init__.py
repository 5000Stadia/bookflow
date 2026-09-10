"""Customer-facing documents, rendered as PDF from command output alone.

`render` is the one entry point. It takes a `read` callable, never a request, so a
web route, a CLI command and the MCP surface can all produce the identical bytes.
"""
from bookflow.documents.render import KINDS, Rendered, filename_for, render

__all__ = ["KINDS", "Rendered", "filename_for", "render"]
