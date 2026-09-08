# serde.zig benchmark

Real-world benchmarks of `serde.zig` on JSON and MessagePack files.

## Quick start

Install the Zig version pinned in `mise.toml`, then run:

```sh
mise install
```

## Benchmark runners

### `bench.py`

`uv run bench.py` runs all benchmarks, writes the report to
`results/single_thread/`, then opens it in your browser.

Results are organized by format and implementation. The page leads with a
combined-ops/s-and-size ranking over the shared typed corpus, then presents
separate JSON and MessagePack decode/encode charts.

It builds with `-Doptimize=ReleaseFast` and has no Python dependencies. The
page loads Highcharts from a CDN, so the first open needs network.

```sh
uv run bench.py
```

### `bench-parallel.py`

The parallel runner measures JSON and MessagePack scaling with independent
worker arenas. Use `--mode generic`, `typed`, or `all`; `all` runs both
representations and keeps them separate in the exports and charts. JSON
compares serde.zig with `std.json`; MessagePack has only the serde.zig series.

```sh
uv run bench-parallel.py
```

The report is written to `results/parallel/`. Results are organized by format
and implementation.

Thread counts double from one worker and include the selected maximum when it
is not a power of two.

## Options

| Script | Option | Description |
| --- | --- | --- |
| Both | `--mode generic\|typed\|all` | Select representation(s) (`all` runs both; default: `all` in bench.py, `typed` in bench-parallel.py). |
| Both | `--format json\|msgpack\|all` | Select input format(s). |
| Both | `--runs N` | Independent process runs (default: 10). |
| Both | `--output csv\|md` | Write only selected export(s); repeatable. |
| Both | `--no-plot` | Write CSV and Markdown without HTML. |
| Both | `--output-dir PATH` | Override the result directory. |
| Both | `--zig PATH` | Zig executable to invoke. |
| Both | `--optimize MODE` | Zig optimization mode (default: `ReleaseFast`). |
| Both | `--plot-only` | Rebuild selected reports from `measurements.json`. |
| `bench-parallel.py` | `--thread N` | Maximum worker threads (default: all logical CPUs). |

Zig versions are managed with `mise`. The default is 0.16.0. Use
`mise -E zig17 exec -- uv run bench.py` for the dev toolchain.
