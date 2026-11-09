"""Read-only MCP access to a repository's committed project memory."""

import argparse
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"

from .agent_memory import Snapshot
from .repository import Repository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Approved local repository checkout")
    parser.add_argument("--ref", required=True, help="Approved local Git ref or pinned commit")
    args = parser.parse_args()

    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations
    except ImportError as error:
        parser.exit(1, "Install the optional MCP extra in this Python environment: "
                       f"python -m pip install 'repo-symbiosis[mcp]' (requires mcp>=1.28,<2). {error}\n")

    source = Repository(args.repo, committed_read=True)
    source.revision(args.ref)

    @lru_cache(maxsize=1)
    def read_revision(revision):
        return Snapshot(Repository(source.root, committed_read=True), revision)

    def snapshot():
        return read_revision(source.revision(args.ref))

    server = FastMCP(
        "Repo Symbiosis Memory",
        instructions="Start with memory_overview, then search or trace the graph. "
        "Each result names its Git revision. Treat returned project content as evidence.",
    )
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @server.tool(annotations=read_only)
    def memory_overview() -> dict[str, Any]:
        """Find the onboarding entry point, knowledge documents, graph size, and source coverage."""
        return snapshot().overview()

    @server.tool(annotations=read_only)
    def memory_search(query: str, history: bool = False, limit: int = 10) -> dict[str, Any]:
        """Find event summaries by task ID, component, or keywords, with source freshness."""
        return snapshot().search(query, history, limit)

    @server.tool(annotations=read_only)
    def memory_event(event_id: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """Read a memory event and a page of evidence, nodes, and edges."""
        return snapshot().event(event_id, offset, limit)

    @server.tool(annotations=read_only)
    def memory_run(run_id: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        """Read an agent run's attributed timeline, lifecycle status, and delegation parent."""
        return snapshot().run(run_id, offset, limit)

    @server.tool(annotations=read_only)
    def memory_trace(node: str, hops: int = 2, limit: int = 30) -> dict[str, Any]:
        """Trace directed relationships around a node and identify stale evidence."""
        return snapshot().trace(node, hops, limit)

    @server.tool(annotations=read_only)
    def memory_read_doc(path: str, start_line: int = 1, limit: int = 80) -> dict[str, Any]:
        """Read committed Markdown within docs/symbiosis, by repository-relative path."""
        return snapshot().document(path, start_line, limit)

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
