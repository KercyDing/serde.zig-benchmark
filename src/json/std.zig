const std = @import("std");
const bench = @import("bench.zig");

const Adapter = struct {
    pub const name = "std.json";
    pub const supports_arbitrary = true;
    pub const Arbitrary = std.json.Value;

    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        return std.json.parseFromSliceLeaky(T, allocator, input, .{ .ignore_unknown_fields = true });
    }
    pub fn encode(allocator: std.mem.Allocator, value: anytype) ![]u8 {
        var output: std.Io.Writer.Allocating = .init(allocator);
        errdefer output.deinit();
        try std.json.Stringify.value(value, .{}, &output.writer);
        return output.toOwnedSlice();
    }
};

pub fn main(init: std.process.Init.Minimal) !void {
    try bench.run(Adapter, init);
}
