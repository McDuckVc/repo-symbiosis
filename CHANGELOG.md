# Changelog

## 0.2.0

- Scope checks match reserved paths case-sensitively. Overlap detection between reservations still ignores case.
- Ledger writes keep the newest 1,000 coordination receipts and preserve older ones in branch history. Previously, accumulated App receipts eventually exceeded the ledger size limit and blocked every claim and release.
- Reservations have leases (4 hours by default). Journal commits renew them, a completed run holds them until merge, and expired reservations stop blocking new claims and are cleared on the next ledger write. New `renew-claim` command.
- In cooperative mode, `extend-claim` adds paths or resources without changing the claim ID. In App mode, a maintainer adds paths from the task issue, with the change recorded in the ledger.
- Holder tokens: only the session that made a reservation can renew, extend, or release it while its lease runs. `release-claim --override` clears held or expired reservations and records the reason.
- When `SYMBIOSIS_CLAIM_TOKEN` is set at claim time it becomes the holder token, so every session of one account can manage that account's claims after an ephemeral clone is gone.
- Ledger writes retry with randomized exponential backoff for up to 90 seconds instead of six immediate attempts. Ten agents claiming at once now all succeed.
- The PR gate reports problems in JSON and in the GitHub Actions step summary, with repair instructions and warnings for outdated memory. The gate and `check-scope` identify the scope repair for the reservation's mode.
- New `stale` command lists memory facts whose evidence files changed between two commits.
- The MCP server supports MCP Python SDK 1.28 or later, including 2.x, and returns tool errors with their message under both. `doctor` reports MCP as available only for a supported SDK.
- More credential formats in the memory scanner: Google, Stripe, GitLab, npm, PyPI, Hugging Face, SendGrid, Telegram, Slack webhooks, Azure storage keys, JWTs, bearer headers, and unquoted environment-style secrets.
- Ruff lint job in CI, an MCP SDK 1.x compatibility step, and a tag-triggered release workflow that waits for the full matrix.
- Two start-up prompts: a handoff export for agents that already hold project context, and a bootstrap for a clean agent.
- Documentation for protecting the coordination branch and for emergency hotfixes.
- [Upgrade and rollback steps](docs/recovery.md#upgrade-and-rollback) for existing v0.1 checkers.

## 0.1.0 preview

- Installable CLI, read-only MCP server, and optional self-hosted GitHub App.
- Git-backed repository inventory, graph evidence, shared reservations, and append-only agent journals.
- GitHub PR and merge-group validation for reserved scope, original history, and memory privacy.
- GitHub identity checks, maintainer transfers, durable webhook replay, and merge and deployment receipts.
- Local two-agent demo, dense-repository benchmark, and recovery documentation.

The preview targets trusted GitHub teams using same-repository branches and merge commits. Complete the [live verification steps](docs/release.md#go-live-evidence) on the target before making the check mandatory.
