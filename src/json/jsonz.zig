const std = @import("std");
const jsonz = @import("jsonz");
const bench = @import("bench.zig");
const dynamic = @import("dynamic.zig");

const Adapter = struct {
    pub const name = "jsonz";
    pub const supports_arbitrary = true;
    pub const Arbitrary = dynamic.Value;

    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        return jsonz.fromSlice(T, allocator, input, .{ .ignore_unknown_fields = true });
    }
    pub fn encode(allocator: std.mem.Allocator, value: anytype) ![]u8 {
        return jsonz.toSlice(allocator, value, .{});
    }
};

pub fn main(init: std.process.Init.Minimal) !void {
    try bench.run(Adapter, init);
}
