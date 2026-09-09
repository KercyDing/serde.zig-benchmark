const std = @import("std");
const msgpack = @import("zig_msgpack");
const bench = @import("bench.zig");

const Adapter = struct {
    pub const name = "zig-msgpack";
    pub const supports_known = true;
    pub const supports_arbitrary = true;
    pub const supports_canada = true;
    pub const Arbitrary = msgpack.Payload;

    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        var dummy_output: [1]u8 = undefined;
        var writer = std.Io.Writer.fixed(&dummy_output);
        var reader = std.Io.Reader.fixed(input);
        var packer = msgpack.PackerIO.init(&reader, &writer);
        const payload = try packer.read(allocator);
        if (T == msgpack.Payload) return payload;
        return fromPayload(T, allocator, payload);
    }
    pub fn encode(allocator: std.mem.Allocator, value: anytype) ![]u8 {
        var output: std.Io.Writer.Allocating = .init(allocator);
        errdefer output.deinit();
        var dummy_input: [1]u8 = undefined;
        var reader = std.Io.Reader.fixed(dummy_input[0..0]);
        var packer = msgpack.PackerIO.init(&reader, &output.writer);
        const payload = if (@TypeOf(value) == msgpack.Payload) value else try toPayload(allocator, value);
        try packer.write(payload);
        return output.toOwnedSlice();
    }
    pub fn fixture(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        return decode(T, allocator, input);
    }
    pub fn knownWire(comptime T: type, input: []const u8) ![]u8 {
        var arena = std.heap.ArenaAllocator.init(std.heap.c_allocator);
        defer arena.deinit();
        return encode(std.heap.c_allocator, try fixture(T, arena.allocator(), input));
    }
};

pub fn main(init: std.process.Init.Minimal) !void {
    try bench.run(Adapter, init);
}

fn fromPayload(comptime T: type, allocator: std.mem.Allocator, payload: msgpack.Payload) !T {
    return switch (@typeInfo(T)) {
        .bool => if (payload == .bool) payload.bool else error.WrongType,
        .int => switch (payload) {
            .int => |value| std.math.cast(T, value) orelse error.Overflow,
            .uint => |value| std.math.cast(T, value) orelse error.Overflow,
            else => error.WrongType,
        },
        .float => switch (payload) {
            .float => |value| @floatCast(value),
            .int => |value| @floatFromInt(value),
            .uint => |value| @floatFromInt(value),
            else => error.WrongType,
        },
        .optional => |info| if (payload == .nil) null else try fromPayload(info.child, allocator, payload),
        .array => |info| blk: {
            if (payload != .arr or payload.arr.len != info.len) return error.WrongType;
            var result: T = undefined;
            for (&result, payload.arr) |*item, source| item.* = try fromPayload(info.child, allocator, source);
            break :blk result;
        },
        .pointer => |info| blk: {
            if (info.size != .slice) @compileError("unsupported pointer type");
            if (info.child == u8) {
                if (payload != .str) return error.WrongType;
                break :blk try allocator.dupe(u8, payload.str.value());
            }
            if (payload != .arr) return error.WrongType;
            const result = try allocator.alloc(info.child, payload.arr.len);
            for (result, payload.arr) |*item, source| item.* = try fromPayload(info.child, allocator, source);
            break :blk result;
        },
        .@"struct" => |info| blk: {
            if (payload != .map) return error.WrongType;
            var result: T = undefined;
            inline for (info.fields) |field| {
                const source = payload.map.getByString(field.name) orelse return error.MissingField;
                @field(result, field.name) = try fromPayload(field.type, allocator, source);
            }
            break :blk result;
        },
        else => @compileError("unsupported known-data type"),
    };
}

fn toPayload(allocator: std.mem.Allocator, value: anytype) !msgpack.Payload {
    const T = @TypeOf(value);
    return switch (@typeInfo(T)) {
        .bool => msgpack.Payload.boolToPayload(value),
        .int => |info| if (info.signedness == .signed) msgpack.Payload.intToPayload(value) else msgpack.Payload.uintToPayload(value),
        .float => msgpack.Payload.floatToPayload(value),
        .optional => if (value) |item| try toPayload(allocator, item) else msgpack.Payload.nilToPayload(),
        .array => blk: {
            var result = try msgpack.Payload.arrPayload(value.len, allocator);
            for (value, 0..) |item, index| try result.setArrElement(index, try toPayload(allocator, item));
            break :blk result;
        },
        .pointer => |info| blk: {
            if (info.size != .slice) @compileError("unsupported pointer type");
            if (info.child == u8) break :blk try msgpack.Payload.strToPayload(value, allocator);
            var result = try msgpack.Payload.arrPayload(value.len, allocator);
            for (value, 0..) |item, index| try result.setArrElement(index, try toPayload(allocator, item));
            break :blk result;
        },
        .@"struct" => |info| blk: {
            var result = msgpack.Payload.mapPayload(allocator);
            inline for (info.fields) |field| try result.mapPut(field.name, try toPayload(allocator, @field(value, field.name)));
            break :blk result;
        },
        else => @compileError("unsupported known-data type"),
    };
}
