const std = @import("std");
const jsonz = @import("jsonz");
const bench = @import("bench.zig");

const Adapter = struct {
    pub const name = "jsonz";
    pub const supports_arbitrary = true;
    pub const Arbitrary = jsonz.dom.Document;

    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        if (T == Arbitrary) return jsonz.dom.parse(input, .{});
        return jsonz.typed.parseBorrowed(T, allocator, input, .{ .ignore_unknown_fields = true });
    }

    pub fn encode(allocator: std.mem.Allocator, value: anytype) ![]u8 {
        if (@TypeOf(value) == Arbitrary) return value.toSlice(allocator, .{});
        return jsonz.typed.toSlice(allocator, value, .{});
    }

    pub fn deinit(value: anytype) void {
        if (comptime @TypeOf(value.*) == Arbitrary) value.deinit();
    }
};

pub fn main(init: std.process.Init.Minimal) !void {
    try bench.run(Adapter, init);
}
