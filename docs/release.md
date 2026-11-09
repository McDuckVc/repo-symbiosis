# Release and distribution

Install the approved wheel in a clean environment. Python 3.10 and 3.12 on Windows, macOS, and Linux are the release CI matrix. Use Git 2.47 or later. MCP requires the optional `mcp` extra, and the self-hosted GitHub service requires `app`.

```text
python -m build
python -m twine check --strict dist/*
python -m pip install dist/repo_symbiosis-0.2.0-py3-none-any.whl
symbiosis --version
```

Release the wheel, source distribution, checksums, and notes from a reviewed Git tag. Pushing a `v<version>` tag runs `.github/workflows/release.yml`. The full integration and lint matrix runs against the tagged commit before publication. The release job checks the tag against `__version__`, builds and validates both distributions, and publishes the release with `SHA256SUMS` and the pinned `--checker` URL in its notes. Use the checksum-pinned public wheel to initialize a GitHub check. For a private installation, use a trusted internal artifact path and equivalent pinned workflow.

## Reproduce the demo

From the source distribution with the wheel installed:

```text
python examples/demo.py --directory ./symbiosis-demo
```

The demo keeps two work branches, their journals, the merged branch, a fresh clone, and the separate Git coordination branch. It simulates GitHub event payloads and performs real Git commits, pushes, and checks against a local bare remote. The workers are deterministic fixtures labeled by provider. It demonstrates both an accepted integration and a rejected out-of-scope change. The release demo bundle can also be cloned directly with `git clone repo-symbiosis-demo.bundle symbiosis-demo`.

## Go-live evidence

- Build and install the wheel and source distribution in clean environments.
- Confirm the Windows, macOS, and Linux GitHub CI jobs on the release commit.
- Run a real GitHub pilot with a contributor and a maintainer. Verify the scoped PR check, original commits, and a fresh-clone handoff.
- If using the App, verify signed delivery, required permissions, a claim and transfer, merged and closed PR receipts, a deployment receipt, and replay after a worker restart. See [App setup](app.md) and [recovery](recovery.md).
