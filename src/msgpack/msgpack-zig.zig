const std = @import("std");
const serde = @import("serde");
const msgpack_lalinsky = @import("msgpack_lalinsky");
const common = @import("shared.zig");

const Allocator = std.mem.Allocator;
const input_allocator = common.input_allocator;
const data_limit = common.data_limit;
const datasets = common.datasets;
const repeatCount = common.repeatCount;
const isKnownDataset = common.isKnownDataset;
const isTypedDataset = common.isTypedDataset;
const nowNanoseconds = common.nowNanoseconds;
const GithubEvent = common.GithubEvent;
const Poem = common.Poem;
const TwitterDocument = common.TwitterDocument;

// ---------------------------------------------------------------------------
// msgpack.zig (lalinsky) — known-data tasks.
// ---------------------------------------------------------------------------

fn lalEncode(allocator: Allocator, value: anytype) ![]u8 {
    var aw: std.Io.Writer.Allocating = .init(allocator);
    errdefer aw.deinit();
    try msgpack_lalinsky.encode(value, &aw.writer);
    return aw.toOwnedSlice();
}

fn lalDecodeLeaky(comptime T: type, allocator: Allocator, data: []const u8) !T {
    return msgpack_lalinsky.decodeFromSliceLeaky(T, allocator, data);
}

fn runLalDecode(comptime T: type, name: []const u8, input: []const u8, repeats: usize) !void {
    var fixture_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer fixture_arena.deinit();
    const value = try serde.msgpack.fromSlice(T, fixture_arena.allocator(), input);
    const wire = try lalEncode(input_allocator, value);
    defer input_allocator.free(wire);

    var warmup_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer warmup_arena.deinit();
    _ = try lalDecodeLeaky(T, warmup_arena.allocator(), wire);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(input_allocator);
        defer arena.deinit();
        const start = nowNanoseconds();
        const decoded = try lalDecodeLeaky(T, arena.allocator(), wire);
        const end = nowNanoseconds();
        std.mem.doNotOptimizeAway(decoded);
        elapsed += @max(end - start, 1);
    }

    const total_bytes: f64 = @floatFromInt(wire.len * repeats);
    const seconds: f64 = @as(f64, @floatFromInt(elapsed)) / std.time.ns_per_s;
    std.debug.print("  {s}: {d:.6} ms/op, {d:.2} MiB/s ({d} bytes)\n", .{
        name,
        @as(f64, @floatFromInt(elapsed)) / @as(f64, @floatFromInt(repeats)) / std.time.ns_per_ms,
        total_bytes / seconds / (1024.0 * 1024.0),
        wire.len,
    });
}

fn runLalEncode(comptime T: type, name: []const u8, input: []const u8, repeats: usize) !void {
    var fixture_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer fixture_arena.deinit();
    const value = try serde.msgpack.fromSlice(T, fixture_arena.allocator(), input);

    const warmup = try lalEncode(input_allocator, value);
    defer input_allocator.free(warmup);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        const start = nowNanoseconds();
        const encoded = try lalEncode(input_allocator, value);
        const end = nowNanoseconds();
        std.mem.doNotOptimizeAway(encoded.ptr);
        input_allocator.free(encoded);
        elapsed += @max(end - start, 1);
    }

    const total_bytes: f64 = @floatFromInt(warmup.len * repeats);
    const seconds: f64 = @as(f64, @floatFromInt(elapsed)) / std.time.ns_per_s;
    std.debug.print("  {s}: {d:.6} ms/op, {d:.2} MiB/s ({d} bytes)\n", .{
        name,
        @as(f64, @floatFromInt(elapsed)) / @as(f64, @floatFromInt(repeats)) / std.time.ns_per_ms,
        total_bytes / seconds / (1024.0 * 1024.0),
        warmup.len,
    });
}

fn runLalRoundtrip(comptime T: type, name: []const u8, input: []const u8, repeats: usize) !void {
    var fixture_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer fixture_arena.deinit();
    const value = try serde.msgpack.fromSlice(T, fixture_arena.allocator(), input);
    const wire = try lalEncode(input_allocator, value);
    defer input_allocator.free(wire);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(input_allocator);
        defer arena.deinit();
        const start = nowNanoseconds();
        const decoded = try lalDecodeLeaky(T, arena.allocator(), wire);
        const encoded = try lalEncode(input_allocator, decoded);
        const end = nowNanoseconds();
        std.mem.doNotOptimizeAway(encoded.ptr);
        input_allocator.free(encoded);
        elapsed += @max(end - start, 1);
    }

    const total_bytes: f64 = @floatFromInt(wire.len * repeats);
    const seconds: f64 = @as(f64, @floatFromInt(elapsed)) / std.time.ns_per_s;
    std.debug.print("  {s}: {d:.6} ms/op, {d:.2} MiB/s ({d} bytes)\n", .{
        name,
        @as(f64, @floatFromInt(elapsed)) / @as(f64, @floatFromInt(repeats)) / std.time.ns_per_ms,
        total_bytes / seconds / (1024.0 * 1024.0),
        wire.len,
    });
}

fn runKnown(name: []const u8, input: []const u8, repeats: usize) !void {
    if (std.mem.eql(u8, name, "github_events.json")) return runLalDecode([]const GithubEvent, "msgpack.zig known-decode", input, repeats);
    if (std.mem.eql(u8, name, "poet.json")) return runLalDecode([]const Poem, "msgpack.zig known-decode", input, repeats);
    if (std.mem.eql(u8, name, "twitter.json") or std.mem.eql(u8, name, "twitterescaped.json")) return runLalDecode(TwitterDocument, "msgpack.zig known-decode", input, repeats);
    return error.InvalidArguments;
}

fn runKnownEncode(name: []const u8, input: []const u8, repeats: usize) !void {
    if (std.mem.eql(u8, name, "github_events.json")) return runLalEncode([]const GithubEvent, "msgpack.zig known-encode", input, repeats);
    if (std.mem.eql(u8, name, "poet.json")) return runLalEncode([]const Poem, "msgpack.zig known-encode", input, repeats);
    if (std.mem.eql(u8, name, "twitter.json") or std.mem.eql(u8, name, "twitterescaped.json")) return runLalEncode(TwitterDocument, "msgpack.zig known-encode", input, repeats);
    return error.InvalidArguments;
}

pub fn main(init: std.process.Init.Minimal) !void {
    var args = std.process.Args.Iterator.init(init.args);
    _ = args.skip();
    if (args.next() != null) return error.InvalidArguments;

    std.debug.print("MessagePack msgpack.zig benchmark\n", .{});
    std.debug.print("data: data/msgpack, input read and cleanup excluded\n", .{});

    var ran_file = false;
    for (datasets) |name| {
        if (!isKnownDataset(name)) continue;
        if (std.mem.eql(u8, name, "canada.json")) continue;
        ran_file = true;
        var path_buffer: [64]u8 = undefined;
        const stem = name[0 .. name.len - ".json".len];
        const path = try std.fmt.bufPrint(&path_buffer, "data/msgpack/{s}.msgpack", .{stem});
        const input = try std.Io.Dir.cwd().readFileAlloc(std.Options.debug_io, path, input_allocator, .limited(data_limit));
        defer input_allocator.free(input);
        const repeats = repeatCount(input.len);
        std.debug.print("\n{s} ({d} bytes, {d} repeats)\n", .{ name, input.len, repeats });
        try runKnown(name, input, repeats);
        try runKnownEncode(name, input, repeats);
    }
    if (!ran_file) return error.InvalidArguments;
}
