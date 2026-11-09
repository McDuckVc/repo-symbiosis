# Recover and upgrade

## Interrupted work

1. Fetch the protected branch and the published task branch. Compare your local and remote tips before retrying a push. When the remote already has the exact SHA, continue from that commit.
2. Read `symbiosis claims --remote origin` and `symbiosis memory --ref <published-branch> run <run>`. Reuse the reserved branch and existing worktree where available. On a fresh device, check out the published branch and repeat the scope check.
3. Retry `agent-commit` with the same event ID and JSON. It returns the original journal commit when the event was already committed. Record new work with a new journal event.
4. For an abandoned reservation, confirm there is no active PR. In App mode use the authorized issue command. In cooperative mode, a crashed agent's reservation expires with its lease and stops blocking other work; nobody needs to act. To clear a held reservation whose holder is gone, use `release-claim` with the recorded owner and claim ID plus `--override "<reason>"`. For a merge, fetch the actual integration SHA and verify ancestry before recording completion.
5. To resume a lapsed lease that nobody has taken over, run `renew-claim` from the clone that made the claim, or pass its `--token`. If the reservation was already removed, claim the task again and regenerate the change manifest with the new claim ID.

## Emergency hotfixes

A production fix may need paths that another task has reserved, and the full claim, manifest and journal flow can be too slow during an incident.

1. Open the hotfix PR from a branch off the protected base. Keep it small, and leave `.symbiosis/` and `docs/symbiosis/` unchanged.
2. A maintainer on the ruleset bypass list merges it after review and the application CI. The bypass appears in GitHub's ruleset insights and audit log.
3. Record a `decision` or `failure` event for the incident in a follow-up PR. Include the hotfix merge SHA and the reservations it touched.
4. Tell the holders of affected reservations. Each holder merges the updated protected base into the task branch, resolves conflicts, and runs `check-scope <task> --remote origin --claim-id <id> --against <new-base>`. Changes that arrive from the base do not count against the reservation, so their claims stay valid.

When a hotfix touches no reserved paths and time allows, use the normal reserved-task flow instead.

## App downtime

Restart the webhook process and worker with the existing queue volume. Inspect `symbiosis-app --db PATH status`. Correct the failed delivery's permissions, repository policy, or network problem, then use `symbiosis-app --db PATH retry DELIVERY_ID`. The worker re-reads GitHub state. If GitHub missed delivery during an outage, use GitHub's delivery interface to redeliver it. Back up SQLite using its online backup facility or a consistent stopped-service volume snapshot. Keep the Git repository and its protected coordination branch backed up independently. [GitHub webhook delivery recovery](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries)

## Sensitive data exposure

Pause publishing and rotate or revoke affected credentials. Contact the repository security owner. Shared Git history may need a coordinated cleanup involving clones and forks. Scan staged memory with `symbiosis scan-memory`; review the text for customer data and other information outside the built-in credential patterns. [GitHub sensitive data removal](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)

## Upgrade and rollback

Upgrade the checker before enabling v0.2 ledger writes. The v0.1 checker rejects reservations that contain v0.2 leases or holder token hashes. The v0.2 checker reads both formats. Existing reservations keep their claim IDs, scope, and original authorization behavior.

1. Pause new claims and scope changes. Record the protected base and coordination branch SHAs, then test the v0.2 wheel against a clone with the existing ledger and journals.
2. Update the wheel URL and SHA-256 in the protected workflow through a reviewed PR. Verify an existing reservation with the new checker on GitHub.
3. Upgrade the App, CLI, and MCP installations before resuming claims. Check that the updated gate accepts a new reservation and that a fresh clone can still read the earlier journals.

If a v0.2 writer already updated the ledger and the v0.1 check is failing, pause new writes. Have a designated maintainer use the configured ruleset bypass to land the reviewed checker update, then rerun the affected PR checks. The checker update can recover the gate while keeping the existing coordination and journal commits.

To roll back to v0.1, keep v0.2 readers active while the team finishes or explicitly releases incompatible reservations with v0.2 tooling. Verify the current ledger with the intended v0.1 checker in a test clone before restoring that pin. If active work needs the new fields, keep the v0.2 checker in place while resolving the underlying problem. Preserve all existing Git history through the rollout and recovery.
