const std = @import("std");
const serde = @import("serde");
const msgpack = @import("msgpack_lalinsky");
const bench = @import("bench.zig");
const shared = @import("shared.zig");

const Field = struct { key: []const u8, value: Value };
const Value = union(enum) {
    null,
    bool: bool,
    int: i64,
    uint: u64,
    float: f64,
    string: []const u8,
    array: []Value,
    object: []Field,
};

const CanadaWireGeometry = struct { type: []const u8, coordinates: []const []const []const f64 };
const CanadaWireFeature = struct {
    type: []const u8,
    properties: struct { name: []const u8 },
    geometry: CanadaWireGeometry,
};
const CanadaWireDocument = struct { type: []const u8, features: []const CanadaWireFeature };

const Adapter = struct {
    pub const name = "msgpack.zig";
    pub const supports_known = true;
    pub const supports_arbitrary = true;
    pub const supports_canada = true;
    pub const Arbitrary = Value;

    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        if (T == Value) {
            var reader = std.Io.Reader.fixed(input);
            return readValue(allocator, &reader);
        }
        if (T == shared.CanadaDocument) return decodeCanada(allocator, input);
        return msgpack.decodeFromSliceLeaky(T, allocator, input);
    }
    pub fn encode(allocator: std.mem.Allocator, value: anytype) ![]u8 {
        var output: std.Io.Writer.Allocating = .init(allocator);
        errdefer output.deinit();
        if (@TypeOf(value) == Value) {
            try writeValue(&output.writer, value);
        } else if (@TypeOf(value) == shared.CanadaDocument) {
            try writeKnown(&output.writer, value);
        } else {
            try msgpack.encode(value, &output.writer);
        }
        return output.toOwnedSlice();
    }
    pub fn fixture(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        return serde.msgpack.fromSlice(T, allocator, input);
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

fn readValue(allocator: std.mem.Allocator, reader: *std.Io.Reader) anyerror!Value {
    const tag = reader.buffer[reader.seek];
    return switch (tag) {
        0xc0 => blk: {
            try msgpack.unpackNull(reader);
            break :blk .null;
        },
        0xc2, 0xc3 => .{ .bool = try msgpack.unpackBool(reader, bool) },
        0xca, 0xcb => .{ .float = try msgpack.unpackFloat(reader, f64) },
        0xd0, 0xd1, 0xd2, 0xd3 => .{ .int = try msgpack.unpackInt(reader, i64) },
        0xcc, 0xcd, 0xce, 0xcf => .{ .uint = try msgpack.unpackInt(reader, u64) },
        0xd9, 0xda, 0xdb => .{ .string = try msgpack.unpackString(reader, allocator) },
        0xdc, 0xdd => readArray(allocator, reader),
        0xde, 0xdf => readMap(allocator, reader),
        else => if (tag <= 0x7f)
            .{ .uint = try msgpack.unpackInt(reader, u64) }
        else if (tag >= 0xe0)
            .{ .int = try msgpack.unpackInt(reader, i64) }
        else if (tag & 0xe0 == 0xa0)
            .{ .string = try msgpack.unpackString(reader, allocator) }
        else if (tag & 0xf0 == 0x90)
            readArray(allocator, reader)
        else if (tag & 0xf0 == 0x80)
            readMap(allocator, reader)
        else
            error.WrongType,
    };
}

fn readArray(allocator: std.mem.Allocator, reader: *std.Io.Reader) anyerror!Value {
    const len = try msgpack.unpackArrayHeader(reader, u32);
    const values = try allocator.alloc(Value, len);
    for (values) |*value| value.* = try readValue(allocator, reader);
    return .{ .array = values };
}

fn readMap(allocator: std.mem.Allocator, reader: *std.Io.Reader) anyerror!Value {
    const len = try msgpack.unpackMapHeader(reader, u32);
    const fields = try allocator.alloc(Field, len);
    for (fields) |*field| {
        const key = try readValue(allocator, reader);
        if (key != .string) return error.WrongType;
        field.* = .{ .key = key.string, .value = try readValue(allocator, reader) };
    }
    return .{ .object = fields };
}

fn writeValue(writer: *std.Io.Writer, value: Value) anyerror!void {
    switch (value) {
        .null => try msgpack.packNull(writer),
        .bool => |item| try msgpack.packBool(writer, item),
        .int => |item| try msgpack.packInt(writer, i64, item),
        .uint => |item| try msgpack.packInt(writer, u64, item),
        .float => |item| try msgpack.packFloat(writer, f64, item),
        .string => |item| try msgpack.packString(writer, item),
        .array => |items| {
            try msgpack.packArrayHeader(writer, items.len);
            for (items) |item| try writeValue(writer, item);
        },
        .object => |fields| {
            try msgpack.packMapHeader(writer, fields.len);
            for (fields) |field| {
                try msgpack.packString(writer, field.key);
                try writeValue(writer, field.value);
            }
        },
    }
}

fn decodeCanada(allocator: std.mem.Allocator, input: []const u8) !shared.CanadaDocument {
    const wire = try msgpack.decodeFromSliceLeaky(CanadaWireDocument, allocator, input);
    const features = try allocator.alloc(shared.CanadaFeature, wire.features.len);
    for (features, wire.features) |*feature, source| {
        const polygons = try allocator.alloc([]const shared.CanadaCoordinate, source.geometry.coordinates.len);
        for (polygons, source.geometry.coordinates) |*polygon, source_polygon| {
            const coordinates = try allocator.alloc(shared.CanadaCoordinate, source_polygon.len);
            for (coordinates, source_polygon) |*coordinate, source_coordinate| {
                if (source_coordinate.len != 2) return error.WrongType;
                coordinate.* = .{ source_coordinate[0], source_coordinate[1] };
            }
            polygon.* = coordinates;
        }
        feature.* = .{
            .type = source.type,
            .properties = .{ .name = source.properties.name },
            .geometry = .{ .type = source.geometry.type, .coordinates = polygons },
        };
    }
    return .{ .type = wire.type, .features = features };
}

fn writeKnown(writer: *std.Io.Writer, value: anytype) anyerror!void {
    const T = @TypeOf(value);
    switch (@typeInfo(T)) {
        .bool => try msgpack.packBool(writer, value),
        .int => try msgpack.packInt(writer, T, value),
        .float => try msgpack.packFloat(writer, T, value),
        .optional => if (value) |item| try writeKnown(writer, item) else try msgpack.packNull(writer),
        .array => {
            try msgpack.packArrayHeader(writer, value.len);
            for (value) |item| try writeKnown(writer, item);
        },
        .pointer => |info| {
            if (info.size != .slice) @compileError("unsupported pointer type");
            if (info.child == u8) {
                try msgpack.packString(writer, value);
            } else {
                try msgpack.packArrayHeader(writer, value.len);
                for (value) |item| try writeKnown(writer, item);
            }
        },
        .@"struct" => |info| {
            try msgpack.packMapHeader(writer, info.fields.len);
            inline for (info.fields) |field| {
                try msgpack.packString(writer, field.name);
                try writeKnown(writer, @field(value, field.name));
            }
        },
        else => @compileError("unsupported known-data type"),
    }
}
