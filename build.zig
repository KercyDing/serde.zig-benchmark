const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const BenchMode = enum { generic, typed };
    const ParallelFormat = enum { json, msgpack, all };
    const Implementation = enum { serde, std, all };
    const mode = b.option(BenchMode, "mode", "Benchmark representation: generic or typed") orelse .generic;
    const max_threads = b.option(usize, "max-threads", "Maximum worker count for the parallel benchmark") orelse 16;
    const format = b.option(ParallelFormat, "format", "Format for the parallel benchmark") orelse .all;
    const implementation = b.option(Implementation, "implementation", "Implementation for JSON benchmarks") orelse .all;

    const serde = b.dependency("serde", .{
        .target = target,
        .optimize = optimize,
    });
    const lalinsky = b.dependency("msgpack", .{
        .target = target,
        .optimize = optimize,
    });
    const zig_msgpack = b.dependency("zig_msgpack", .{
        .target = target,
        .optimize = optimize,
    });

    const mod_json_bench = b.createModule(.{
        .root_source_file = b.path("bench/json.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "serde", .module = serde.module("serde") },
        },
    });
    const json_bench = b.addExecutable(.{ .name = "json_bench", .root_module = mod_json_bench });
    json_bench.use_llvm = true;
    json_bench.root_module.link_libc = true;
    b.installArtifact(json_bench);

    // One executable per MessagePack library: each benchmarks the tasks its
    // native API supports (typed structs for serde.zig and msgpack.zig,
    // generic values for zig-msgpack), sharing bench/msgpack/shared.zig.
    const mod_msgpack_serde_bench = b.createModule(.{
        .root_source_file = b.path("bench/msgpack/serde.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "serde", .module = serde.module("serde") },
        },
    });
    const msgpack_serde_bench = b.addExecutable(.{ .name = "msgpack_serde_bench", .root_module = mod_msgpack_serde_bench });
    msgpack_serde_bench.use_llvm = true;
    msgpack_serde_bench.root_module.link_libc = true;
    b.installArtifact(msgpack_serde_bench);

    const mod_msgpack_lal_bench = b.createModule(.{
        .root_source_file = b.path("bench/msgpack/msgpack-zig.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "serde", .module = serde.module("serde") },
            .{ .name = "msgpack_lalinsky", .module = lalinsky.module("msgpack") },
        },
    });
    const msgpack_lal_bench = b.addExecutable(.{ .name = "msgpack_lal_bench", .root_module = mod_msgpack_lal_bench });
    msgpack_lal_bench.use_llvm = true;
    msgpack_lal_bench.root_module.link_libc = true;
    b.installArtifact(msgpack_lal_bench);

    const mod_msgpack_zigmp_bench = b.createModule(.{
        .root_source_file = b.path("bench/msgpack/zig-msgpack.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "zig_msgpack", .module = zig_msgpack.module("msgpack") },
        },
    });
    const msgpack_zigmp_bench = b.addExecutable(.{ .name = "msgpack_zigmp_bench", .root_module = mod_msgpack_zigmp_bench });
    msgpack_zigmp_bench.use_llvm = true;
    msgpack_zigmp_bench.root_module.link_libc = true;
    b.installArtifact(msgpack_zigmp_bench);

    const mod_parallel_bench = b.createModule(.{
        .root_source_file = b.path("bench/parallel.zig"),
        .target = target,
        .optimize = optimize,
        .imports = &.{
            .{ .name = "serde", .module = serde.module("serde") },
        },
    });
    const parallel_bench = b.addExecutable(.{ .name = "parallel_bench", .root_module = mod_parallel_bench });
    parallel_bench.use_llvm = true;
    parallel_bench.root_module.link_libc = true;
    b.installArtifact(parallel_bench);

    const json_step = b.step("bench-json", "Run JSON benchmarks");
    const run_json = b.addRunArtifact(json_bench);
    run_json.addArg(@tagName(mode));
    run_json.addArg(@tagName(implementation));
    json_step.dependOn(&run_json.step);

    const msgpack_serde_step = b.step("bench-msgpack-serde", "Run serde.zig MessagePack (typed + generic) benchmarks");
    const run_msgpack_serde = b.addRunArtifact(msgpack_serde_bench);
    run_msgpack_serde.addArg(@tagName(mode));
    msgpack_serde_step.dependOn(&run_msgpack_serde.step);

    const msgpack_lal_step = b.step("bench-msgpack-msgpack-zig", "Run msgpack.zig (lalinsky) MessagePack typed benchmarks");
    const run_msgpack_lal = b.addRunArtifact(msgpack_lal_bench);
    run_msgpack_lal.addArg("typed");
    msgpack_lal_step.dependOn(&run_msgpack_lal.step);

    const msgpack_zigmp_step = b.step("bench-msgpack-zig-msgpack", "Run zig-msgpack MessagePack generic benchmarks");
    const run_msgpack_zigmp = b.addRunArtifact(msgpack_zigmp_bench);
    run_msgpack_zigmp.addArg("generic");
    msgpack_zigmp_step.dependOn(&run_msgpack_zigmp.step);

    const parallel_step = b.step("bench-parallel", "Run JSON and MessagePack parallel benchmarks");
    const run_parallel = b.addRunArtifact(parallel_bench);
    run_parallel.addArg(b.fmt("{d}", .{max_threads}));
    run_parallel.addArg(@tagName(format));
    run_parallel.addArg(@tagName(implementation));
    run_parallel.addArg(@tagName(mode));
    parallel_step.dependOn(&run_parallel.step);
}
