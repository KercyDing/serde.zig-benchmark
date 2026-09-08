//! Shared data model and helpers for the MessagePack task benchmarks.
const std = @import("std");

pub const Allocator = std.mem.Allocator;
pub const input_allocator = std.heap.c_allocator;
pub const data_limit = 128 * 1024 * 1024;

pub const datasets = [_][]const u8{
    "canada.json",
    "citm_catalog.json",
    "fgo.json",
    "github_events.json",
    "gsoc-2018.json",
    "lottie.json",
    "otfcc.json",
    "poet.json",
    "twitter.json",
    "twitterescaped.json",
};

pub const TwitterUser = struct {
    id: u64,
    name: []const u8,
    screen_name: []const u8,
    location: []const u8,
    description: []const u8,
    verified: bool,
    followers_count: u64,
    friends_count: u64,
    statuses_count: ?u64,
};

pub const TwitterStatus = struct {
    created_at: []const u8,
    id: u64,
    text: []const u8,
    user: TwitterUser,
    retweet_count: u64,
    favorite_count: u64,
};

pub const TwitterDocument = struct { statuses: []const TwitterStatus };

pub const CanadaCoordinate = [2]f64;
pub const CanadaGeometry = struct {
    type: []const u8,
    coordinates: []const []const CanadaCoordinate,
};
pub const CanadaFeature = struct {
    type: []const u8,
    properties: struct { name: []const u8 },
    geometry: CanadaGeometry,
};
pub const CanadaDocument = struct {
    type: []const u8,
    features: []const CanadaFeature,
};

pub const Poem = struct {
    desc: []const u8,
    name: []const u8,
    id: []const u8,
};

pub const GithubActor = struct {
    gravatar_id: []const u8,
    login: []const u8,
    avatar_url: []const u8,
    url: []const u8,
    id: u64,
};
pub const GithubRepository = struct {
    url: []const u8,
    id: u64,
    name: []const u8,
};
pub const GithubEvent = struct {
    type: []const u8,
    created_at: []const u8,
    actor: GithubActor,
    repo: GithubRepository,
    public: bool,
    id: []const u8,
};

pub fn isKnownDataset(name: []const u8) bool {
    return std.mem.eql(u8, name, "canada.json") or
        std.mem.eql(u8, name, "github_events.json") or
        std.mem.eql(u8, name, "poet.json") or
        std.mem.eql(u8, name, "twitter.json") or
        std.mem.eql(u8, name, "twitterescaped.json");
}

pub fn repeatCount(size: usize) usize {
    const target_bytes = 64 * 1024 * 1024;
    if (size == 0 or size >= target_bytes) return 1;
    return (target_bytes + size - 1) / size;
}

pub inline fn nowNanoseconds() u64 {
    return @intCast(std.Io.Clock.awake.now(std.Options.debug_io).nanoseconds);
}
