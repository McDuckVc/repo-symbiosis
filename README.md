# Repo Symbiosis

A portable Git workflow for project memory and parallel agent delivery. The Python CLI manages evidence, reservations, journals, and verification. Agents carry out semantic analysis and implementation. Git stores the shared project state.

## Use it in a project

1. Install the release wheel with `python -m pip install path/to/repo_symbiosis-0.1.0-py3-none-any.whl`. From this source tree, use `python -m pip install .`.
2. In the target Git checkout, run `symbiosis init --base main` and `symbiosis doctor`. Use the [GitHub setup](docs/github.md) to install and require the scope, history, and privacy check.
3. For Codex, copy the [skill directory](skills/repo-symbiosis) into the project's `.agents/skills/` directory and invoke `$repo-symbiosis`. Other agents can use the same Git files, CLI, or optional read-only MCP connection.

The release targets Python 3.10+ and Git 2.47+. Use the `mcp` extra for a local MCP server and the `app` extra for [self-hosted GitHub integration](docs/app.md). External worker, cloud, and data routes use the capabilities and credentials configured for the project.

```text
symbiosis --repo <target-checkout> inventory
symbiosis --repo <target-checkout> coverage --details
symbiosis --repo <target-checkout> plan <backlog.json>
```

The initial analysis produces per-component Markdown under `docs/symbiosis/`, including architecture, runtime paths, data, deployment, onboarding, and coverage. Subsequent work adds Git-tracked events under `.symbiosis/events/`. The graph can trace relationships and show where source evidence has changed. Shared reservations live on a separate fast-forward Git branch in the same repository. They expire unless renewed, so a crashed agent cannot block work indefinitely. Each accepted task keeps its own branch and worktree through review.

## Read shared memory

Agents with access to the repository can read the Markdown and JSON on GitHub, query a local clone with the CLI, or connect through the optional read-only MCP server. Use the protected branch or pin the same commit across agents. Each query returns its resolved Git revision.

```text
symbiosis --repo <target-checkout> memory --ref origin/main overview
symbiosis --repo <target-checkout> memory --ref origin/main search "checkout"
```

See [agent memory access](skills/repo-symbiosis/references/agent-access.md) for the MCP launch config and a snippet for the target repository's `AGENTS.md`.

Each agent can append an attributed journal commit on its reserved branch. Keep the original branch history through GitHub merge commits and use `verify-history` to check the approved PR heads and the prior event files. See [agent journals and commit history](skills/repo-symbiosis/references/agent-journal.md).

The GitHub App verifies users before issuing or transferring reservations. It records merges, abandoned registered PRs, and GitHub deployment outcomes in the coordination branch. The local CLI and MCP reader also work in cooperative mode.

| Reference | Use it for |
| --- | --- |
| [Discovery](skills/repo-symbiosis/references/discovery.md) | Snapshot, file and function coverage, knowledge pack |
| [Memory](skills/repo-symbiosis/references/memory.md) | Event format, graph, verification, release records |
| [Delivery](skills/repo-symbiosis/references/delivery.md) | Dependencies, team claims, PRs, protected releases |
| [Integrations](skills/repo-symbiosis/references/integrations.md) | Antigravity, Conductor, GitHub, cloud and data routes |
| [Recovery](docs/recovery.md) | Interrupted runs, failed deliveries, credential incidents, upgrades |
| [Release and demo](docs/release.md) | Build, verification, and two-agent walkthrough |
| [Benchmark](docs/benchmark.md) | Measured inventory and memory query on TypeScript |

Run `python -B -m unittest discover -s tests -v` from this package to exercise the CLI and the two-clone reservation races against temporary local Git repositories.
