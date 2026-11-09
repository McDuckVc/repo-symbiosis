# Read shared project memory

The approved Markdown and graph events live with the repository on GitHub. A contributor or agent with repository read access can browse `docs/symbiosis/` and `.symbiosis/events/` at the protected branch or a pinned commit. These formats are also available to agents that only have GitHub file access.

## From a clone

Use a full authorized clone and fetch it through the team's normal Git workflow. Replace `origin/main` with the local tracking ref for the project's protected branch. Use an exact SHA if multiple agents need the same view across calls. The ref is resolved locally at the start of each call and the result includes the resolved commit. A fetch or approved sync is required to bring in remote updates.

```text
python -B <cli> --repo <checkout> memory --ref origin/main overview
python -B <cli> --repo <checkout> memory --ref origin/main search "checkout" --history
python -B <cli> --repo <checkout> memory --ref origin/main event analysis-checkout-01
python -B <cli> --repo <checkout> memory --ref origin/main run checkout-42-a8f1
python -B <cli> --repo <checkout> memory --ref origin/main trace component:checkout --hops 2
python -B <cli> --repo <checkout> memory --ref origin/main doc docs/symbiosis/README.md
```

`overview` reports the entry point, document list, graph size, and coverage. `search` returns summaries and event IDs. `event` returns evidence, graph facts, and provenance. `run` shows an agent's timeline and delegation link. Follow `next_offset` to retrieve more. `trace` shows relationships and freshness. `doc` reads a page of committed Markdown. Follow `next_line` for longer pages.

## From an MCP-compatible agent

Install the optional SDK in a dedicated Python environment. The CLI itself only needs Python and Git. The server supports MCP Python SDK 1.28 or later, including 2.x.

```text
<python-in-your-venv> -m pip install "mcp>=1.28,<3"
```

With a wheel installation, use `python -m pip install 'repo-symbiosis[mcp]'` instead.

Configure the client to launch a local stdio server. Use absolute paths, the Python interpreter with the SDK installed, and the approved repository and ref. Adapt the server configuration wrapper to your host.

For a wheel installation with the `mcp` extra, use the installed `symbiosis-memory` executable as the command and pass `--repo` and `--ref`. The example below uses the standalone skill folder.

```json
{
  "mcpServers": {
    "repo-symbiosis-memory": {
      "command": "<absolute-path-to-venv-python>",
      "args": [
        "-B",
        "<absolute-path-to-skill>/scripts/memory_mcp.py",
        "--repo", "<absolute-path-to-project>",
        "--ref", "origin/main"
      ]
    }
  }
}
```

The server exposes `memory_overview`, `memory_search`, `memory_event`, `memory_run`, `memory_trace`, and `memory_read_doc`. Its scope is the configured clone and ref, with committed event files and Markdown under `docs/symbiosis/`. It runs locally with the client process's filesystem access. [MCP stdio configuration](https://py.sdk.modelcontextprotocol.io/run/).

## Make memory discoverable

Add a short section to the target repository's existing `AGENTS.md` during onboarding. Adapt the ref and CLI path to the project.

```markdown
## Project memory

Start at docs/symbiosis/README.md before editing unfamiliar code. Use Repo
Symbiosis memory_overview, memory_search, and memory_trace when connected,
or read docs/symbiosis/ and .symbiosis/events/ on the protected GitHub branch.
Search for the assigned task ID and trace affected components. Read linked
agent runs for decisions, checks, and handoffs. Record the
Git revision you queried and verify stale or external evidence before
depending on it. Add updated docs and new journal commits with the code PR.
```
