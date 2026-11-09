# Self-host the GitHub App

The optional App handles identity-bound claims, maintainer transfers, and Git-backed outcome receipts. The PR gate remains a separate GitHub check. This service fits a single installation host with durable local storage. It uses GitHub.com REST and same-repository PRs targeting the default branch.

## Configure GitHub

1. Create and install a GitHub App on the approved repositories. Set the webhook URL to the HTTPS endpoint `/github/webhook`. Generate an RSA private key and a webhook secret of at least 32 bytes.
2. Grant **Contents write**, **Issues read**, **Pull requests read**, and **Deployments read**. GitHub adds Metadata read. Subscribe to Issue comment, Pull request, and Deployment status events.
3. Commit `.symbiosis/policy.json` in `github-app` mode to the default branch. Add `.symbiosis/backlog.json` using the backlog schema in the [delivery guide](../skills/repo-symbiosis/references/delivery.md). Name tasks `issue-<GitHub issue number>`; include each change manifest, journal directory, and source path in the reservation. Allow the App alone to update `symbiosis-coordination`, with force pushes and deletion blocked. Keep contributor code branches writable under your usual rules.
4. If converting from cooperative mode, close or release the earlier reservations before changing policy. The App accepts GitHub-issued reservations.

[GitHub App installation authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation) · [Webhook signature validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)

## Run the service

Install `repo-symbiosis[app]` from the trusted wheel in an isolated environment. Set these values using the host's secret manager.

| Setting | Process | Value |
| --- | --- | --- |
| `SYMBIOSIS_GITHUB_REPOSITORY_IDS` | Both | Comma-separated numeric repository allowlist |
| `SYMBIOSIS_GITHUB_WEBHOOK_SECRET` | Webhook | Shared webhook signing secret |
| `SYMBIOSIS_GITHUB_APP_ID` | Worker | Numeric App ID |
| `SYMBIOSIS_GITHUB_PRIVATE_KEY_FILE` | Worker | Path to mounted GitHub App PEM |

```text
symbiosis-app --db /var/lib/symbiosis/queue.sqlite serve --listen 127.0.0.1 --port 8080
symbiosis-app --db /var/lib/symbiosis/queue.sqlite worker
symbiosis-app --db /var/lib/symbiosis/queue.sqlite status
```

Run both processes under a service manager on one host, with the same durable SQLite file. Terminate TLS at a reverse proxy and set a one-megabyte webhook body limit. The webhook process accepts a delivery after durable enqueue. The worker authenticates with a short-lived token restricted to the selected repository and rechecks GitHub state. Git writes are fast-forward-only. Monitor the worker and failed deliveries separately from the webhook `/health` endpoint. A redelivered event is idempotent and Git retains the durable receipt.

## Team commands and outcomes

On an open issue in the backlog, use `/symbiosis claim`. The worker verifies the comment author's GitHub ID and repository write access. Use `symbiosis claims --remote origin` to obtain the reserved branch, scope and claim ID. A maintainer can use `/symbiosis transfer @new-holder`, which preserves the branch and rotates the claim ID. Coordinate with the former holder, update the unmerged manifest with the new claim ID, and have the new holder open a PR using the existing branch history. Use `/symbiosis abandon` after closing any active PR. A reopened PR needs a matching active reservation.

On a closed, registered PR, the worker fetches the live PR state, matches the author and manifest to the current claim, and records the outcome. Merged PRs must preserve their original head on the protected base. Deployment receipts record terminal GitHub deployment statuses for commits on that base. They describe observed outcomes. Release approvals and deployment actions remain with the repository's configured workflow.

Read the receipts with `symbiosis audit --remote origin` or through the coordination branch on GitHub. Each ledger write keeps the newest 1,000 receipts in `claims.json`, which stays near 300 KiB. Older receipts remain in the coordination branch history; read them with `git log -p origin/symbiosis-coordination -- claims.json`. Receipts from the last 7 days are never pruned because they guard against duplicate webhook deliveries. The REST-backed ledger still stops writes before its state file reaches 900 KiB, which takes several thousand receipts within one week.
