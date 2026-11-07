# GitHub pilot setup

Use Git 2.47 or later and a full clone. Use the repository's default branch for App mode. Contributors work on same-repository task branches. The protected branch uses merge commits so original journal and code SHAs remain reachable.

## Bootstrap

Install the wheel on a maintainer machine. Initialize policy with a published wheel URL and the SHA-256 from the release assets. The verifier runs from that pinned artifact.

```text
symbiosis init --base main --checker https://github.com/OWNER/REPO/releases/download/v0.1.0/repo_symbiosis-0.1.0-py3-none-any.whl#sha256=HASH
symbiosis doctor --strict
```

Run `init` without `--checker` for a local pilot. It creates just the policy. Use `--mode github-app` for the [App](app.md). Bootstrap policy and workflow in a reviewed maintainer change before requiring the check. Keep the settings and workflow on the protected branch.

Configure GitHub to require a reviewed PR, CI, and **Symbiosis / history and scope**. Restrict merges to designated maintainers, block history rewrites, and protect the workflow and policy with required owner review or an organization-required workflow from a protected source. The generated workflow uses read-only credentials on `pull_request` and `merge_group`; it treats candidate commits as data. Keep the trusted workflow definition protected as well as the pinned wheel. For a queue, use its merge-commit strategy and run application tests on the combined candidate. [Secure GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use) · [Merge queues](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)

Use `origin` for the code and coordination branches in the generated workflow. For a separate, approved coordination repository, configure its read credentials in your trusted CI and pass `--coordination-remote NAME` after adding that remote. Keep privilege appropriate to the checkout trust model.

## Contributor path

1. Reserve the task, including source paths, `.symbiosis/changes/<task>.json`, and the run's journal directory. Create or check out the reserved branch.
2. Run `symbiosis manifest <task> --run <run> --claim-id <id>`. Commit the manifest with your work and append a completed journal using `agent-commit`.
3. Run `symbiosis check-scope <task> --remote origin --claim-id <id>`, stage the intended files, and run `symbiosis scan-memory`. Push the branch and link its PR to the task. Keep the claim active through merge.
4. The gate reads the policy from the protected base, checks the reservation against the committed PR diff, verifies the original PR head and completed journal in the candidate, and scans changed memory. A new commit or branch tip change needs a new result.
5. After merge, fetch the base and resume from Git or MCP. In cooperative mode, record the outcome and release the claim after confirming the merge commit. App mode records the receipt and releases the claim automatically.

The preview supports one change manifest per PR and same-repository branches. Merge-group checks resolve the current reserved branch tips and fail closed on a moving or missing tip. To onboard external fork contributors, define a separate maintainer review workflow before applying this gate to their PRs.

## Pilot verification

Use separate contributor and maintainer accounts. Verify that a valid PR passes, an out-of-scope PR fails, the contributor cannot merge or change the protected ledger, and the approved original commits can be fetched from a fresh clone. Run at least one real merge group if the repository uses a queue.
