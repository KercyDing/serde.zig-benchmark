# zig-serde-bench

Benchmarks for Zig JSON and MessagePack libraries using real-world data.

Libraries are compared by task: encode, decode, load, and transform. Each chart
only includes libraries that support that task.

## Implementations

### JSON

| Name | Project | Adapter |
| --- | --- | --- |
| `serde` | [OrlovEvgeny/serde.zig](https://github.com/OrlovEvgeny/serde.zig) | [`src/json/serde.zig`](src/json/serde.zig) |
| `jsonz` | [KercyDing/jsonz](https://github.com/KercyDing/jsonz) | [`src/json/jsonz.zig`](src/json/jsonz.zig) |
| `std.json` | Zig standard library | [`src/json/std.zig`](src/json/std.zig) |

### MessagePack

| Name | Project | Adapter |
| --- | --- | --- |
| `serde` | [OrlovEvgeny/serde.zig](https://github.com/OrlovEvgeny/serde.zig) | [`src/msgpack/serde.zig`](src/msgpack/serde.zig) |
| `msgpack.zig` | [lalinsky/msgpack.zig](https://github.com/lalinsky/msgpack.zig) | [`src/msgpack/msgpack-zig.zig`](src/msgpack/msgpack-zig.zig) |
| `zig-msgpack` | [zigcc/zig-msgpack](https://github.com/zigcc/zig-msgpack) | [`src/msgpack/zig-msgpack.zig`](src/msgpack/zig-msgpack.zig) |

## Quick start

The Zig version is pinned in `mise.toml` (0.16.0). Install it and run the
benchmarks:

```sh
mise install
uv run bench.py
```

Reports are written to `results/json/` and `results/msgpack/`.

## Options

| Script | Option | Description |
| --- | --- | --- |
| `bench.py` | `--format json\|msgpack\|all` | Select input format(s). |
| `bench.py` | `--runs N` | Independent process runs (default: 10). |
| `bench.py` | `--no-build` | Regenerate HTML, CSV, and Markdown from existing results. |
