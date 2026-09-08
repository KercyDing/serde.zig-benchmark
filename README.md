# serde.zig benchmark

Real-world benchmarks of `serde.zig` on JSON and MessagePack files.

## Quick start

Install the Zig version pinned in `mise.toml`, then run:

```sh
mise install
uv run bench.py
```

`uv run bench.py` runs all benchmarks and writes `index.html`,
`summary.csv` and `summary.md` into `results/single_thread/`, then opens the page in
your browser. It builds with `-Doptimize=ReleaseFast` and has no Python
dependencies (the page loads Highcharts from a CDN, so the first open needs
network). Raw JSON and MessagePack logs are kept separately in
`results/single_thread/json/` and `results/single_thread/msgpack/`.
The combined summary stays in `results/single_thread/`. The page leads with a throughput-
and-size ranking over the shared typed corpus, using geometric means to balance
the datasets. It then shows JSON-vs-MessagePack comparisons for decode and
encode, followed by JSON and MessagePack decode/encode charts with generic and
typed results where available.

Examples:

```sh
uv run bench.py --runs 20 --mode generic
uv run bench.py --format msgpack
uv run bench.py --no-plot
uv run bench.py --output csv
uv run bench.py --plot-only
```

- `--runs 20 --mode generic`: twenty process runs, generic representation only.
- `--format msgpack`: benchmark one format.
- `--no-plot`: write summary.csv and summary.md without the page.
- `--output csv`: write only summary.csv (or `--output md`).
- `--plot-only`: rebuild outputs from saved results without rerunning.

Zig versions are managed with `mise`. The default is 0.16.0. Use
`mise -E zig17 exec -- uv run bench.py` for the dev toolchain.

## Running zig build directly

Use `-Doptimize=ReleaseFast`; the default is Debug, which is useless for
timing.

```sh
zig build bench-json -Doptimize=ReleaseFast
zig build bench-msgpack -Doptimize=ReleaseFast
```

`-Dmode=typed` selects the typed subset.

## Parallel scaling

The parallel benchmark is separate from the single-threaded report. It runs
the typed JSON and MessagePack corpora with independent worker arenas and
reports total throughput plus speedup relative to one worker:

```sh
uv run bench-parallel.py
uv run bench-parallel.py --thread 8
uv run bench-parallel.py --format msgpack
```

The report is written to `results/parallel/index.html`; raw logs are separated
by format under `results/parallel/json/` and `results/parallel/msgpack/`.
The combined summary stays in `results/parallel/`. The tested thread counts
double from one worker and include the selected maximum when it is not a power
of two.
