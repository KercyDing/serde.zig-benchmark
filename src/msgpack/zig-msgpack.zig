const std = @import("std");
const msgpack = @import("zig_msgpack");
const bench = @import("bench.zig");

const Adapter = struct {
    pub const name = "zig-msgpack";
    pub const supports_known = false;
    pub const supports_arbitrary = true;
    pub const supports_canada = true;
    pub const Arbitrary = msgpack.Payload;

    pub fn decode(comptime T: type, allocator: std.mem.Allocator, input: []const u8) !T {
        if (T != msgpack.Payload) @compileError("zig-msgpack only supports Payload");
        var dummy_output: [1]u8 = undefined;
        var writer = std.Io.Writer.fixed(&dummy_output);
        var reader = std.Io.Reader.fixed(input);
        var packer = msgpack.PackerIO.init(&reader, &writer);
        return packer.read(allocator);
    }
    pub fn encode(allocator: std.mem.Allocator, value: msgpack.Payload) ![]u8 {
        var output: std.Io.Writer.Allocating = .init(allocator);
        errdefer output.deinit();
        var dummy_input: [1]u8 = undefined;
        var reader = std.Io.Reader.fixed(dummy_input[0..0]);
        var packer = msgpack.PackerIO.init(&reader, &output.writer);
        try packer.write(value);
        return output.toOwnedSlice();
    }
};

pub fn main(init: std.process.Init.Minimal) !void {
    try bench.run(Adapter, init);
}
