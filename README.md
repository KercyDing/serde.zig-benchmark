# serde.zig benchmark

Real-world benchmarks of `serde.zig` on JSON and MessagePack files.

## Quick start

Install the Zig version pinned in `mise.toml`, then run:

```sh
mise install
uv run bench.py
```

`uv run bench.py` runs all benchmarks and opens an interactive
`bench-results/index.html` in your browser. It builds with
`-Doptimize=ReleaseFast` and installs plotly automatically. Raw data stays in
`bench-results/`. Corpora with a static typed schema get their own charts; the
rest are shown separately as generic-only.

Examples:

```sh
uv run bench.py --runs 5 --mode generic
uv run bench.py --format msgpack
uv run bench.py --output csv --output md
uv run bench.py --plot-only
```

- `--runs 5 --mode generic`: five runs, generic representation only.
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
