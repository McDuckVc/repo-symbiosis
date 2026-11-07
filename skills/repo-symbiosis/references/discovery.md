# Repository discovery and refresh

## Choose the snapshot

Inspect existing repository instructions, docs, and Git status. Use the protected branch tip for a baseline or the requested commit for an investigation. Preserve any current work. The inventory reads Git objects at the pinned commit, including when the checkout has edits. Give workers that exact revision and tell them to use Git objects or an isolated checkout at that revision. For a repository without a commit, establish a first commit from the agreed scope before claiming coverage.

```text
python <cli> --repo <repo> inventory --rev <sha> --shard-size 100
python <cli> --repo <repo> inventory --rev <sha> --shard-size 100 --shard 1
python <cli> --repo <repo> inventory --rev <new-sha> --since <old-sha>
python <cli> --repo <repo> coverage --rev <sha> --details
```

Use smaller batches for dense or large files. Group by package, ownership, or runtime boundary as appropriate. Give each batch a unique worker or coordinator assignment. The inventory covers tracked paths and classifies them heuristically. It reports exclusions for common secret files, binary assets, generated dependencies, symlinks, and submodules. Record additional exclusions, large or opaque files, generated code, and inaccessible systems in the coverage notes. For an authorized submodule, repeat discovery at the pinned submodule commit. Avoid executing repository code during source discovery.

## Collect verifiable findings

Run independent investigations for application entry points, packages and contracts, data and migrations, delivery infrastructure, and quality or failure behavior. Use the active language tooling or static analysis to enumerate symbols for each source shard. Give each callable a file and line range, purpose, caller or entry point, inputs and outputs, side effects, and error behavior where relevant. Group straightforward internal callables in per-module tables. Trace dynamic registration, reflection, and runtime dependencies separately. Mark each unresolved call or access gap. Give workers paths, the revision, the expected structured report, and the limits on external access.

Each report should give reviewed paths, evidence ranges, meaningful graph nodes and edges, cross-shard dependencies, unknowns, and a compact summary. The coordinator checks source evidence, resolves duplicate IDs, and connects findings across shards. Only mark a file reviewed after covering its relevant contents. A path listed in the inventory alone gives no coverage.

Record configured staging and production separately. For each environment capture triggers, gates, build and artifact identity, deployment target, configuration and secret references by name where safe, migrations, observability, rollback, and operator ownership. Label declarations from Git as configured. Label actual runtime facts as observed only when supported by scoped provider or deployment evidence and an observation date. Track contradictions between the two.

## Write the knowledge pack

Use `docs/symbiosis/` by default. Extend the existing knowledge layout if one is already established.

| File | Content |
| --- | --- |
| `README.md` | Onboarding entry point, source snapshot, ownership, protected base, shared coordination remote, and how to query the memory |
| `architecture.md` | Package and service boundaries, dependency directions, interfaces, design decisions |
| `runtime.md` | Entry points, request and job flows, failure and retry behavior |
| `data.md` | Stores, models, pipelines, access boundaries, migrations |
| `deployment.md` | CI, previews, staging, production, approvals, rollback, and known drift |
| `components/<name>.md` | Per-component file and function index, links to source, tests and contracts |
| `coverage.md` | Snapshot, shard status, exclusions, stale or missing files, and unverified runtime facts |

Use relative links in the repository, evidence at the pinned commit for stable references, and Mermaid for cross-component flows when helpful. Split pages by component ownership so feature PRs can update different documents. On refresh, compare the last mapped revision with the target, inspect deleted paths and dependent graph nodes, then record successor events and update affected pages. A rename appears as a deletion and addition. Keep prior facts in event history.

When onboarding the repository, point its existing `AGENTS.md` at the knowledge pack and shared memory. Use the [agent access snippet](agent-access.md#make-memory-discoverable) and preserve the repository's existing instructions.

Run `verify --base <protected-base>` and `coverage --rev <sha>`. Reconcile anything claimed complete with the report. For a refresh also read the changed paths and relevant graph neighborhood. Re-evaluate any relationship whose source evidence has gone stale.
