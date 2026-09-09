const std = @import("std");
const serde = @import("serde");
const bench = @import("bench.zig");
const dynamic = @import("dynamic.zig");

const Adapter = struct {
    pub const name = "serde";
    pub const supports_arbitrary = true;
    pub const Arbitrary = dynamic.Value;

    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        return serde.json.fromSlice(T, allocator, input);
    }
    pub fn encode(allocator: std.mem.Allocator, value: anytype) ![]u8 {
        return serde.json.toSlice(allocator, value);
    }
};

pub fn main(init: std.process.Init.Minimal) !void {
    try bench.run(Adapter, init);
}
