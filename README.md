# serde.zig benchmark

Real-world benchmarks of `serde.zig` on JSON and MessagePack files.

Benchmarks are grouped by user-visible task, not library internals: each
chart only compares libraries that deliver the same result for the same
workload (encode known data, decode known data, load arbitrary data, transform data). There is no overall
winner ranking.

## Quick start

Install the Zig version pinned in `mise.toml`, then run:

```sh
mise install
```

## Benchmark runners

### `bench.py`

`uv run bench.py` runs every format, writes `index.html`,
`summary.csv`, and `summary.md` to `results/`, then opens the
page in your browser.

Results are grouped by user task (encode known data, decode known data, load
arbitrary data, transform data) per format; each task only includes the
libraries that can complete it with their native API. It builds with
`-Doptimize=ReleaseFast` and has no Python dependencies. The page loads
Highcharts from a CDN, so the first open needs network.

## Options
## Options

| Script | Option | Description |
| --- | --- | --- |
| `bench.py` | `--format json\|msgpack\|all` | Select input format(s). |
| `bench.py` | `--runs N` | Independent process runs (default: 10). |
| `bench.py` | `--output csv\|md` | Write only selected export(s); repeatable. |
| `bench-parallel.py` | `--thread N` | Maximum worker threads (default: all logical CPUs). |

The Zig version is pinned in `mise.toml` (0.16.0).
