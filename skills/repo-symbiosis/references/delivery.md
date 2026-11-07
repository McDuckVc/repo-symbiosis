# Parallel delivery

## Join an active project

Read the knowledge pack entry point and fetch the protected base. Find your task ID in the team's backlog and inspect any linked PR, its head SHA, review status, and required checks. Search memory for the task ID with `--history` and trace the affected components. Read the remote claims to see the active owner and paths. For a handoff, agree on ownership and the branch with the current contributor before starting a new reservation or editing their PR.

## Turn requests into a plan

Resolve existing issue and PR references against the target repository. Capture requirements, acceptance checks, variants, dependencies, rollback needs, and whether each item is already merged. Analyze high-risk dependencies with `search`, `trace`, and source verification. Model dependencies for changes that must land first. Model shared write paths or resources for changes that need exclusive access. Examples include a schema, API contract, dependency lockfile, rollout flag, or shared staging environment. Split broad requests by independently testable outcomes and keep an explicit shared-contract task when multiple features depend on it.

Write a backlog JSON file. `completed` contains dependencies verified as merged into the target base. `paths` contains literal repository-relative paths or prefixes ending in `/`. Reserve exact files where possible. Include intended Markdown and unique event paths. `resources` contains shared lock names. Higher integer `priority` goes first among ready tasks.

```json
{
  "completed": [],
  "tasks": [
    {
      "id": "payments-contract",
      "title": "Make the payment API accept request IDs",
      "paths": ["payments/api.py", ".symbiosis/events/task-payments-contract-01.json"],
      "resources": ["payments-api"],
      "acceptance": ["Existing clients still work", "Duplicate IDs are covered by a test"]
    },
    {
      "id": "retry-payment",
      "title": "Retry payment submissions safely",
      "paths": ["checkout/retry.py"],
      "resources": ["payments-api"],
      "depends_on": ["payments-contract"]
    }
  ]
}
```

`plan backlog.json --capacity 4 --remote <coordination-remote>` produces dependency waves and lists tasks blocked by active reservations. Keep the normalized backlog in the team's approved tracker or in a reviewed project file. The waves are projections. Recheck the remote base, confirmed completions, and claims before each dispatch. A later wave can begin as soon as its own dependencies and resources clear.

## Reserve and work

Agree on one writable coordination remote for the project and protected base. It can be the main repository or a separate private team Git repository for contributors who submit code from forks. A named remote must have one matching fetch/push URL. The tool writes only `refs/heads/symbiosis-coordination` there. The branch contains active claims, merged task IDs, and their history, separate from the code branch. Use distinct IDs for a revert or follow-up. Restrict the branch to the team, disable force pushes and deletion, and exclude it from deployment triggers. Writers are trusted collaborators. Claim owner strings and IDs distinguish sessions and retries. GitHub permissions control access to the remote.

```text
python <cli> --repo <repo> claims --remote <coord>
python <cli> --repo <repo> claim <task> --remote <coord> --plan <backlog> --owner <user-session> --base <protected-tip-sha> --branch symbiosis/<task-session>
python <cli> --repo <repo> worktree <task> --remote <coord> --claim-id <id> --path <new-directory>
python <cli> --repo <worktree> check-scope <task> --remote <coord> --claim-id <id>
```

The claim operation retries competing fast-forward pushes against fresh state. A conflicting reservation blocks the new task. Keep the claim active through review and merge. When scope changes, stop dispatch, coordinate with the owners, and update the plan and reservation before continuing. An abandoned claim remains visible until its owner releases it or a maintainer verifies the work was handed off or abandoned and uses the recorded owner and claim ID to clear it. Reuse an existing worktree on resume. Preserve published commits by merging updates from the protected base and pass `--against <verified-updated-protected-base>` for the scope check.

Run independent task implementations concurrently when authorized and available. Use each task's isolated worktree, reviewed patch, and acceptance criteria. Reserve a [journal directory](agent-journal.md) for each run and commit its attributed progress alongside the code and component Markdown. Recheck actual changed paths, hidden coupling, source evidence, and acceptance tests before publishing. Coordinate changes to shared schemas and migration order explicitly even if Git reports no file conflict.

For a repo with Symbiosis GitHub policy, reserve `.symbiosis/changes/<task>.json` too. Create it with `manifest <task> --run <run> --claim-id <id> --remote <coord>`, commit it, and run `scan-memory` on staged files before publishing. In App mode obtain the reservation through `/symbiosis claim` on the GitHub issue and use `audit --remote <coord>` to read its receipts.

## Publish to GitHub

Confirm the owner, repository, base, code/data scope, and requested visibility before first publication. Inspect the actual Git remotes and GitHub identity. Use the authenticated GitHub connector if it supports the operations, or Git and GitHub CLI. For a new repository with authorization, create an empty repo such as `gh repo create OWNER/REPO --private`. Initialize only the approved history, establish the intended default branch, then apply its rules. For an existing repository, push only the feature branch to the approved remote. Open a draft with `gh pr create --repo OWNER/REPO --base BASE --head HEAD --draft --title TITLE --body-file BODY`. Use the appropriate fork-qualified head when applicable. Check for an existing PR for the head/base first, including after a timed-out create. Include task ID, dependencies, changed areas, verification results, migration/rollback notes, and memory changes. [Repository creation](https://cli.github.com/manual/gh_repo_create), [draft PRs](https://cli.github.com/manual/gh_pr_create).

Once the actual merged result is confirmed on the protected base, record the integration SHA, PR link, checks and follow-ups in a new task event. Include the event in a follow-up or a batched outcomes PR. Release the reservation with `release-claim <task> --remote <coord> --claim-id <id> --owner <user-session> --outcome merged --merge-rev <integration-sha>`. The next task's claimed base must contain each recorded prerequisite's integration commit. Use `abandoned` after confirming the PR and work are no longer active. A draft, a worker success, or a request for review is a separate state.

## Configure maintainer gates

- Keep contributors on feature branches or forks. Restrict updates to protected integration and release branches to designated maintainers, a vetted merge queue, or an authorized merge app. Require PR review, current approvals, relevant owners, CI, and resolution of conversations. Verify effective actor permissions with the actual repository plan and settings. Where the platform cannot restrict merge access as intended, use fork PRs with write access to the base limited to authorized maintainers. [Protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).
- Use a merge queue where supported. Ensure required checks run on `pull_request` and `merge_group`, including validation of the combined code and memory against the applicable base. Else require an up-to-date branch and rerun checks before each merge. Use a protected/pinned copy of the CLI for required checks and a checkout with the base and relevant evidence history. Keep validation jobs read-only. [Merge queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue).
- Keep production credentials in protected jobs. Use scoped, short-lived deployment credentials and explicit environment restrictions. Verify environment reviewer availability on the repository plan or use the team's external deployment approval gate. [Environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments), [OIDC](https://docs.github.com/en/enterprise-cloud@latest/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-cloud-providers).

## Freeze, stage, promote

Use per-PR previews or a staging gate before integration where the project supports them. After review and integration, freeze the approved release candidate as an immutable commit and artifact digest. Record the frozen candidate, deploy that exact artifact to staging, run smoke and migration checks, and record the staging evidence. After the release authority approves it, promote that same artifact through the protected production workflow. Verify its health and record the production deployment ID, digest, and evidence. For a failure, append a failure or rollback event with the observed result and use the project's rollback runbook.

Keep independent preview environments and independently deployable services parallel. Serialize shared environments, ordered migrations and conflicting resource updates. Local reservations reduce collisions. The protected base and CI are responsible for detecting cross-task integration failures.
