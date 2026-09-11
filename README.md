# zig-serde-bench

Benchmarks for Zig JSON libraries using real-world data.

Libraries are compared by task: encode, decode, load, and transform. Each chart
only includes libraries that support that task.

## Implementations

| Name | Project | Adapter |
| --- | --- | --- |
| `serde` | [OrlovEvgeny/serde.zig](https://github.com/OrlovEvgeny/serde.zig) | [`src/json/serde.zig`](src/json/serde.zig) |
| `jsonz` | [KercyDing/jsonz](https://github.com/KercyDing/jsonz) | [`src/json/jsonz.zig`](src/json/jsonz.zig) |
| `std.json` | Zig standard library | [`src/json/std.zig`](src/json/std.zig) |

## Quick start

The Zig version is pinned in `mise.toml` (0.16.0). Install it and run the
benchmarks:

```sh
mise install
uv run bench.py
```

Reports are written to `results/json/`. With
`--parallel` the pages also chart aggregate throughput and speedup against the
thread count, next to the single-threaded comparison.

## Options

| Script | Option | Description |
| --- | --- | --- |
| `bench.py` | `--format json\|all` | Select the input format. |
| `bench.py` | `--runs N` | Independent process runs per thread count (default: 3). |
| `bench.py` | `--parallel [THREADS]` | Also measure 1, 2, 4, ... processes at once, up to `THREADS` (default: every machine thread), and chart the scaling. |
| `bench.py` | `--no-build` | Regenerate HTML, CSV, and Markdown from existing results. |
