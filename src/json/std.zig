const legacy = @import("serde.zig");
pub fn main(init: @import("std").process.Init.Minimal) !void {
    try legacy.run(init, .std);
}
