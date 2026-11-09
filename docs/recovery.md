# Recover and upgrade

## Interrupted work

1. Fetch the protected branch and the published task branch. Compare your local and remote tips before retrying a push. When the remote already has the exact SHA, continue from that commit.
2. Read `symbiosis claims --remote origin` and `symbiosis memory --ref <published-branch> run <run>`. Reuse the reserved branch and existing worktree where available. On a fresh device, check out the published branch and repeat the scope check.
3. Retry `agent-commit` with the same event ID and JSON. It returns the original journal commit when the event was already committed. Record new work with a new journal event.
4. For an abandoned reservation, confirm there is no active PR. In App mode use the authorized issue command. In cooperative mode, use `release-claim` with the recorded owner and claim ID. For a merge, fetch the actual integration SHA and verify ancestry before recording completion.

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

Pin the installed CLI, MCP server, and GitHub checker to a release. Run the new version against a test clone and verify existing events before updating the pinned checker in a reviewed PR. The preview uses version 1 policy, memory, and ledger schemas. To roll back a tool deployment, reinstall the previous compatible wheel and restore its checker pin. Keep journal and Git commit history intact. Project owners can disable the integration and retain all approved memory files in Git.
