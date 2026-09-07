# serde.zig benchmark

Real-world benchmarks of `serde.zig` on JSON and MessagePack files.

## Quick start

Install the Zig version pinned in `mise.toml`, then run:

```sh
mise install
uv run bench.py
```

`uv run bench.py` runs all benchmarks and opens
`bench-results/index.html` in your browser. It builds with
`-Doptimize=ReleaseFast` and has no Python dependencies (the page loads
Highcharts from a CDN, so the first open needs network). Raw data stays in
`bench-results/`. The page leads with a centered JSON-vs-MessagePack decode
comparison over the typed corpus, then shows side-by-side JSON charts and
side-by-side MessagePack charts (each split into typed and generic-only
datasets).

Examples:

```sh
uv run bench.py --runs 20 --mode generic
uv run bench.py --format msgpack
uv run bench.py --output csv --output md
uv run bench.py --plot-only
```

- `--runs 20 --mode generic`: twenty process runs, generic representation only.
- `--format msgpack`: benchmark one format.
- `--output csv --output md`: also write summary tables.
- `--plot-only`: rebuild the page from saved results without rerunning.

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
