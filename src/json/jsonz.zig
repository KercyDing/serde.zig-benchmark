const std = @import("std");
const jsonz = @import("jsonz");
const c = @import("common.zig");

pub fn main(init: std.process.Init.Minimal) !void {
    var args = try std.process.Args.Iterator.initAllocator(init.args, c.input_allocator);
    defer args.deinit();

    _ = args.skip();

    if (args.next() != null) return error.InvalidArguments;
    std.debug.print("JSON jsonz benchmark\ndata: data/json, input read and cleanup excluded\n", .{});

    for (c.datasets) |name| {
        if (!c.isKnownDataset(name)) continue;

        var path: [64]u8 = undefined;
        const file = try std.fmt.bufPrint(&path, "data/json/{s}", .{name});

        const input = try std.Io.Dir.cwd().readFileAlloc(std.Options.debug_io, file, c.input_allocator, .limited(c.data_limit));
        defer c.input_allocator.free(input);

        const repeats = c.repeatCount(input.len);
        std.debug.print("\n{s} ({d} bytes, {d} repeats)\n", .{ name, input.len, repeats });

        try known(name, input, repeats, false);
        try known(name, input, repeats, true);
    }
}

fn known(name: []const u8, input: []const u8, repeats: usize, encode: bool) !void {
    if (std.mem.eql(u8, name, "canada.json")) return run(c.CanadaDocument, input, repeats, encode);
    if (std.mem.eql(u8, name, "github_events.json")) return run([]const c.GithubEvent, input, repeats, encode);
    if (std.mem.eql(u8, name, "poet.json")) return run([]const c.Poem, input, repeats, encode);
    return run(c.TwitterDocument, input, repeats, encode);
}

fn run(comptime T: type, input: []const u8, repeats: usize, encode: bool) !void {
    var fixture = std.heap.ArenaAllocator.init(c.input_allocator);
    defer fixture.deinit();

    const value = try jsonz.fromSlice(T, fixture.allocator(), input, .{ .ignore_unknown_fields = true });

    if (!encode) {
        var elapsed: u64 = 0;
        for (0..repeats) |_| {
            var arena = std.heap.ArenaAllocator.init(c.input_allocator);
            defer arena.deinit();
            const start = c.nowNanoseconds();
            const result = try jsonz.fromSlice(T, arena.allocator(), input, .{ .ignore_unknown_fields = true });
            elapsed += @max(c.nowNanoseconds() - start, 1);
            std.mem.doNotOptimizeAway(result);
        }
        return report("jsonz known-decode", input.len, repeats, elapsed, null);
    }

    const warmup = try jsonz.toSlice(c.input_allocator, value, .{});
    defer c.input_allocator.free(warmup);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        const start = c.nowNanoseconds();
        const output = try jsonz.toSlice(c.input_allocator, value, .{});
        elapsed += @max(c.nowNanoseconds() - start, 1);
        std.mem.doNotOptimizeAway(output.ptr);
        c.input_allocator.free(output);
    }
    report("jsonz known-encode", warmup.len, repeats, elapsed, warmup.len);
}

fn report(label: []const u8, bytes: usize, repeats: usize, elapsed: u64, output: ?usize) void {
    const ms = @as(f64, @floatFromInt(elapsed)) / @as(f64, @floatFromInt(repeats)) / std.time.ns_per_ms;
    const mib = @as(f64, @floatFromInt(bytes * repeats)) / (@as(f64, @floatFromInt(elapsed)) / std.time.ns_per_s) / (1024.0 * 1024.0);
    std.debug.print("  {s}: {d:.6} ms/op, {d:.2} MiB/s", .{ label, ms, mib });

    if (output) |n| std.debug.print(" ({d} bytes)", .{n});
    std.debug.print("\n", .{});
}
