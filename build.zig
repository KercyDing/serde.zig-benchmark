const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const BenchMode = enum { generic, typed };
    const ParallelFormat = enum { json, msgpack, all };
    const mode = b.option(BenchMode, "mode", "Benchmark representation: generic or typed") orelse .generic;
    const max_threads = b.option(usize, "max-threads", "Maximum worker count for the parallel benchmark") orelse 16;
    const format = b.option(ParallelFormat, "format", "Format for the parallel benchmark") orelse .all;

    const serde = b.dependency("serde", .{
        .target = target,
        .optimize = optimize,
    });

    const json_mod = b.createModule(.{
        .root_source_file = b.path("bench/json.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "serde", .module = serde.module("serde") },
        },
    });

    const msgpack_mod = b.createModule(.{
        .root_source_file = b.path("bench/msgpack.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "serde", .module = serde.module("serde") },
        },
    });

    const parallel_mod = b.createModule(.{
        .root_source_file = b.path("bench/parallel.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "serde", .module = serde.module("serde") },
        },
    });

    const json_bench = b.addExecutable(.{
        .name = "json-bench",
        .root_module = json_mod,
    });
    json_bench.use_llvm = true;
    json_bench.root_module.link_libc = true;
    b.installArtifact(json_bench);

    const msgpack_bench = b.addExecutable(.{
        .name = "msgpack-bench",
        .root_module = msgpack_mod,
    });
    msgpack_bench.use_llvm = true;
    msgpack_bench.root_module.link_libc = true;
    b.installArtifact(msgpack_bench);

    const parallel_bench = b.addExecutable(.{
        .name = "parallel-bench",
        .root_module = parallel_mod,
    });
    parallel_bench.use_llvm = true;
    parallel_bench.root_module.link_libc = true;
    b.installArtifact(parallel_bench);

    const json_step = b.step("bench-json", "Run JSON benchmarks");
    const run_json = b.addRunArtifact(json_bench);
    run_json.addArg(@tagName(mode));
    json_step.dependOn(&run_json.step);

    const msgpack_step = b.step("bench-msgpack", "Run MessagePack benchmarks");
    const run_msgpack = b.addRunArtifact(msgpack_bench);
    run_msgpack.addArg(@tagName(mode));
    msgpack_step.dependOn(&run_msgpack.step);

    const parallel_step = b.step("bench-parallel", "Run JSON and MessagePack parallel benchmarks");
    const run_parallel = b.addRunArtifact(parallel_bench);
    run_parallel.addArg(b.fmt("{d}", .{max_threads}));
    run_parallel.addArg(@tagName(format));
    parallel_step.dependOn(&run_parallel.step);
}
