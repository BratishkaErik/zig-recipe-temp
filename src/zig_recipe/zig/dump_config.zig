// SPDX-FileCopyrightText: 2026 Eric Joldasov
// SPDX-License-Identifier: Apache-2.0
//
// Extracts build graph metadata from a Zig 0.17+ binary std.Build.Configuration
// file (produced by `zig build --print-configuration-path`) as JSON on stdout.

const std = @import("std");
const Configuration = std.Build.Configuration;

const UserOptionOut = struct {
    name: []const u8,
    description: []const u8,
    type: []const u8,
    values: ?[]const []const u8 = null,
};

const SystemIntegrationOut = struct {
    name: []const u8,
    enabled: bool,
};

const LibraryUserKind = enum {
    artifact,
    module,
    translate_c,
};

const LibraryUserOut = struct {
    kind: LibraryUserKind,
    name: []const u8,
};

const SystemLibraryOut = struct {
    name: []const u8,
    used_by: LibraryUserOut,
    use_pkg_config: []const u8,
};

const TopLevelStepOut = struct {
    name: []const u8,
    description: []const u8,
    is_default: bool,
};

const PackageOut = struct {
    hash: []const u8,
    dep_prefix: []const u8,
    root_path: []const u8,
};

const ReportOut = struct {
    user_options: []const UserOptionOut,
    system_integrations: []const SystemIntegrationOut,
    system_libraries: []const SystemLibraryOut,
    top_level_steps: []const TopLevelStepOut,
    used_packages: []const PackageOut,
    find_programs: []const []const []const u8,
    has_c_sources: bool,
};

fn enqueueStep(
    arena: std.mem.Allocator,
    step_idx: Configuration.Step.Index,
    seen_steps: *std.AutoHashMapUnmanaged(u32, void),
    queue: *std.ArrayList(Configuration.Step.Index),
) !void {
    const gop = try seen_steps.getOrPut(arena, @intFromEnum(step_idx));
    if (!gop.found_existing) {
        try queue.append(arena, step_idx);
    }
}

fn recordPkg(
    arena: std.mem.Allocator,
    c: *const Configuration,
    pkg_idx: Configuration.Package.Index,
    seen: *std.StringHashMapUnmanaged(void),
    out: *std.ArrayList(PackageOut),
) !void {
    const pkg = pkg_idx.get(c) orelse return;
    const hash = pkg.hash.slice(c);
    if (hash.len == 0) return;
    const gop = try seen.getOrPut(arena, hash);
    if (!gop.found_existing) {
        try out.append(arena, .{
            .hash = hash,
            .dep_prefix = pkg.dep_prefix.slice(c),
            .root_path = pkg.root_path.slice(c),
        });
    }
}

fn recordLazyPathPkg(
    arena: std.mem.Allocator,
    c: *const Configuration,
    lp_idx: Configuration.LazyPath.Index,
    seen: *std.StringHashMapUnmanaged(void),
    out: *std.ArrayList(PackageOut),
) !void {
    switch (lp_idx.get(c)) {
        .source_path => |sp| try recordPkg(arena, c, sp.owner, seen, out),
        else => {},
    }
}

fn inspectModule(
    arena: std.mem.Allocator,
    c: *const Configuration,
    mod_idx: Configuration.Module.Index,
    owner: LibraryUserOut,
    seen_mods: *std.AutoHashMapUnmanaged(u32, void),
    seen_steps: *std.AutoHashMapUnmanaged(u32, void),
    step_queue: *std.ArrayList(Configuration.Step.Index),
    seen_pkgs: *std.StringHashMapUnmanaged(void),
    pkgs: *std.ArrayList(PackageOut),
    sys_libs: *std.ArrayList(SystemLibraryOut),
    has_c_sources: *bool,
) !void {
    const gop = try seen_mods.getOrPut(arena, @intFromEnum(mod_idx));
    if (gop.found_existing) return;

    const mod = mod_idx.get(c);
    try recordPkg(arena, c, mod.owner, seen_pkgs, pkgs);

    if (mod.root_source_file.unwrap()) |lp| {
        try recordLazyPathPkg(arena, c, lp, seen_pkgs, pkgs);
    }

    for (mod.lib_paths.slice) |lp| {
        try recordLazyPathPkg(arena, c, lp, seen_pkgs, pkgs);
    }

    for (0..mod.include_dirs.len) |i| {
        switch (mod.include_dirs.get(c.extra, i)) {
            .path, .path_system, .path_after, .framework_path, .framework_path_system, .embed_path => |lp| {
                try recordLazyPathPkg(arena, c, lp, seen_pkgs, pkgs);
            },
            .config_header_step => |ch_idx| {
                try enqueueStep(arena, ch_idx, seen_steps, step_queue);
            },
        }
    }

    for (0..mod.link_objects.len) |i| {
        switch (mod.link_objects.get(c.extra, i)) {
            .system_lib => |sl_idx| {
                const sl = sl_idx.get(c);
                try sys_libs.append(arena, .{
                    .name = sl.name.slice(c),
                    .used_by = owner,
                    .use_pkg_config = @tagName(sl.flags.use_pkg_config),
                });
            },
            .c_source_file => |csf_idx| {
                has_c_sources.* = true;
                const csf = csf_idx.get(c);
                try recordLazyPathPkg(arena, c, csf.file, seen_pkgs, pkgs);
            },
            .c_source_files => |csfs_idx| {
                has_c_sources.* = true;
                const csfs = csfs_idx.get(c);
                try recordLazyPathPkg(arena, c, csfs.root, seen_pkgs, pkgs);
            },
            .static_path, .assembly_file => |lp| {
                try recordLazyPathPkg(arena, c, lp, seen_pkgs, pkgs);
            },
            .win32_resource_file => |rc_idx| {
                const rc = rc_idx.get(c);
                try recordLazyPathPkg(arena, c, rc.file, seen_pkgs, pkgs);
            },
            .other_step => |other_idx| {
                try enqueueStep(arena, other_idx, seen_steps, step_queue);
            },
        }
    }

    if (mod.import_table != .invalid) {
        const table = mod.import_table.get(c);
        const names = table.imports.mal.items(.name);
        const mods = table.imports.mal.items(.module);
        for (names, mods) |name, sub_mod| {
            try inspectModule(
                arena,
                c,
                sub_mod,
                .{ .kind = .module, .name = name.slice(c) },
                seen_mods,
                seen_steps,
                step_queue,
                seen_pkgs,
                pkgs,
                sys_libs,
                has_c_sources,
            );
        }
    }
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    const io = init.io;

    const args = try init.minimal.args.toSlice(arena);
    if (args.len < 2) {
        std.process.fatal("usage: dump_config <path-to-configuration> [step-name...]", .{});
    }

    const file = try std.Io.Dir.cwd().openFile(io, args[1], .{});
    defer file.close(io);

    const c = try Configuration.loadFile(arena, io, file);

    var user_options: std.ArrayList(UserOptionOut) = .empty;
    for (c.available_options) |opt| {
        var values: ?[]const []const u8 = null;
        if (opt.enum_options.unwrap()) |sl| {
            var vals: std.ArrayList([]const u8) = .empty;
            for (sl.slice(&c)) |s| {
                try vals.append(arena, s.slice(&c));
            }
            values = vals.items;
        }
        try user_options.append(arena, .{
            .name = opt.name.slice(&c),
            .description = opt.description.slice(&c),
            .type = @tagName(opt.type),
            .values = values,
        });
    }

    var system_integrations: std.ArrayList(SystemIntegrationOut) = .empty;
    for (c.system_integrations) |si| {
        try system_integrations.append(arena, .{
            .name = si.name.slice(&c),
            .enabled = si.status == .enabled,
        });
    }

    // Collect all root top-level steps (always reported regardless of traversal roots).
    var top_level_steps: std.ArrayList(TopLevelStepOut) = .empty;
    var top_level_by_name: std.StringHashMapUnmanaged(Configuration.Step.Index) = .empty;
    for (c.steps, 0..) |step, i| {
        const step_idx: Configuration.Step.Index = @enumFromInt(i);
        switch (step.extended.get(c.extra)) {
            .top_level => |tl| {
                if (step.owner == .root) {
                    const name = step.name.slice(&c);
                    try top_level_steps.append(arena, .{
                        .name = name,
                        .description = tl.description.slice(&c),
                        .is_default = step_idx == c.default_step,
                    });
                    try top_level_by_name.put(arena, name, step_idx);
                }
            },
            else => {},
        }
    }

    var system_libraries: std.ArrayList(SystemLibraryOut) = .empty;
    var used_packages: std.ArrayList(PackageOut) = .empty;
    var find_programs: std.ArrayList([]const []const u8) = .empty;
    var has_c_sources = false;

    var seen_pkgs: std.StringHashMapUnmanaged(void) = .empty;
    var seen_mods: std.AutoHashMapUnmanaged(u32, void) = .empty;
    var seen_steps: std.AutoHashMapUnmanaged(u32, void) = .empty;
    var step_queue: std.ArrayList(Configuration.Step.Index) = .empty;

    for (c.path_deps) |pd| {
        if (pd.pkg.unwrap()) |pkg_idx| {
            try recordPkg(arena, &c, pkg_idx, &seen_pkgs, &used_packages);
        }
    }

    // Seed BFS from requested step names, or from c.default_step by default.
    if (args.len > 2) {
        for (args[2..]) |step_name| {
            const idx = top_level_by_name.get(step_name) orelse
                std.process.fatal("unknown top-level step: {s}", .{step_name});
            try enqueueStep(arena, idx, &seen_steps, &step_queue);
        }
    } else if (c.steps.len > 0) {
        try enqueueStep(arena, c.default_step, &seen_steps, &step_queue);
    }

    var q_i: usize = 0;
    while (q_i < step_queue.items.len) : (q_i += 1) {
        const step_idx = step_queue.items[q_i];
        const step = step_idx.ptr(&c);
        try recordPkg(arena, &c, step.owner, &seen_pkgs, &used_packages);

        for (step.deps.slice(&c)) |dep_idx| {
            try enqueueStep(arena, dep_idx, &seen_steps, &step_queue);
        }

        switch (step.extended.get(c.extra)) {
            .compile => |comp| {
                try inspectModule(
                    arena,
                    &c,
                    comp.root_module,
                    .{ .kind = .artifact, .name = comp.root_name.slice(&c) },
                    &seen_mods,
                    &seen_steps,
                    &step_queue,
                    &seen_pkgs,
                    &used_packages,
                    &system_libraries,
                    &has_c_sources,
                );
            },
            .translate_c => |tc| {
                has_c_sources = true;
                try recordLazyPathPkg(arena, &c, tc.src_path, &seen_pkgs, &used_packages);
                const step_name = step.name.slice(&c);
                for (tc.system_libs.slice) |sl_idx| {
                    const sl = sl_idx.get(&c);
                    try system_libraries.append(arena, .{
                        .name = sl.name.slice(&c),
                        .used_by = .{ .kind = .translate_c, .name = step_name },
                        .use_pkg_config = @tagName(sl.flags.use_pkg_config),
                    });
                }
            },
            .find_program => |fp| {
                var names: std.ArrayList([]const u8) = .empty;
                for (fp.names.slice(&c)) |s| {
                    try names.append(arena, s.slice(&c));
                }
                try find_programs.append(arena, names.items);
            },
            .install_file => |inf| {
                try recordLazyPathPkg(arena, &c, inf.source, &seen_pkgs, &used_packages);
            },
            .install_dir => |ind| {
                try recordLazyPathPkg(arena, &c, ind.source_dir, &seen_pkgs, &used_packages);
            },
            else => {},
        }
    }

    const report: ReportOut = .{
        .user_options = user_options.items,
        .system_integrations = system_integrations.items,
        .system_libraries = system_libraries.items,
        .top_level_steps = top_level_steps.items,
        .used_packages = used_packages.items,
        .find_programs = find_programs.items,
        .has_c_sources = has_c_sources,
    };

    var buf: [4096]u8 = undefined;
    var stdout_writer = std.Io.File.stdout().writer(io, &buf);
    try stdout_writer.interface.print("{f}\n", .{std.json.fmt(report, .{})});
    try stdout_writer.interface.flush();
}
