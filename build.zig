const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const serde = b.dependency("serde", .{
        .target = target,
        .optimize = optimize,
    });
    const jsonz = b.dependency("jsonz", .{ .target = target, .optimize = optimize });

    const mod_json_bench = b.createModule(.{
        .root_source_file = b.path("src/json/serde.zig"),
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

    const mod_std_json_bench = b.createModule(.{
        .root_source_file = b.path("src/json/std.zig"),
        .target = target,
        .optimize = optimize,
    });
    const std_json_bench = b.addExecutable(.{ .name = "std_json_bench", .root_module = mod_std_json_bench });
    std_json_bench.use_llvm = true;
    std_json_bench.root_module.link_libc = true;
    b.installArtifact(std_json_bench);

    const mod_jsonz_bench = b.createModule(.{ .root_source_file = b.path("src/json/jsonz.zig"), .target = target, .optimize = optimize, .imports = &.{.{ .name = "jsonz", .module = jsonz.module("jsonz") }} });
    const jsonz_bench = b.addExecutable(.{ .name = "jsonz_bench", .root_module = mod_jsonz_bench });
    jsonz_bench.use_llvm = true;
    jsonz_bench.root_module.link_libc = true;
    b.installArtifact(jsonz_bench);

    const json_step = b.step("bench-json-serde", "Run serde.zig JSON task benchmarks");
    const run_json = b.addRunArtifact(json_bench);
    json_step.dependOn(&run_json.step);

    const std_json_step = b.step("bench-json-std", "Run std.json JSON task benchmarks");
    const run_std_json = b.addRunArtifact(std_json_bench);
    std_json_step.dependOn(&run_std_json.step);

    const jsonz_step = b.step("bench-json-jsonz", "Run jsonz JSON task benchmarks");
    const run_jsonz = b.addRunArtifact(jsonz_bench);
    jsonz_step.dependOn(&run_jsonz.step);

}
