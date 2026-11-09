# Agent journals and commit history

Each agent run has a task, actor, provider, reserved task branch, and its own directory at `.symbiosis/events/agent-runs/<run>/`. Every checkpoint is a new event and a new commit. Code, tests, and component docs can be committed separately on the same branch. The journal links to the source commit it describes, while Git records the author and the commit that added the journal entry.

## Start and finish a run

1. Allocate a unique run ID before dispatch. Reserve the run directory as a write path ending in `/`, alongside the source and documentation paths. Use the approved coordination remote and the task's isolated worktree. Run IDs use lowercase letters, digits, and hyphens, with up to 69 characters.
2. Start at `agent-<run>-0001` with phase `started`. To record delegation or a handoff, set `agent.parent` to the initiating event from another run. The parent must be committed and present in the worker's branch. Parallel workers can share one committed dispatch event.
3. Commit implementation and relevant docs normally. Append `checkpoint` events as needed. Use `blocked` or `handoff` with concrete next steps. Each new event points to the previous event in that run. A handoff closes the current run and the next worker starts its own run linked to that event. For unfinished work, coordinate the reservation transfer and resume from the published branch containing the handoff.
4. Append a `completed` event after committing the work and recording verification results. Include the decision summary, important tradeoffs, evidence, and remaining work. Keep project-relevant rationale and summarized checks in the journal, and keep secrets and raw provider transcripts in their approved systems.

Example completion event for a run with one prior event:

```json
{
  "id": "agent-checkout-42-a8f1-0002",
  "kind": "agent",
  "task": "checkout-42",
  "actor": "varun/checkout-worker",
  "summary": "Added idempotency keys to payment submission.",
  "agent": {
    "run": "checkout-42-a8f1",
    "provider": "antigravity",
    "phase": "completed",
    "previous": "agent-checkout-42-a8f1-0001",
    "rationale": "Reuse the existing payment adapter so retries preserve the same request identity.",
    "checks": ["Checkout integration tests passed, including duplicate submission."]
  },
  "evidence": [{"path": "checkout/payment.py"}]
}
```

```text
python -B <cli> --repo <worktree> agent-commit <event.json> --remote <coord> --claim-id <claim>
python -B <cli> --repo <worktree> check-scope <task> --remote <coord> --claim-id <claim>
```

`agent-commit` checks the reservation, holder token, reserved branch, journal path, and committed predecessor. It renews the reservation lease after each entry and returns `lease_expires_at`. A `completed` entry holds the reservation until it is released, so review time does not expire it. Pass `--token` or set `SYMBIOSIS_CLAIM_TOKEN` when journaling from a clone that did not make the claim. It records the current code commit, adds only the new journal file, and includes the task, run, event, actor, and provider as Git commit trailers. Git uses the configured person or service identity. Agents operating read-only can have the coordinator append an attributed journal commit for their result. Each writer uses their own Git credentials.

Retry an interrupted commit with the same event input and ID. A committed retry returns the original journal commit. A change to an existing entry requires a new event or follow-up run. If a branch push loses its acknowledgement, fetch the published branch and compare its tip with the local commit before resuming.

## Publish and preserve

Push each work branch to GitHub with a normal fast-forward push and open a PR linked to the task. Keep the original commits by enabling merge commits for audited branches. Merge the updated base into a published work branch when it needs to catch up. Block force pushes and deletion on the protected base and force pushes on published work branches. Configure merge-queue strategy accordingly if the repo uses a queue. [GitHub merge methods](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-methods-on-github)

Run the following from a full checkout using the trusted base and the original approved PR head SHA. Pass each included head and expected run for a combined integration.

```text
python -B <cli> --repo <checkout> verify-history --base <trusted-base> --candidate <tested-merge> --preserve <approved-pr-head> --require-run <run>
```

The check compares historical journal blobs byte for byte, verifies each new event against its dedicated, attributed commit, validates delegation and predecessor links, and confirms that the protected base, supplied PR heads, and new journal source commits are ancestors of the candidate. `--require-run` checks that the candidate adds the specified run's completion. Use a trusted copy of the CLI in required GitHub checks. Keep the expected PR heads and runs in the approved integration inputs. The normal `verify` command still checks the detailed source evidence.

After merge, every authorized clone can read the same run timeline via `memory --ref origin/main run <run>` or the read-only MCP `memory_run` tool. Use `memory_event` to read the full rationale and check summary, or GitHub to view the event file and its commit history.
