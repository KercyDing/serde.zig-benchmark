const std = @import("std");
const zig_msgpack = @import("zig_msgpack");
const shared = @import("shared.zig");

const Allocator = std.mem.Allocator;
const input_allocator = shared.input_allocator;
const data_limit = shared.data_limit;
const datasets = shared.datasets;
const repeatCount = shared.repeatCount;
const nowNanoseconds = shared.nowNanoseconds;

// ---------------------------------------------------------------------------
// zig-msgpack (zigcc) — arbitrary-data tasks.
// ---------------------------------------------------------------------------

fn zigDecodePayload(allocator: Allocator, bytes: []const u8) !zig_msgpack.Payload {
    var dummy_w: [1]u8 = undefined;
    var writer = std.Io.Writer.fixed(&dummy_w);
    var reader = std.Io.Reader.fixed(bytes);
    var packer = zig_msgpack.PackerIO.init(&reader, &writer);
    return packer.read(allocator);
}

fn zigEncodeSlice(allocator: Allocator, payload: zig_msgpack.Payload) ![]u8 {
    var aw: std.Io.Writer.Allocating = .init(allocator);
    errdefer aw.deinit();
    var dummy: [1]u8 = undefined;
    var reader = std.Io.Reader.fixed(dummy[0..0]);
    var packer = zig_msgpack.PackerIO.init(&reader, &aw.writer);
    try packer.write(payload);
    return aw.toOwnedSlice();
}

fn runDecode(name: []const u8, input: []const u8, repeats: usize) !void {
    var warmup_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer warmup_arena.deinit();
    _ = try zigDecodePayload(warmup_arena.allocator(), input);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(input_allocator);
        defer arena.deinit();
        const start = nowNanoseconds();
        const payload = try zigDecodePayload(arena.allocator(), input);
        const end = nowNanoseconds();
        std.mem.doNotOptimizeAway(payload);
        elapsed += @max(end - start, 1);
    }

    const total_bytes: f64 = @floatFromInt(input.len * repeats);
    const seconds: f64 = @as(f64, @floatFromInt(elapsed)) / std.time.ns_per_s;
    std.debug.print("  {s}: {d:.6} ms/op, {d:.2} MiB/s\n", .{
        name,
        @as(f64, @floatFromInt(elapsed)) / @as(f64, @floatFromInt(repeats)) / std.time.ns_per_ms,
        total_bytes / seconds / (1024.0 * 1024.0),
    });
}

fn runZigEncode(name: []const u8, input: []const u8, repeats: usize) !void {
    var fixture_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer fixture_arena.deinit();
    const payload = try zigDecodePayload(fixture_arena.allocator(), input);

    const warmup = try zigEncodeSlice(input_allocator, payload);
    defer input_allocator.free(warmup);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        const start = nowNanoseconds();
        const encoded = try zigEncodeSlice(input_allocator, payload);
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

fn runRoundtrip(name: []const u8, input: []const u8, repeats: usize) !void {
    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(input_allocator);
        defer arena.deinit();
        const start = nowNanoseconds();
        const payload = try zigDecodePayload(arena.allocator(), input);
        const encoded = try zigEncodeSlice(input_allocator, payload);
        const end = nowNanoseconds();
        std.mem.doNotOptimizeAway(encoded.ptr);
        input_allocator.free(encoded);
        elapsed += @max(end - start, 1);
    }

    const total_bytes: f64 = @floatFromInt(input.len * repeats);
    const seconds: f64 = @as(f64, @floatFromInt(elapsed)) / std.time.ns_per_s;
    std.debug.print("  {s}: {d:.6} ms/op, {d:.2} MiB/s\n", .{
        name,
        @as(f64, @floatFromInt(elapsed)) / @as(f64, @floatFromInt(repeats)) / std.time.ns_per_ms,
        total_bytes / seconds / (1024.0 * 1024.0),
    });
}

pub fn main(init: std.process.Init.Minimal) !void {
    var args = try std.process.Args.Iterator.initAllocator(init.args, input_allocator);
    defer args.deinit();
    _ = args.skip();
    if (args.next() != null) return error.InvalidArguments;

    std.debug.print("MessagePack zig-msgpack benchmark\n", .{});
    std.debug.print("data: data/msgpack, input read and cleanup excluded\n", .{});

    var ran_file = false;
    for (datasets) |name| {
        ran_file = true;
        var path_buffer: [64]u8 = undefined;
        const stem = name[0 .. name.len - ".json".len];
        const path = try std.fmt.bufPrint(&path_buffer, "data/msgpack/{s}.msgpack", .{stem});
        const input = try std.Io.Dir.cwd().readFileAlloc(std.Options.debug_io, path, input_allocator, .limited(data_limit));
        defer input_allocator.free(input);
        const repeats = repeatCount(input.len);
        std.debug.print("\n{s} ({d} bytes, {d} repeats)\n", .{ name, input.len, repeats });
        try runDecode("zig-msgpack arbitrary-decode", input, repeats);
        try runRoundtrip("zig-msgpack transform", input, repeats);
    }
    if (!ran_file) return error.InvalidArguments;
}
