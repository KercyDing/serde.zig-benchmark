const std = @import("std");
const serde = @import("serde");
const bench = @import("bench.zig");
const dynamic = @import("dynamic.zig");

const Adapter = struct {
    pub const name = "serde";
    pub const supports_known = true;
    pub const supports_arbitrary = true;
    pub const supports_canada = true;
    pub const Arbitrary = dynamic.Value;
    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        return serde.msgpack.fromSlice(T, allocator, input);
    }
    pub fn encode(allocator: std.mem.Allocator, value: anytype) ![]u8 {
        return serde.msgpack.toSlice(allocator, value);
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
