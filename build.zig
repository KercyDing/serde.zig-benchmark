const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const Implementation = enum { serde, std, all };
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
        .root_source_file = b.path("src/json.zig"),
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
    // Each library completes only the tasks its own API supports,
    // sharing src/msgpack/shared.zig.
    const mod_msgpack_serde_bench = b.createModule(.{
        .root_source_file = b.path("src/msgpack/serde.zig"),
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
        .root_source_file = b.path("src/msgpack/msgpack-zig.zig"),
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
        .root_source_file = b.path("src/msgpack/zig-msgpack.zig"),
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

    const json_step = b.step("bench-json", "Run JSON benchmarks");
    const run_json = b.addRunArtifact(json_bench);
    run_json.addArg(@tagName(implementation));
    json_step.dependOn(&run_json.step);

    const msgpack_serde_step = b.step("bench-msgpack-serde", "Run serde.zig MessagePack task benchmarks");
    const run_msgpack_serde = b.addRunArtifact(msgpack_serde_bench);
    msgpack_serde_step.dependOn(&run_msgpack_serde.step);

    const msgpack_lal_step = b.step("bench-msgpack-msgpack-zig", "Run msgpack.zig (lalinsky) MessagePack task benchmarks");
    const run_msgpack_lal = b.addRunArtifact(msgpack_lal_bench);
    msgpack_lal_step.dependOn(&run_msgpack_lal.step);

    const msgpack_zigmp_step = b.step("bench-msgpack-zig-msgpack", "Run zig-msgpack MessagePack task benchmarks");
    const run_msgpack_zigmp = b.addRunArtifact(msgpack_zigmp_bench);
    msgpack_zigmp_step.dependOn(&run_msgpack_zigmp.step);
}
