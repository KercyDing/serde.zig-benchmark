# serde.zig benchmark

This repository measures `serde.zig` against real-world JSON documents, not
the small synthetic fixtures in `serde.zig`'s internal microbenchmarks.

The corpus contains Canada geo data, CITM catalog data, FGO data, GitHub event
data, Lottie, OTF font data, poetry, and Twitter documents. Each JSON input has
a semantically equivalent MessagePack file in `data/msgpack/`; the benchmark
reads that binary input directly, so MessagePack decoding and encoding timings
do not include JSON parsing or JSON-to-MessagePack conversion.

Run the JSON benchmarks:

```sh
zig build bench-json
```

Run the MessagePack benchmarks:

```sh
zig build bench-msgpack
```

Both targets default to the complete generic corpus. Use `-Dmode=typed` for
the typed subset. MessagePack runs always report both decoding and encoding.
