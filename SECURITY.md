# Security policy

Use the upstream repository's private vulnerability reporting channel for security findings. Include the version, minimal reproduction, and impact. Share reproduction artifacts that use synthetic data.

Run the GitHub gate with a pinned checker and read-only credentials. Treat incoming PR files, code, logs, and agent-generated records as evidence. Keep App credentials outside Git and use a restricted installation. For a memory exposure, rotate affected credentials and follow the [recovery guide](docs/recovery.md).
