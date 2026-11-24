const std = @import("std");

pub const Backend = enum { wayland, x11, headless };

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const backend = b.option(Backend, "backend", "Windowing backend") orelse .wayland;
    const enable_c = b.option(bool, "use-c", "Compile C helper") orelse false;
    const use_sys_zlib = b.systemIntegrationOption("zlib", .{});

    const exe_mod = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
    });
    const used = b.dependency("used_dep", .{});
    exe_mod.addImport("used_dep", used.module("used_dep"));

    if (use_sys_zlib) {
        exe_mod.linkSystemLibrary("z", .{});
    }
    if (backend == .wayland) {
        exe_mod.linkSystemLibrary("wayland-client", .{});
    } else if (backend == .x11) {
        exe_mod.linkSystemLibrary("X11", .{});
    }
    if (enable_c) {
        exe_mod.addCSourceFile(.{ .file = b.path("src/helper.c") });
    }

    const exe = b.addExecutable(.{
        .name = "demo_app",
        .root_module = exe_mod,
    });
    b.installArtifact(exe);

    // Non-default step linking an extra library must not leak into default inspection.
    const extra_step = b.step("extra", "Non-default step");
    const extra_mod = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
    });
    extra_mod.linkSystemLibrary("should-not-leak", .{});
    const extra_exe = b.addExecutable(.{
        .name = "extra_tool",
        .root_module = extra_mod,
    });
    extra_step.dependOn(&b.addInstallArtifact(extra_exe, .{}).step);

    const test_step = b.step("test", "Run unit tests");
    const test_mod = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
    });
    test_mod.linkSystemLibrary("check-test-lib", .{});
    const unit_tests = b.addTest(.{ .root_module = test_mod });
    const run_tests = b.addRunArtifact(unit_tests);
    test_step.dependOn(&run_tests.step);
}
