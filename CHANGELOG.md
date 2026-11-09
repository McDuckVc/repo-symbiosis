# Changelog

## Unreleased

- Scope checks match reserved paths case-sensitively. Overlap detection between reservations still ignores case.
- Ledger writes keep the newest 1,000 coordination receipts and preserve older ones in branch history. Previously, accumulated App receipts eventually exceeded the ledger size limit and blocked every claim and release.
- `doctor` reports MCP as available only for a supported SDK (`mcp>=1.28,<2`), and the MCP server's install hint names the `mcp` extra.
- Documentation for protecting the coordination branch and for emergency hotfixes.

## 0.1.0 preview

- Installable CLI, read-only MCP server, and optional self-hosted GitHub App.
- Git-backed repository inventory, graph evidence, shared reservations, and append-only agent journals.
- GitHub PR and merge-group validation for reserved scope, original history, and memory privacy.
- GitHub identity checks, maintainer transfers, durable webhook replay, and merge and deployment receipts.
- Local two-agent demo, dense-repository benchmark, and recovery documentation.

The preview targets trusted GitHub teams using same-repository branches and merge commits. Complete the [live verification steps](docs/release.md#go-live-evidence) on the target before making the check mandatory.
