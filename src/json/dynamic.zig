const std = @import("std");

const Allocator = std.mem.Allocator;

pub const Number = union(enum) { int: i64, uint: u64, float: f64, raw: []const u8 };
pub const Field = struct { key: []const u8, value: Value };

pub const Value = union(enum) {
    null,
    bool: bool,
    number: Number,
    string: []const u8,
    array: []Value,
    object: []Field,

    pub fn zerdeDeserialize(comptime _: type, allocator: Allocator, deserializer: anytype) ErrorOf(@TypeOf(deserializer.deserializeBool()))!Value {
        return deserialize(allocator, deserializer, false);
    }

    pub fn jsonzDeserialize(comptime _: type, allocator: Allocator, deserializer: anytype) ErrorOf(@TypeOf(deserializer.deserializeBool()))!Value {
        return deserialize(allocator, deserializer, true);
    }

    pub fn zerdeSerialize(self: Value, serializer: anytype) ErrorOf(@TypeOf(serializer.serializeNull()))!void {
        return serializeSerde(self, serializer);
    }

    pub fn jsonzSerialize(self: Value, serializer: anytype) ErrorOf(@TypeOf(serializer.serializeNull()))!void {
        return serializeJsonz(self, serializer);
    }
};

fn ErrorOf(comptime ErrorUnion: type) type {
    return @typeInfo(ErrorUnion).error_union.error_set;
}

fn deserialize(allocator: Allocator, deserializer: anytype, comptime jsonz: bool) ErrorOf(@TypeOf(deserializer.deserializeBool()))!Value {
    const cursor = if (jsonz) &deserializer.cursor else &deserializer.scanner;
    const token = try cursor.peek();
    return switch (token) {
        .null_lit => blk: {
            _ = try cursor.next();
            break :blk .null;
        },
        .true_lit => blk: {
            _ = try cursor.next();
            break :blk .{ .bool = true };
        },
        .false_lit => blk: {
            _ = try cursor.next();
            break :blk .{ .bool = false };
        },
        .number => |raw| blk: {
            _ = try cursor.next();
            break :blk .{ .number = parseNumber(raw) };
        },
        .string => .{ .string = if (jsonz) try deserializer.deserializeString() else try deserializer.deserializeString(allocator) },
        .array_begin => parseArray(allocator, deserializer, jsonz),
        .object_begin => parseObject(allocator, deserializer, jsonz),
        else => error.WrongType,
    };
}

fn serializeSerde(value: Value, serializer: anytype) ErrorOf(@TypeOf(serializer.serializeNull()))!void {
    switch (value) {
        .null => try serializer.serializeNull(),
        .bool => |item| try serializer.serializeBool(item),
        .number => |item| switch (item) {
            .int => |number| try serializer.serializeInt(number),
            .uint => |number| try serializer.serializeInt(number),
            .float => |number| try serializer.serializeFloat(number),
            .raw => unreachable,
        },
        .string => |item| try serializer.serializeString(item),
        .array => |items| {
            var array = try serializer.beginArray();
            for (items) |item| try serializeSerde(item, &array);
            try array.end();
        },
        .object => |fields| {
            var object = try serializer.beginStruct();
            for (fields) |field| try object.serializeEntry(field.key, field.value);
            try object.end();
        },
    }
}

fn serializeJsonz(value: Value, serializer: anytype) ErrorOf(@TypeOf(serializer.serializeNull()))!void {
    switch (value) {
        .null => try serializer.serializeNull(),
        .bool => |item| try serializer.serializeBool(item),
        .number => |item| switch (item) {
            .int => |number| try serializer.serializeInt(number),
            .uint => |number| try serializer.serializeInt(number),
            .float => |number| try serializer.serializeFloat(number),
            .raw => unreachable,
        },
        .string => |item| try serializer.serializeString(item),
        .array => |items| {
            try serializer.writer.writeByte('[');
            for (items, 0..) |item, index| {
                if (index != 0) try serializer.writer.writeByte(',');
                try serializer.serialize(item);
            }
            try serializer.writer.writeByte(']');
        },
        .object => |fields| {
            try serializer.writer.writeByte('{');
            for (fields, 0..) |field, index| {
                if (index != 0) try serializer.writer.writeByte(',');
                try serializer.serializeString(field.key);
                try serializer.writer.writeByte(':');
                try serializer.serialize(field.value);
            }
            try serializer.writer.writeByte('}');
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

fn parseArray(allocator: Allocator, deserializer: anytype, comptime jsonz: bool) ErrorOf(@TypeOf(deserializer.deserializeBool()))!Value {
    const cursor = if (jsonz) &deserializer.cursor else &deserializer.scanner;
    _ = try cursor.next();
    var values: std.ArrayList(Value) = .empty;
    errdefer values.deinit(allocator);
    if (try cursor.isContainerEmpty(']')) {
        _ = try cursor.next();
        return .{ .array = values.toOwnedSlice(allocator) catch return error.OutOfMemory };
    }
    while (true) {
        values.append(allocator, try deserialize(allocator, deserializer, jsonz)) catch return error.OutOfMemory;
        if (try cursor.finishContainer(']') == .end) break;
    }
    return .{ .array = values.toOwnedSlice(allocator) catch return error.OutOfMemory };
}

fn parseObject(allocator: Allocator, deserializer: anytype, comptime jsonz: bool) ErrorOf(@TypeOf(deserializer.deserializeBool()))!Value {
    const cursor = if (jsonz) &deserializer.cursor else &deserializer.scanner;
    _ = try cursor.next();
    var fields: std.ArrayList(Field) = .empty;
    errdefer fields.deinit(allocator);
    if (try cursor.isContainerEmpty('}')) {
        _ = try cursor.next();
        return .{ .object = fields.toOwnedSlice(allocator) catch return error.OutOfMemory };
    }
    while (true) {
        const key = if (jsonz) try deserializer.deserializeString() else try deserializer.deserializeString(allocator);
        try cursor.expectColon();
        fields.append(allocator, .{ .key = key, .value = try deserialize(allocator, deserializer, jsonz) }) catch return error.OutOfMemory;
        if (try cursor.finishContainer('}') == .end) break;
    }
    return .{ .object = fields.toOwnedSlice(allocator) catch return error.OutOfMemory };
}
