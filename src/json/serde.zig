const std = @import("std");
const serde = @import("serde");
const bench = @import("bench.zig");

const Allocator = std.mem.Allocator;

const Number = union(enum) { int: i64, uint: u64, float: f64, raw: []const u8 };
const Field = struct { key: []const u8, value: Value };

const Value = union(enum) {
    null,
    bool: bool,
    number: Number,
    string: []const u8,
    array: []Value,
    object: []Field,

    pub fn zerdeDeserialize(comptime _: type, allocator: Allocator, deserializer: anytype) ErrorOf(@TypeOf(deserializer.deserializeBool()))!Value {
        return deserialize(allocator, deserializer);
    }

    pub fn zerdeSerialize(self: Value, serializer: anytype) ErrorOf(@TypeOf(serializer.serializeNull()))!void {
        return serialize(self, serializer);
    }
};

fn ErrorOf(comptime ErrorUnion: type) type {
    return @typeInfo(ErrorUnion).error_union.error_set;
}

fn deserialize(allocator: Allocator, deserializer: anytype) ErrorOf(@TypeOf(deserializer.deserializeBool()))!Value {
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
        .array_begin => parseArray(allocator, deserializer),
        .object_begin => parseObject(allocator, deserializer),
        else => error.WrongType,
    };
}

fn serialize(value: Value, serializer: anytype) ErrorOf(@TypeOf(serializer.serializeNull()))!void {
    switch (value) {
        .null => try serializer.serializeNull(),
        .bool => |item| try serializer.serializeBool(item),
        .number => |item| switch (item) {
            .int => |n| try serializer.serializeInt(n),
            .uint => |n| try serializer.serializeInt(n),
            .float => |n| try serializer.serializeFloat(n),
            .raw => unreachable,
        },
        .string => |item| try serializer.serializeString(item),
        .array => |items| {
            var array = try serializer.beginArray();
            for (items) |item| try serialize(item, &array);
            try array.end();
        },
        .object => |fields| {
            var object = try serializer.beginStruct();
            for (fields) |field| try object.serializeEntry(field.key, field.value);
            try object.end();
        },
    }
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

fn parseArray(allocator: Allocator, deserializer: anytype) ErrorOf(@TypeOf(deserializer.deserializeBool()))!Value {
    _ = try deserializer.scanner.next();
    var values: std.ArrayList(Value) = .empty;
    errdefer values.deinit(allocator);
    if (try deserializer.scanner.isContainerEmpty(']')) {
        _ = try deserializer.scanner.next();
        return .{ .array = values.toOwnedSlice(allocator) catch return error.OutOfMemory };
    }
    while (true) {
        values.append(allocator, try deserialize(allocator, deserializer)) catch return error.OutOfMemory;
        if (try deserializer.scanner.finishContainer(']') == .end) break;
    }
    return .{ .array = values.toOwnedSlice(allocator) catch return error.OutOfMemory };
}

fn parseObject(allocator: Allocator, deserializer: anytype) ErrorOf(@TypeOf(deserializer.deserializeBool()))!Value {
    _ = try deserializer.scanner.next();
    var fields: std.ArrayList(Field) = .empty;
    errdefer fields.deinit(allocator);
    while (true) {
        if (try deserializer.scanner.isContainerEmpty('}')) {
            _ = try deserializer.scanner.next();
            break;
        }
        const key = try deserializer.deserializeString(allocator);
        try deserializer.scanner.expectColon();
        try fields.append(allocator, .{ .key = key, .value = try deserialize(allocator, deserializer) });
        if (try deserializer.scanner.finishContainer('}') == .end) break;
    }
    return .{ .object = fields.toOwnedSlice(allocator) catch return error.OutOfMemory };
}

const Adapter = struct {
    pub const name = "serde";
    pub const supports_arbitrary = true;
    pub const Arbitrary = Value;

    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        if (T == Arbitrary) return serde.json.fromSlice(T, allocator, input);
        // Borrowed strings cannot represent decoded JSON escapes. Match the
        // zero-copy path where valid and retain an owning fallback otherwise.
        if (std.mem.indexOfScalar(u8, input, '\\') != null)
            return serde.json.fromSlice(T, allocator, input);
        return serde.json.fromSliceBorrowed(T, allocator, input);
    }
    pub fn encode(allocator: std.mem.Allocator, value: anytype) ![]u8 {
        return serde.json.toSlice(allocator, value);
    }
};

pub fn main(init: std.process.Init.Minimal) !void {
    try bench.run(Adapter, init);
}
