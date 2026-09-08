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

const Number = union(enum) {
    int: i64,
    uint: u64,
    float: f64,
    raw: []const u8,
};

const GenericField = struct {
    key: []const u8,
    value: GenericValue,
};

/// A JSON-compatible, schema-free tree used by the generic benchmark.
const GenericValue = union(enum) {
    null,
    bool: bool,
    number: Number,
    string: []const u8,
    array: []GenericValue,
    object: []GenericField,

    pub fn zerdeDeserialize(
        comptime _: type,
        allocator: Allocator,
        deserializer: anytype,
    ) @TypeOf(deserializer.*).Error!GenericValue {
        const token = try deserializer.scanner.peek();
        return switch (token) {
            .null_lit => blk: {
                _ = try deserializer.scanner.next();
                break :blk .null;
            },
            .true_lit => blk: {
                _ = try deserializer.scanner.next();
                break :blk .{ .bool = true };
            },
            .false_lit => blk: {
                _ = try deserializer.scanner.next();
                break :blk .{ .bool = false };
            },
            .number => |raw| blk: {
                _ = try deserializer.scanner.next();
                break :blk .{ .number = parseNumber(raw) };
            },
            .string => .{ .string = try deserializer.deserializeString(allocator) },
            .array_begin => try parseArray(allocator, deserializer),
            .object_begin => try parseObject(allocator, deserializer),
            else => error.WrongType,
        };
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
    const mode = parseMode(&args) catch return error.InvalidArguments;

    std.debug.print("JSON benchmark ({s})\n", .{@tagName(@import("builtin").mode)});
    std.debug.print("data: data/json, input read and cleanup excluded\n", .{});

    for (datasets) |name| {
        if (mode == .typed and !isTypedDataset(name)) continue;

        var path_buffer: [64]u8 = undefined;
        const path = try std.fmt.bufPrint(&path_buffer, "data/json/{s}", .{name});
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
        } else {
            try runTyped(name, input, repeats);
        }
    }
}

fn parseMode(args: *std.process.Args.Iterator) !Mode {
    const argument = args.next() orelse return .generic;
    if (args.next() != null) return error.InvalidArguments;
    if (std.mem.eql(u8, argument, "generic")) return .generic;
    if (std.mem.eql(u8, argument, "typed")) return .typed;
    return error.InvalidArguments;
}

fn parseNumber(raw: []const u8) Number {
    if (std.mem.indexOfAny(u8, raw, ".eE") == null) {
        if (std.fmt.parseInt(i64, raw, 10)) |value| return .{ .int = value } else |_| {}
        if (std.fmt.parseInt(u64, raw, 10)) |value| return .{ .uint = value } else |_| {}
    } else if (std.fmt.parseFloat(f64, raw)) |value| {
        if (std.math.isFinite(value)) return .{ .float = value };
    } else |_| {}
    return .{ .raw = raw };
}

fn parseArray(allocator: Allocator, deserializer: anytype) @TypeOf(deserializer.*).Error!GenericValue {
    _ = try deserializer.scanner.next();
    var values: std.ArrayList(GenericValue) = .empty;
    errdefer values.deinit(allocator);

    if (try deserializer.scanner.isContainerEmpty(']')) {
        _ = try deserializer.scanner.next();
        return .{ .array = values.toOwnedSlice(allocator) catch return error.OutOfMemory };
    }
    while (true) {
        values.append(allocator, try GenericValue.zerdeDeserialize(GenericValue, allocator, deserializer)) catch return error.OutOfMemory;
        if (try deserializer.scanner.finishContainer(']') == .end) break;
    }
    return .{ .array = values.toOwnedSlice(allocator) catch return error.OutOfMemory };
}

fn parseObject(allocator: Allocator, deserializer: anytype) @TypeOf(deserializer.*).Error!GenericValue {
    _ = try deserializer.scanner.next();
    var fields: std.ArrayList(GenericField) = .empty;
    errdefer fields.deinit(allocator);

    if (try deserializer.scanner.isContainerEmpty('}')) {
        _ = try deserializer.scanner.next();
        return .{ .object = fields.toOwnedSlice(allocator) catch return error.OutOfMemory };
    }
    while (true) {
        const key = try deserializer.deserializeString(allocator);
        try deserializer.scanner.expectColon();
        fields.append(allocator, .{
            .key = key,
            .value = try GenericValue.zerdeDeserialize(GenericValue, allocator, deserializer),
        }) catch return error.OutOfMemory;
        if (try deserializer.scanner.finishContainer('}') == .end) break;
    }
    return .{ .object = fields.toOwnedSlice(allocator) catch return error.OutOfMemory };
}

fn runTyped(name: []const u8, input: []const u8, repeats: usize) !void {
    if (std.mem.eql(u8, name, "canada.json")) return runDecode(CanadaDocument, "serde typed decode", input, repeats);
    if (std.mem.eql(u8, name, "github_events.json")) return runDecode([]const GithubEvent, "serde typed decode", input, repeats);
    if (std.mem.eql(u8, name, "poet.json")) return runDecode([]const Poem, "serde typed decode", input, repeats);
    if (std.mem.eql(u8, name, "twitter.json") or std.mem.eql(u8, name, "twitterescaped.json")) return runDecode(TwitterDocument, "serde typed decode", input, repeats);
    return error.InvalidArguments;
}

fn runDecode(comptime T: type, name: []const u8, input: []const u8, repeats: usize) !void {
    var warmup_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer warmup_arena.deinit();
    _ = try serde.json.fromSlice(T, warmup_arena.allocator(), input);

    var elapsed: u64 = 0;
    for (0..repeats) |_| {
        var arena = std.heap.ArenaAllocator.init(input_allocator);
        defer arena.deinit();
        const start = nowNanoseconds();
        const value = try serde.json.fromSlice(T, arena.allocator(), input);
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

fn isTypedDataset(name: []const u8) bool {
    return std.mem.eql(u8, name, "canada.json") or
        std.mem.eql(u8, name, "github_events.json") or
        std.mem.eql(u8, name, "poet.json") or
        std.mem.eql(u8, name, "twitter.json") or
        std.mem.eql(u8, name, "twitterescaped.json");
}

fn repeatCount(size: usize) usize {
    const target_bytes = 64 * 1024 * 1024;
    if (size == 0 or size >= target_bytes) return 1;
    return (target_bytes + size - 1) / size;
}

inline fn nowNanoseconds() u64 {
    return @intCast(std.Io.Clock.awake.now(std.Options.debug_io).nanoseconds);
}
