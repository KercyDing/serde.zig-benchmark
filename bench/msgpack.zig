const std = @import("std");
const serde = @import("serde");

const Allocator = std.mem.Allocator;
const input_allocator = std.heap.c_allocator;
const data_limit = 128 * 1024 * 1024;

const datasets = [_][]const u8{
    "canada.json",
    "citm_catalog.json",
    "fgo.json",
    "github_events.json",
    "gsoc-2018.json",
    "lottie.json",
    "otfcc.json",
    "poet.json",
    "twitter.json",
    "twitterescaped.json",
};

const Mode = enum { generic, typed };

const GenericField = struct {
    key: []const u8,
    value: GenericValue,
};

/// A JSON-compatible MessagePack tree. It is intentionally local to the
/// MessagePack executable so its decoder cannot invoke JSON code.
const GenericValue = union(enum) {
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

const TwitterUser = struct {
    id: u64,
    name: []const u8,
    screen_name: []const u8,
    location: []const u8,
    description: []const u8,
    verified: bool,
    followers_count: u64,
    friends_count: u64,
    statuses_count: ?u64,
};

const TwitterStatus = struct {
    created_at: []const u8,
    id: u64,
    text: []const u8,
    user: TwitterUser,
    retweet_count: u64,
    favorite_count: u64,
};

const TwitterDocument = struct { statuses: []const TwitterStatus };

const CanadaCoordinate = [2]f64;
const CanadaGeometry = struct {
    type: []const u8,
    coordinates: []const []const CanadaCoordinate,
};
const CanadaFeature = struct {
    type: []const u8,
    properties: struct { name: []const u8 },
    geometry: CanadaGeometry,
};
const CanadaDocument = struct {
    type: []const u8,
    features: []const CanadaFeature,
};

const Poem = struct {
    desc: []const u8,
    name: []const u8,
    id: []const u8,
};

const GithubActor = struct {
    gravatar_id: []const u8,
    login: []const u8,
    avatar_url: []const u8,
    url: []const u8,
    id: u64,
};
const GithubRepository = struct {
    url: []const u8,
    id: u64,
    name: []const u8,
};
const GithubEvent = struct {
    type: []const u8,
    created_at: []const u8,
    actor: GithubActor,
    repo: GithubRepository,
    public: bool,
    id: []const u8,
};

pub fn main(init: std.process.Init.Minimal) !void {
    var args = std.process.Args.Iterator.init(init.args);
    _ = args.skip();

    var mode: Mode = .generic;
    while (args.next()) |arg| {
        if (std.mem.eql(u8, arg, "--help")) {
            printHelp();
            return;
        } else if (std.mem.eql(u8, arg, "generic")) {
            mode = .generic;
        } else if (std.mem.eql(u8, arg, "typed")) {
            mode = .typed;
        } else {
            return error.InvalidArguments;
        }
    }

    std.debug.print("MessagePack benchmark ({s})\n", .{@tagName(@import("builtin").mode)});
    std.debug.print("data: data/msgpack, input read and cleanup excluded\n", .{});

    var ran_file = false;
    for (datasets) |name| {
        if (mode == .typed and !isTypedDataset(name)) continue;
        ran_file = true;

        var path_buffer: [64]u8 = undefined;
        const stem = name[0 .. name.len - ".json".len];
        const path = try std.fmt.bufPrint(&path_buffer, "data/msgpack/{s}.msgpack", .{stem});
        const input = try std.Io.Dir.cwd().readFileAlloc(
            std.Options.debug_io,
            path,
            input_allocator,
            .limited(data_limit),
        );
        defer input_allocator.free(input);

        const repeats = repeatCount(input.len);
        std.debug.print("\n{s} ({d} bytes, {d} repeats)\n", .{ name, input.len, repeats });
        if (mode == .generic) {
            try runDecode(GenericValue, "serde generic decode", input, repeats);
            try runEncode(GenericValue, "serde generic encode", input, repeats);
        } else {
            try runTyped(name, input, repeats);
            try runTypedEncode(name, input, repeats);
        }
    }
    if (!ran_file) return error.InvalidArguments;
}

fn printHelp() void {
    std.debug.print(
        "usage: msgpack-bench [generic|typed]\n\n" ++
            "  generic  measure decode and encode for every corpus file\n" ++
            "  typed    measure decode and encode for the typed corpus subset\n",
        .{},
    );
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

fn runTyped(name: []const u8, input: []const u8, repeats: usize) !void {
    if (std.mem.eql(u8, name, "canada.json")) return runDecode(CanadaDocument, "serde typed", input, repeats);
    if (std.mem.eql(u8, name, "github_events.json")) return runDecode([]const GithubEvent, "serde typed", input, repeats);
    if (std.mem.eql(u8, name, "poet.json")) return runDecode([]const Poem, "serde typed", input, repeats);
    if (std.mem.eql(u8, name, "twitter.json") or std.mem.eql(u8, name, "twitterescaped.json")) return runDecode(TwitterDocument, "serde typed", input, repeats);
    return error.InvalidArguments;
}

fn runTypedEncode(name: []const u8, input: []const u8, repeats: usize) !void {
    if (std.mem.eql(u8, name, "canada.json")) return runEncode(CanadaDocument, "serde typed encode", input, repeats);
    if (std.mem.eql(u8, name, "github_events.json")) return runEncode([]const GithubEvent, "serde typed encode", input, repeats);
    if (std.mem.eql(u8, name, "poet.json")) return runEncode([]const Poem, "serde typed encode", input, repeats);
    if (std.mem.eql(u8, name, "twitter.json") or std.mem.eql(u8, name, "twitterescaped.json")) return runEncode(TwitterDocument, "serde typed encode", input, repeats);
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
    std.debug.print("  {s}: {d:.3} ms/op, {d:.2} MiB/s\n", .{
        name,
        @as(f64, @floatFromInt(elapsed / repeats)) / std.time.ns_per_ms,
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
    std.debug.print("  {s}: {d:.3} ms/op, {d:.2} MiB/s ({d} bytes)\n", .{
        name,
        @as(f64, @floatFromInt(elapsed / repeats)) / std.time.ns_per_ms,
        total_bytes / seconds / (1024.0 * 1024.0),
        warmup.len,
    });
}

fn isTypedDataset(name: []const u8) bool {
    return std.mem.eql(u8, name, "canada.json") or
        std.mem.eql(u8, name, "github_events.json") or
        std.mem.eql(u8, name, "poet.json") or
        std.mem.eql(u8, name, "twitter.json") or
        std.mem.eql(u8, name, "twitterescaped.json");
}

fn repeatCount(size: usize) usize {
    if (size >= 32 * 1024 * 1024) return 1;
    if (size >= 4 * 1024 * 1024) return 2;
    return 32;
}

inline fn nowNanoseconds() u64 {
    return @intCast(std.Io.Clock.awake.now(std.Options.debug_io).nanoseconds);
}
