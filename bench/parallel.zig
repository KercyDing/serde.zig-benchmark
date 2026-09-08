const std = @import("std");
const serde = @import("serde");

const Allocator = std.mem.Allocator;
const input_allocator = std.heap.c_allocator;
const data_limit = 128 * 1024 * 1024;
const target_bytes_per_worker = 64 * 1024 * 1024;

const Format = enum { json, msgpack };
const SelectedFormat = enum { json, msgpack, all };

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

const StartGate = struct {
    ready: std.atomic.Value(usize) = std.atomic.Value(usize).init(0),
    started: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),

    fn wait(self: *StartGate) void {
        _ = self.ready.fetchAdd(1, .release);
        while (!self.started.load(.acquire)) std.Thread.yield() catch {};
    }
};

pub fn main(init: std.process.Init.Minimal) !void {
    var args = std.process.Args.Iterator.init(init.args);
    _ = args.skip();
    const max_threads = std.fmt.parseInt(usize, args.next() orelse return error.InvalidArguments, 10) catch return error.InvalidArguments;
    const selected_format = std.meta.stringToEnum(SelectedFormat, args.next() orelse return error.InvalidArguments) orelse return error.InvalidArguments;
    if (max_threads == 0 or args.next() != null) return error.InvalidArguments;

    std.debug.print("Parallel benchmark ({s})\n", .{@tagName(@import("builtin").mode)});
    std.debug.print("Each worker processes about {d} MiB per operation. File loading, fixture setup, warmup, and cleanup are excluded.\n", .{target_bytes_per_worker / (1024 * 1024)});

    if (selected_format == .json or selected_format == .all) {
        try runFiles(.json, max_threads);
    }
    if (selected_format == .msgpack or selected_format == .all) {
        try runFiles(.msgpack, max_threads);
    }
}

fn runFiles(comptime format: Format, max_threads: usize) !void {
    try runFormat(format, CanadaDocument, "canada.json", max_threads);
    try runFormat(format, []const GithubEvent, "github_events.json", max_threads);
    try runFormat(format, []const Poem, "poet.json", max_threads);
    try runFormat(format, TwitterDocument, "twitter.json", max_threads);
    try runFormat(format, TwitterDocument, "twitterescaped.json", max_threads);
}

fn runFormat(comptime format: Format, comptime T: type, name: []const u8, max_threads: usize) !void {
    var path_buffer: [64]u8 = undefined;
    const stem = name[0 .. name.len - ".json".len];
    const extension = switch (format) {
        .json => "json",
        .msgpack => "msgpack",
    };
    const path = try std.fmt.bufPrint(&path_buffer, "data/{s}/{s}.{s}", .{ @tagName(format), stem, extension });
    const input = try std.Io.Dir.cwd().readFileAlloc(
        std.Options.debug_io,
        path,
        input_allocator,
        .limited(data_limit),
    );
    defer input_allocator.free(input);

    var fixture_arena = std.heap.ArenaAllocator.init(input_allocator);
    defer fixture_arena.deinit();
    const value = try decode(format, T, fixture_arena.allocator(), input);
    const reference = try encode(format, input_allocator, value);
    defer input_allocator.free(reference);

    const repeats = repeatCount(@max(input.len, reference.len));
    std.debug.print("\n{s} / {s} ({d} input bytes, {d} encoded bytes, {d} repeats/worker)\n", .{ @tagName(format), name, input.len, reference.len, repeats });

    var decode_baseline: ?f64 = null;
    var encode_baseline: ?f64 = null;
    var threads: usize = 1;
    while (true) {
        const decode_rate = try measureDecode(format, T, input, repeats, threads);
        const encode_rate = try measureEncode(format, T, &value, reference.len, repeats, threads);
        if (threads == 1) {
            decode_baseline = decode_rate;
            encode_baseline = encode_rate;
        }
        std.debug.print("  {d:2} threads: decode {d:.3} GB/s ({d:.2}x), encode {d:.3} GB/s ({d:.2}x)\n", .{
            threads,
            decode_rate,
            decode_rate / decode_baseline.?,
            encode_rate,
            encode_rate / encode_baseline.?,
        });

        if (threads == max_threads) break;
        threads = @min(threads *| 2, max_threads);
    }
}

fn decode(comptime format: Format, comptime T: type, allocator: Allocator, input: []const u8) anyerror!T {
    return switch (format) {
        .json => serde.json.fromSlice(T, allocator, input),
        .msgpack => serde.msgpack.fromSlice(T, allocator, input),
    };
}

fn encode(comptime format: Format, allocator: Allocator, value: anytype) anyerror![]u8 {
    return switch (format) {
        .json => serde.json.toSlice(allocator, value),
        .msgpack => serde.msgpack.toSlice(allocator, value),
    };
}

fn measureDecode(comptime format: Format, comptime T: type, input: []const u8, repeats: usize, threads: usize) !f64 {
    var gate = StartGate{};
    const workers = try input_allocator.alloc(std.Thread, threads);
    defer input_allocator.free(workers);
    const contexts = try input_allocator.alloc(DecodeContext, threads);
    defer input_allocator.free(contexts);
    for (workers, contexts) |*worker, *context| {
        context.* = .{ .input = input, .repeats = repeats, .gate = &gate };
        worker.* = try std.Thread.spawn(.{}, decodeWorker(format, T), .{context});
    }
    while (gate.ready.load(.acquire) != threads) std.Thread.yield() catch {};
    const start = nowNanoseconds();
    gate.started.store(true, .release);
    for (workers) |worker| worker.join();
    return throughput(input.len * repeats * threads, nowNanoseconds() - start);
}

const DecodeContext = struct { input: []const u8, repeats: usize, gate: *StartGate };

fn decodeWorker(comptime format: Format, comptime T: type) fn (*const DecodeContext) void {
    return struct {
        fn run(context: *const DecodeContext) void {
            var warmup_arena = std.heap.ArenaAllocator.init(input_allocator);
            defer warmup_arena.deinit();
            _ = decode(format, T, warmup_arena.allocator(), context.input) catch @panic("decode warmup failed");
            context.gate.wait();
            for (0..context.repeats) |_| {
                var arena = std.heap.ArenaAllocator.init(input_allocator);
                defer arena.deinit();
                const value = decode(format, T, arena.allocator(), context.input) catch @panic("decode failed");
                std.mem.doNotOptimizeAway(value);
            }
        }
    }.run;
}

fn measureEncode(comptime format: Format, comptime T: type, value: *const T, encoded_len: usize, repeats: usize, threads: usize) !f64 {
    var gate = StartGate{};
    const workers = try input_allocator.alloc(std.Thread, threads);
    defer input_allocator.free(workers);
    const contexts = try input_allocator.alloc(EncodeContext(T), threads);
    defer input_allocator.free(contexts);
    for (workers, contexts) |*worker, *context| {
        context.* = .{ .value = value, .repeats = repeats, .gate = &gate };
        worker.* = try std.Thread.spawn(.{}, encodeWorker(format, T), .{context});
    }
    while (gate.ready.load(.acquire) != threads) std.Thread.yield() catch {};
    const start = nowNanoseconds();
    gate.started.store(true, .release);
    for (workers) |worker| worker.join();
    return throughput(encoded_len * repeats * threads, nowNanoseconds() - start);
}

fn EncodeContext(comptime T: type) type {
    return struct { value: *const T, repeats: usize, gate: *StartGate };
}

fn encodeWorker(comptime format: Format, comptime T: type) fn (*const EncodeContext(T)) void {
    return struct {
        fn run(context: *const EncodeContext(T)) void {
            var output_arena = std.heap.ArenaAllocator.init(input_allocator);
            defer output_arena.deinit();
            _ = encode(format, output_arena.allocator(), context.value.*) catch @panic("encode warmup failed");
            _ = output_arena.reset(.retain_capacity);
            context.gate.wait();
            for (0..context.repeats) |_| {
                _ = output_arena.reset(.retain_capacity);
                const encoded = encode(format, output_arena.allocator(), context.value.*) catch @panic("encode failed");
                std.mem.doNotOptimizeAway(encoded.ptr);
            }
        }
    }.run;
}

fn repeatCount(size: usize) usize {
    if (size == 0 or size >= target_bytes_per_worker) return 1;
    return (target_bytes_per_worker + size - 1) / size;
}

fn throughput(total_bytes: usize, elapsed_ns: u64) f64 {
    const seconds = @as(f64, @floatFromInt(@max(elapsed_ns, 1))) / std.time.ns_per_s;
    return @as(f64, @floatFromInt(total_bytes)) / seconds / 1_000_000_000.0;
}

inline fn nowNanoseconds() u64 {
    return @intCast(std.Io.Clock.awake.now(std.Options.debug_io).nanoseconds);
}
