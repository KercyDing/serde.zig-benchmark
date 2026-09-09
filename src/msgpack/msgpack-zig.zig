const std = @import("std");
const serde = @import("serde");
const msgpack = @import("msgpack_lalinsky");
const bench = @import("bench.zig");

const Adapter = struct {
    pub const name = "msgpack.zig";
    pub const supports_known = true;
    pub const supports_arbitrary = false;
    pub const supports_canada = false;
    pub const Arbitrary = void;

    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        return msgpack.decodeFromSliceLeaky(T, allocator, input);
    }
    pub fn encode(allocator: std.mem.Allocator, value: anytype) ![]u8 {
        var output: std.Io.Writer.Allocating = .init(allocator);
        errdefer output.deinit();
        try msgpack.encode(value, &output.writer);
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
