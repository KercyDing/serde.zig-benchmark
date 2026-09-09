const std = @import("std");
const shared = @import("shared.zig");

pub fn run(comptime Adapter: type, init: std.process.Init.Minimal) !void {
    var args = try std.process.Args.Iterator.initAllocator(init.args, shared.input_allocator);
    defer args.deinit();
    _ = args.skip();
    if (args.next() != null) return error.InvalidArguments;

    std.debug.print("JSON {s} benchmark\n", .{Adapter.name});
    std.debug.print("data: data/json, input read and cleanup excluded\n", .{});

    for (shared.datasets) |dataset| {
        var path_buffer: [64]u8 = undefined;
        const path = try std.fmt.bufPrint(&path_buffer, "data/json/{s}", .{dataset});
        const input = try std.Io.Dir.cwd().readFileAlloc(
            std.Options.debug_io,
            path,
            shared.input_allocator,
            .limited(shared.data_limit),
        );
        defer shared.input_allocator.free(input);

        const repeats = shared.repeatCount(input.len);
        std.debug.print("\n{s} ({d} bytes, {d} repeats)\n", .{ dataset, input.len, repeats });
        if (shared.isKnownDataset(dataset)) {
            try runKnown(Adapter, dataset, input, repeats, false);
            try runKnown(Adapter, dataset, input, repeats, true);
        }
        if (comptime Adapter.supports_arbitrary) {
            try decode(Adapter, Adapter.Arbitrary, "arbitrary-decode", input, repeats);
            try transform(Adapter, Adapter.Arbitrary, input, repeats);
        }
    }
}

fn runKnown(comptime Adapter: type, dataset: []const u8, input: []const u8, repeats: usize, encode_value: bool) !void {
    if (std.mem.eql(u8, dataset, "canada.json")) return known(Adapter, shared.CanadaDocument, input, repeats, encode_value);
    if (std.mem.eql(u8, dataset, "github_events.json")) return known(Adapter, []const shared.GithubEvent, input, repeats, encode_value);
    if (std.mem.eql(u8, dataset, "poet.json")) return known(Adapter, []const shared.Poem, input, repeats, encode_value);
    return known(Adapter, shared.TwitterDocument, input, repeats, encode_value);
}

fn known(comptime Adapter: type, comptime T: type, input: []const u8, repeats: usize, encode_value: bool) !void {
    if (encode_value) return encode(Adapter, T, input, repeats);
    return decode(Adapter, T, "known-decode", input, repeats);
}

fn decode(comptime Adapter: type, comptime T: type, comptime task: []const u8, input: []const u8, repeats: usize) !void {
    var warmup_arena = std.heap.ArenaAllocator.init(shared.input_allocator);
    defer warmup_arena.deinit();
    _ = try Adapter.decode(T, warmup_arena.allocator(), input);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(shared.input_allocator);
        defer arena.deinit();
        const start = shared.nowNanoseconds();
        const value = try Adapter.decode(T, arena.allocator(), input);
        elapsed += @max(shared.nowNanoseconds() - start, 1);
        std.mem.doNotOptimizeAway(value);
    }
    report(Adapter.name ++ " " ++ task, input.len, repeats, elapsed, null);
}

fn encode(comptime Adapter: type, comptime T: type, input: []const u8, repeats: usize) !void {
    var fixture_arena = std.heap.ArenaAllocator.init(shared.input_allocator);
    defer fixture_arena.deinit();
    const value = try Adapter.decode(T, fixture_arena.allocator(), input);
    const warmup = try Adapter.encode(shared.input_allocator, value);
    defer shared.input_allocator.free(warmup);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        const start = shared.nowNanoseconds();
        const output = try Adapter.encode(shared.input_allocator, value);
        elapsed += @max(shared.nowNanoseconds() - start, 1);
        std.mem.doNotOptimizeAway(output.ptr);
        shared.input_allocator.free(output);
    }
    report(Adapter.name ++ " known-encode", warmup.len, repeats, elapsed, warmup.len);
}

fn transform(comptime Adapter: type, comptime T: type, input: []const u8, repeats: usize) !void {
    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(shared.input_allocator);
        defer arena.deinit();
        const start = shared.nowNanoseconds();
        const value = try Adapter.decode(T, arena.allocator(), input);
        const output = try Adapter.encode(shared.input_allocator, value);
        elapsed += @max(shared.nowNanoseconds() - start, 1);
        std.mem.doNotOptimizeAway(output.ptr);
        shared.input_allocator.free(output);
    }
    report(Adapter.name ++ " transform", input.len, repeats, elapsed, null);
}

fn report(label: []const u8, bytes: usize, repeats: usize, elapsed: u64, output: ?usize) void {
    const milliseconds = @as(f64, @floatFromInt(elapsed)) / @as(f64, @floatFromInt(repeats)) / std.time.ns_per_ms;
    const seconds = @as(f64, @floatFromInt(elapsed)) / std.time.ns_per_s;
    const mib_per_second = @as(f64, @floatFromInt(bytes * repeats)) / seconds / (1024.0 * 1024.0);
    std.debug.print("  {s}: {d:.6} ms/op, {d:.2} MiB/s", .{ label, milliseconds, mib_per_second });
    if (output) |len| std.debug.print(" ({d} bytes)", .{len});
    std.debug.print("\n", .{});
}
