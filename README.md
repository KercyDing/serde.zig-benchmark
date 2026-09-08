# serde.zig benchmark

Real-world benchmarks of `serde.zig` on JSON and MessagePack files.

Benchmarks are grouped by user-visible task, not library internals: each
chart only compares libraries that deliver the same result for the same
workload (typed encode/decode, generic decode/encode). There is no overall
winner ranking.

## Quick start

Install the Zig version pinned in `mise.toml`, then run:

```sh
mise install
```

## Benchmark runners

### `bench.py`

`uv run bench/bench.py` runs every format, writes `index.html`,
`summary.csv`, and `summary.md` to `results/single_thread/`, then opens the
page in your browser.

Results are grouped by user task (encode known data, decode known data, load
arbitrary data, transform data) per format; each task only includes the
libraries that can complete it with their native API. It builds with
`-Doptimize=ReleaseFast` and has no Python dependencies. The page loads
Highcharts from a CDN, so the first open needs network.

Thread scaling is the same engine with an extra dimension: pass `--thread N`
to sweep workers 1, 2, 4, ... N. Scaling results are written to
`scaling.csv`, `scaling.md`, and `scaling.html` next to the normal reports.

## Options
## Options

| Script | Option | Description |
| --- | --- | --- |
| `bench/bench.py` | `--format json\|msgpack\|all` | Select input format(s). |
| `bench/bench.py` | `--runs N` | Independent process runs (default: 10). |
| `bench/bench.py` | `--output csv\|md` | Write only selected export(s); repeatable. |
| `bench-parallel.py` | `--thread N` | Maximum worker threads (default: all logical CPUs). |

Zig versions are managed with `mise`. The default is 0.16.0. Use
`mise -E zig17 exec -- uv run bench/bench.py` for the dev toolchain.
