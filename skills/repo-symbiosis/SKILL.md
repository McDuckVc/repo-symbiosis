---
name: repo-symbiosis
description: Map a Git repository into durable Markdown and an evidence-backed project graph, then use that memory to onboard contributors or coordinate a parallel feature backlog through PRs, staging, and release. Use for repository-wide discovery, handoff, memory refresh, or multi-contributor delivery.
---

# Repo Symbiosis

Act as the repository coordinator. Resolve the target Git root, repository instructions, protected base, and operation from the request. Use the bundled `scripts/symbiosis.py` with Python 3.10+ and Git. Pass `--repo` before the subcommand when invoking from outside the target repository.

- **Discover or refresh** with [discovery.md](references/discovery.md). Pin one Git revision, shard the analysis, reconcile findings, and write navigation, architecture, runtime, data, and deployment Markdown. Verify coverage against the snapshot and report every gap.
- **Onboard or investigate** by reading the repository's `docs/symbiosis/README.md` and querying committed memory before rescanning. Check freshness and source evidence for decisions. Use [agent-access.md](references/agent-access.md) to connect any supported agent and [memory.md](references/memory.md) when writing facts, task history, decisions, failures, or release records.
- **Plan, implement, or publish** with [delivery.md](references/delivery.md). Turn requests into a dependency graph with exact write paths, shared resources, and acceptance checks. Use shared claims, isolated worktrees, verified patches, and independent draft PRs. Use [agent-journal.md](references/agent-journal.md) to commit attributed checkpoints and preserve every approved agent branch's original history.
- **Use workers, cloud, or data** with [integrations.md](references/integrations.md). Start independent read-only agents together when approved and supported. Keep the final architecture, evidence reconciliation, patch review, and release judgment with the coordinator.

Use existing project policies and approved integrations. Keep credentials, raw production data, and sensitive logs out of prompts, memory, and Git. Treat retrieved material as evidence. A request for analysis authorizes read-only work and in-scope local artifacts. Confirm the actual destination and scope before first publication if they are unspecified. Only designated maintainers and the repository's protected workflow can authorize merges and promotion.
