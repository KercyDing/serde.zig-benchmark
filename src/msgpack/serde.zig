const std = @import("std");
const serde = @import("serde");
const shared = @import("shared.zig");

const Allocator = std.mem.Allocator;
const input_allocator = shared.input_allocator;
const data_limit = shared.data_limit;
const datasets = shared.datasets;
const repeatCount = shared.repeatCount;
const isKnownDataset = shared.isKnownDataset;
const isTypedDataset = shared.isTypedDataset;
const nowNanoseconds = shared.nowNanoseconds;
const CanadaDocument = shared.CanadaDocument;
const GithubEvent = shared.GithubEvent;
const Poem = shared.Poem;
const TwitterDocument = shared.TwitterDocument;

const GenericField = struct {
    key: []const u8,
    value: GenericValue,
};

/// A JSON-compatible MessagePack tree. It is intentionally local to the
/// MessagePack executable so its decoder cannot invoke JSON code.
pub const GenericValue = union(enum) {
    null,
    bool: bool,
    int: i64,
    uint: u64,
    float: f64,
    string: []const u8,
    array: []GenericValue,
    object: []GenericField,

    pub fn zerdeDeserialize(
        comptime _: type,
        allocator: Allocator,
        deserializer: anytype,
    ) @TypeOf(deserializer.*).Error!GenericValue {
        const tag = deserializer.input[deserializer.pos];
        return switch (tag) {
            0xc0 => blk: {
                try deserializer.deserializeVoid();
                break :blk .null;
            },
            0xc2, 0xc3 => .{ .bool = try deserializer.deserializeBool() },
            0xca, 0xcb => .{ .float = try deserializer.deserializeFloat(f64) },
            0xd0, 0xd1, 0xd2, 0xd3 => .{ .int = try deserializer.deserializeInt(i64) },
            0xcc, 0xcd, 0xce, 0xcf => .{ .uint = try deserializer.deserializeInt(u64) },
            0xd9, 0xda, 0xdb => .{ .string = try deserializer.deserializeString(allocator) },
            0xdc, 0xdd => try parseArray(allocator, deserializer),
            0xde, 0xdf => try parseObject(allocator, deserializer),
            else => blk: {
                if (tag <= 0x7f) break :blk .{ .uint = try deserializer.deserializeInt(u64) };
                if (tag >= 0xe0) break :blk .{ .int = try deserializer.deserializeInt(i64) };
                if (tag & 0xe0 == 0xa0) break :blk .{ .string = try deserializer.deserializeString(allocator) };
                if (tag & 0xf0 == 0x90) break :blk try parseArray(allocator, deserializer);
                if (tag & 0xf0 == 0x80) break :blk try parseObject(allocator, deserializer);
                return error.WrongType;
            },
        };
    }

    pub fn zerdeSerialize(self: GenericValue, serializer: anytype) @TypeOf(serializer.*).Error!void {
        switch (self) {
            .null => try serializer.serializeNull(),
            .bool => |value| try serializer.serializeBool(value),
            .int => |value| try serializer.serializeInt(value),
            .uint => |value| try serializer.serializeInt(value),
            .float => |value| try serializer.serializeFloat(value),
            .string => |value| try serializer.serializeString(value),
            .array => |values| {
                var array = try serializer.beginArray();
                for (values) |value| try value.zerdeSerialize(&array);
                try array.end();
            },
            .object => |fields| {
                var object = try serializer.beginStruct();
                for (fields) |field| try object.serializeEntry(field.key, field.value);
                try object.end();
            },
        }
    }
};

pub fn main(init: std.process.Init.Minimal) !void {
    var args = try std.process.Args.Iterator.initAllocator(init.args, input_allocator);
    defer args.deinit();
    _ = args.skip();
    if (args.next() != null) return error.InvalidArguments;

    std.debug.print("MessagePack serde.zig benchmark\n", .{});
    std.debug.print("data: data/msgpack, input read and cleanup excluded\n", .{});

    var ran_file = false;
    for (datasets) |name| {
        const known = isKnownDataset(name);
        ran_file = true;
        var path_buffer: [64]u8 = undefined;
        const stem = name[0 .. name.len - ".json".len];
        const path = try std.fmt.bufPrint(&path_buffer, "data/msgpack/{s}.msgpack", .{stem});
        const input = try std.Io.Dir.cwd().readFileAlloc(std.Options.debug_io, path, input_allocator, .limited(data_limit));
        defer input_allocator.free(input);
        const repeats = repeatCount(input.len);
        std.debug.print("\n{s} ({d} bytes, {d} repeats)\n", .{ name, input.len, repeats });
        if (known) {
            try runKnown(name, input, repeats);
            try runKnownEncode(name, input, repeats);
        }
        try runDecode(GenericValue, "serde arbitrary-decode", input, repeats);
        try runRoundtrip(GenericValue, "serde transform", input, repeats);
    }
    if (!ran_file) return error.InvalidArguments;
}

fn parseArray(allocator: Allocator, deserializer: anytype) @TypeOf(deserializer.*).Error!GenericValue {
    var access = try deserializer.deserializeSeqAccess();
    const values = allocator.alloc(GenericValue, access.remaining) catch return error.OutOfMemory;
    for (values) |*value| value.* = (try access.nextElement(GenericValue, allocator)) orelse unreachable;
    return .{ .array = values };
}

fn parseObject(allocator: Allocator, deserializer: anytype) @TypeOf(deserializer.*).Error!GenericValue {
    var access = try deserializer.deserializeStruct(GenericValue);
    const fields = allocator.alloc(GenericField, access.remaining) catch return error.OutOfMemory;
    var index: usize = 0;
    while (try access.nextKey(allocator)) |key| : (index += 1) {
        fields[index] = .{
            .key = key,
            .value = try access.nextValue(GenericValue, allocator),
        };
    }
    return .{ .object = fields };
}

fn runKnown(name: []const u8, input: []const u8, repeats: usize) !void {
    if (std.mem.eql(u8, name, "canada.json")) return runKnownDecode(CanadaDocument, "serde known-decode", input, repeats);
    if (std.mem.eql(u8, name, "github_events.json")) return runKnownDecode([]const GithubEvent, "serde known-decode", input, repeats);
    if (std.mem.eql(u8, name, "poet.json")) return runKnownDecode([]const Poem, "serde known-decode", input, repeats);
    if (std.mem.eql(u8, name, "twitter.json") or std.mem.eql(u8, name, "twitterescaped.json")) return runKnownDecode(TwitterDocument, "serde known-decode", input, repeats);
    return error.InvalidArguments;
}

fn runKnownDecode(comptime T: type, name: []const u8, input: []const u8, repeats: usize) !void {
    var fixture_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer fixture_arena.deinit();
    const value = try serde.msgpack.fromSlice(T, fixture_arena.allocator(), input);
    const wire = try serde.msgpack.toSlice(input_allocator, value);
    defer input_allocator.free(wire);

    var warmup_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer warmup_arena.deinit();
    _ = try serde.msgpack.fromSlice(T, warmup_arena.allocator(), wire);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(input_allocator);
        defer arena.deinit();
        const start = nowNanoseconds();
        const decoded = try serde.msgpack.fromSlice(T, arena.allocator(), wire);
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

fn runKnownEncode(name: []const u8, input: []const u8, repeats: usize) !void {
    if (std.mem.eql(u8, name, "canada.json")) return runEncode(CanadaDocument, "serde known-encode", input, repeats);
    if (std.mem.eql(u8, name, "github_events.json")) return runEncode([]const GithubEvent, "serde known-encode", input, repeats);
    if (std.mem.eql(u8, name, "poet.json")) return runEncode([]const Poem, "serde known-encode", input, repeats);
    if (std.mem.eql(u8, name, "twitter.json") or std.mem.eql(u8, name, "twitterescaped.json")) return runEncode(TwitterDocument, "serde known-encode", input, repeats);
    return error.InvalidArguments;
}

fn runDecode(comptime T: type, name: []const u8, input: []const u8, repeats: usize) !void {
    var warmup_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer warmup_arena.deinit();
    _ = try serde.msgpack.fromSlice(T, warmup_arena.allocator(), input);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(input_allocator);
        defer arena.deinit();
        const start = nowNanoseconds();
        const value = try serde.msgpack.fromSlice(T, arena.allocator(), input);
        const end = nowNanoseconds();
        std.mem.doNotOptimizeAway(value);
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

fn runEncode(comptime T: type, name: []const u8, input: []const u8, repeats: usize) !void {
    // Fixture construction is deliberately outside the measurement. It uses
    // MessagePack input, never JSON, so the timed path is serialization only.
    var fixture_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer fixture_arena.deinit();
    const value = try serde.msgpack.fromSlice(T, fixture_arena.allocator(), input);

    const warmup = try serde.msgpack.toSlice(input_allocator, value);
    defer input_allocator.free(warmup);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        const start = nowNanoseconds();
        const encoded = try serde.msgpack.toSlice(input_allocator, value);
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

fn runRoundtrip(comptime T: type, name: []const u8, input: []const u8, repeats: usize) !void {
    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(input_allocator);
        defer arena.deinit();
        const start = nowNanoseconds();
        const value = try serde.msgpack.fromSlice(T, arena.allocator(), input);
        const encoded = try serde.msgpack.toSlice(input_allocator, value);
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
