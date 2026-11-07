# TypeScript repository benchmark

Measured on Windows with Python 3.12.4 and Git 2.47.1. The input was the public TypeScript tree at `f7973164a5f079a2d8b88d0fdb39347222549124`, compared with its parent `a3a20704642a0021a74bc56d266ef05be0604813`. [Benchmark source commit](https://github.com/microsoft/TypeScript/tree/f7973164a5f079a2d8b88d0fdb39347222549124)

| Operation | Input | Median of 3 runs |
| --- | --- | ---: |
| Full inventory | 68,517 tracked paths, 690 shards | 2.05 s |
| Incremental inventory | 2 changed paths across two snapshots | 1.45 s |
| Cold memory search | Real source tree plus 500 synthetic graph events | 1.11 s |

The query includes loading the Git tree and event blobs. Results exclude clone time, synthetic event creation, AI analysis, networked providers, and GitHub Actions. The source checkout stays intact. The benchmark adds the synthetic events to a temporary clone and reads their committed snapshot.

```text
git clone --depth 2 --no-tags --no-checkout https://github.com/microsoft/TypeScript.git
python examples/benchmark.py --repo TypeScript --records 500 --repeats 3
```

The command above uses the current tip. Supply `--rev` and `--since` for a pinned comparison when both commits are available.
