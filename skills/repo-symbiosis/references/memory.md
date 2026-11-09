# Project memory

The Git-tracked `.symbiosis/events/` directory holds one immutable JSON record per event. A branch can add unique event files alongside the corresponding source and Markdown. The CLI rebuilds the active graph on demand. Use the Markdown for navigation and the graph for targeted retrieval and impact analysis. Agent work and handoffs use a per-run [journal](agent-journal.md) within this directory.

For shared reads across agents, use the [committed CLI or MCP interface](agent-access.md). The development commands below use event files from the current checkout, so the coordinator can validate changes before committing them.

```text
python <cli> --repo <repo> search "checkout payments" --history
python <cli> --repo <repo> trace component:checkout --hops 3
python <cli> --repo <repo> record <event-input.json> --rev <source-commit>
python <cli> --repo <repo> verify --base <protected-base>
```

## Input for record

```json
{
  "id": "analysis-checkout-01",
  "kind": "analysis",
  "summary": "Checkout validates orders and calls the payment adapter.",
  "actor": "checkout-review",
  "evidence": [
    {"path": "checkout/service.py", "start": 14, "end": 41, "reviewed": true}
  ],
  "nodes": [
    {"id": "component:checkout", "title": "Checkout"},
    {"id": "symbol:checkout/service.py#submit_order", "title": "submit_order"}
  ],
  "edges": [
    {"from": "component:checkout", "to": "symbol:checkout/service.py#submit_order", "type": "exposes"}
  ]
}
```

- Event IDs use lowercase letters, digits, and hyphens. Include a task, shard, or unique suffix to avoid collisions. Supported kinds are `analysis`, `agent`, `decision`, `task`, `failure`, `release`, and `retirement`.
- Stable graph IDs are prefixed, for example `component:`, `file:`, `symbol:`, `service:`, `store:`, `workflow:`, `environment:`, and `resource:`. Use named directed edges such as `calls`, `depends-on`, `writes-to`, `builds`, `deploys-to`, and `promotes-to`. Add both endpoints before linking separate shards. Include a summary and meaningful evidence for assertions.
- Evidence paths are repository-relative. Line ranges are inclusive and optional. Set `reviewed` only for reviewed files. `record` resolves the source commit and adds the blob hashes, schema and UTC recording time. For runtime-only facts use sanitized HTTPS `links` with date and provenance in the summary.
- Small events make selective refresh possible. To replace a fact, add a complete successor with `"supersedes": ["old-event-id"]`. To retire facts without replacements, add a `retirement` event. Keep referenced nodes defined where other active edges still need them. Resolve concurrent replacements in the integration branch before merging.
- A task event includes `task` and `status`. Status values are `planned`, `claimed`, `draft`, `review-ready`, `merged`, and `abandoned`. Record observed outcomes, with a verified PR link for `merged`. Capture acceptance checks, retry decisions, useful failure context, and handoff details in separate events. Link to restricted systems only when permitted.
- A release event includes `release` with `phase`, `environment`, and `digest` in `sha256:<64 lowercase hex characters>` form. `frozen` starts the chain. `staged` references a frozen event through `previous` and `promoted` references a staged event. Later phases require deployment evidence links. The verifier checks that commit and artifact digest match throughout. Record rollbacks as their own release or failure events with the artifact actually restored and the rollback evidence.

## Integrity and retrieval

Evidence is assessed against the requested `--rev`, which defaults to `HEAD`. Search and graph traces expose `current`, `stale`, `external`, or `unpinned` evidence. `coverage` separates reviewed, stale, missing and excluded files. `verify --base` detects rewritten or deleted baseline events. `verify --strict` fails when historical file evidence cannot be recovered. `stale --base <rev> [--candidate <rev>]` lists active facts whose evidence files changed between two commits and no longer match. Add `--strict` to exit 1 when any are found. The PR gate reports the same list as non-blocking warnings. Refresh a fact by recording a successor that supersedes it.

Keep authoritative source commits reachable when practical. After a squash merge a fresh clone may lack the original feature commit. Verification accepts exact content at the supplied target revision and lists historical evidence it cannot recover. Reconcile enduring facts to the merged commit while preserving the earlier event. Keep provider references and summarized observations separate from source-derived coverage.
