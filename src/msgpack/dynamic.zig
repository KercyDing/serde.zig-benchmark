const std = @import("std");

const Allocator = std.mem.Allocator;
pub const Field = struct { key: []const u8, value: Value };

pub const Value = union(enum) {
    null,
    bool: bool,
    int: i64,
    uint: u64,
    float: f64,
    string: []const u8,
    array: []Value,
    object: []Field,

    pub fn zerdeDeserialize(comptime _: type, allocator: Allocator, deserializer: anytype) @TypeOf(deserializer.*).Error!Value {
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
            0xdc, 0xdd => parseArray(allocator, deserializer),
            0xde, 0xdf => parseObject(allocator, deserializer),
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

    pub fn zerdeSerialize(self: Value, serializer: anytype) @TypeOf(serializer.*).Error!void {
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

fn parseArray(allocator: Allocator, deserializer: anytype) @TypeOf(deserializer.*).Error!Value {
    var access = try deserializer.deserializeSeqAccess();
    const values = allocator.alloc(Value, access.remaining) catch return error.OutOfMemory;
    for (values) |*value| value.* = (try access.nextElement(Value, allocator)) orelse unreachable;
    return .{ .array = values };
}

fn parseObject(allocator: Allocator, deserializer: anytype) @TypeOf(deserializer.*).Error!Value {
    var access = try deserializer.deserializeStruct(Value);
    const fields = allocator.alloc(Field, access.remaining) catch return error.OutOfMemory;
    var index: usize = 0;
    while (try access.nextKey(allocator)) |key| : (index += 1) {
        fields[index] = .{ .key = key, .value = try access.nextValue(Value, allocator) };
    }
    return .{ .object = fields };
}
