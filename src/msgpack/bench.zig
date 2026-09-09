const std = @import("std");
const shared = @import("shared.zig");

pub fn run(comptime Adapter: type, init: std.process.Init.Minimal) !void {
    var args = try std.process.Args.Iterator.initAllocator(init.args, shared.input_allocator);
    defer args.deinit();
    _ = args.skip();
    if (args.next() != null) return error.InvalidArguments;

    std.debug.print("MessagePack {s} benchmark\n", .{Adapter.name});
    std.debug.print("data: data/msgpack, input read and cleanup excluded\n", .{});
    for (shared.datasets) |dataset| {
        if (!Adapter.supports_arbitrary and !shared.isKnownDataset(dataset)) continue;
        var path_buffer: [64]u8 = undefined;
        const stem = dataset[0 .. dataset.len - ".json".len];
        const path = try std.fmt.bufPrint(&path_buffer, "data/msgpack/{s}.msgpack", .{stem});
        const input = try std.Io.Dir.cwd().readFileAlloc(std.Options.debug_io, path, shared.input_allocator, .limited(shared.data_limit));
        defer shared.input_allocator.free(input);
        const repeats = shared.repeatCount(input.len);
        std.debug.print("\n{s} ({d} bytes, {d} repeats)\n", .{ dataset, input.len, repeats });
        if (comptime Adapter.supports_known) {
            if (shared.isKnownDataset(dataset) and (Adapter.supports_canada or !std.mem.eql(u8, dataset, "canada.json"))) {
                try runKnown(Adapter, dataset, input, repeats, false);
                try runKnown(Adapter, dataset, input, repeats, true);
            }
        }
        if (comptime Adapter.supports_arbitrary) {
            try decode(Adapter, Adapter.Arbitrary, "arbitrary-decode", input, repeats, false);
            try transform(Adapter, Adapter.Arbitrary, input, repeats);
        }
    }
}

fn runKnown(comptime A: type, dataset: []const u8, input: []const u8, repeats: usize, comptime encode_value: bool) !void {
    if (std.mem.eql(u8, dataset, "canada.json")) {
        if (comptime A.supports_canada) return known(A, shared.CanadaDocument, input, repeats, encode_value);
        return error.InvalidArguments;
    }
    if (std.mem.eql(u8, dataset, "github_events.json")) return known(A, []const shared.GithubEvent, input, repeats, encode_value);
    if (std.mem.eql(u8, dataset, "poet.json")) return known(A, []const shared.Poem, input, repeats, encode_value);
    return known(A, shared.TwitterDocument, input, repeats, encode_value);
}

fn known(comptime A: type, comptime T: type, input: []const u8, repeats: usize, comptime encode_value: bool) !void {
    if (encode_value) return encode(A, T, input, repeats);
    return decode(A, T, "known-decode", input, repeats, true);
}

fn decode(comptime A: type, comptime T: type, comptime task: []const u8, input: []const u8, repeats: usize, comptime normalize: bool) !void {
    const wire = if (normalize) try A.knownWire(T, input) else try shared.input_allocator.dupe(u8, input);
    defer shared.input_allocator.free(wire);
    var warmup = std.heap.ArenaAllocator.init(shared.input_allocator);
    defer warmup.deinit();
    _ = try A.decode(T, warmup.allocator(), wire);
    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(shared.input_allocator);
        defer arena.deinit();
        const start = shared.nowNanoseconds();
        const value = try A.decode(T, arena.allocator(), wire);
        elapsed += @max(shared.nowNanoseconds() - start, 1);
        std.mem.doNotOptimizeAway(value);
    }
    report(A.name ++ " " ++ task, wire.len, repeats, elapsed, if (normalize) wire.len else null);
}

fn encode(comptime A: type, comptime T: type, input: []const u8, repeats: usize) !void {
    var arena = std.heap.ArenaAllocator.init(shared.input_allocator);
    defer arena.deinit();
    const value = try A.fixture(T, arena.allocator(), input);
    const warmup = try A.encode(shared.input_allocator, value);
    defer shared.input_allocator.free(warmup);
    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        const start = shared.nowNanoseconds();
        const output = try A.encode(shared.input_allocator, value);
        elapsed += @max(shared.nowNanoseconds() - start, 1);
        std.mem.doNotOptimizeAway(output.ptr);
        shared.input_allocator.free(output);
    }
    report(A.name ++ " known-encode", warmup.len, repeats, elapsed, warmup.len);
}

fn transform(comptime A: type, comptime T: type, input: []const u8, repeats: usize) !void {
    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(shared.input_allocator);
        defer arena.deinit();
        const start = shared.nowNanoseconds();
        const value = try A.decode(T, arena.allocator(), input);
        const output = try A.encode(shared.input_allocator, value);
        elapsed += @max(shared.nowNanoseconds() - start, 1);
        std.mem.doNotOptimizeAway(output.ptr);
        shared.input_allocator.free(output);
    }
    report(A.name ++ " transform", input.len, repeats, elapsed, null);
}

fn report(label: []const u8, bytes: usize, repeats: usize, elapsed: u64, output: ?usize) void {
    const ms = @as(f64, @floatFromInt(elapsed)) / @as(f64, @floatFromInt(repeats)) / std.time.ns_per_ms;
    const seconds = @as(f64, @floatFromInt(elapsed)) / std.time.ns_per_s;
    const mib = @as(f64, @floatFromInt(bytes * repeats)) / seconds / (1024.0 * 1024.0);
    std.debug.print("  {s}: {d:.6} ms/op, {d:.2} MiB/s", .{ label, ms, mib });
    if (output) |len| std.debug.print(" ({d} bytes)", .{len});
    std.debug.print("\n", .{});
}
