use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use codex_obsidian_sync_rs::config::{load_toml_config, resolve_service_paths};
use codex_obsidian_sync_rs::error::SyncError;
use codex_obsidian_sync_rs::launchd::{
    ensure_launch_agent_dirs, render_launch_agent_plist, write_launch_agent_plist,
};

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

#[test]
fn render_launch_agent_plist_uses_canonical_rust_binary_and_start_interval() {
    let root = temp_dir("render");
    let config_path = write_config(&root);
    let config = load_toml_config(Some(&config_path)).unwrap();
    let paths = resolve_service_paths(Some(&config_path), &config).unwrap();
    let binary_path = root.join("bin").join("codex-obsidian-sync-rs");
    fs::create_dir_all(binary_path.parent().unwrap()).unwrap();
    fs::write(&binary_path, "").unwrap();

    let rendered = render_launch_agent_plist(&config_path, &paths, 60, &binary_path).unwrap();
    let binary_path = fs::canonicalize(binary_path).unwrap();

    assert!(rendered.contains(&format!("<string>{}</string>", binary_path.display())));
    assert!(rendered.contains("<string>--config</string>"));
    assert!(rendered.contains(&format!("<string>{}</string>", config_path.display())));
    assert!(rendered.contains("<string>service-run</string>"));
    assert!(rendered.contains("<key>StartInterval</key>"));
    assert!(rendered.contains("<integer>60</integer>"));
    assert!(!rendered.contains("WatchPaths"));
}

#[cfg(unix)]
#[test]
fn launch_agent_paths_reject_symlink_parent_escape() {
    let root = temp_dir("symlink-parent");
    let real = root.join("real");
    let link = root.join("link");
    fs::create_dir_all(&real).unwrap();
    std::os::unix::fs::symlink(&real, &link).unwrap();

    let error = write_launch_agent_plist(&link.join("nested/agent.plist"), "<plist/>").unwrap_err();

    assert_eq!(error.to_string(), "config error");
}

#[cfg(unix)]
#[test]
fn ensure_launch_agent_dirs_creates_private_parents() {
    let root = temp_dir("private-dirs");
    let config_path = write_config(&root);
    let config = load_toml_config(Some(&config_path)).unwrap();
    let paths = resolve_service_paths(Some(&config_path), &config).unwrap();

    ensure_launch_agent_dirs(&paths).unwrap();

    for path in [
        &paths.launchd_plist_path,
        &paths.launchd_stdout_path,
        &paths.launchd_stderr_path,
    ] {
        let mode = fs::metadata(path.parent().unwrap())
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o077, 0);
    }
}

#[test]
fn launchd_error_display_is_content_free() {
    assert_eq!(SyncError::Launchd.to_string(), "launchd error");
}

fn write_config(root: &Path) -> PathBuf {
    let codex_home = root.join(".codex");
    fs::create_dir_all(&codex_home).unwrap();
    let config_path = root.join("config.toml");
    fs::write(
        &config_path,
        format!(
            "codex_home = \"{}\"\nlaunchd_plist_path = \"{}\"\nlaunchd_stdout_path = \"{}\"\nlaunchd_stderr_path = \"{}\"\n",
            codex_home.display(),
            root.join("LaunchAgents").join("agent.plist").display(),
            root.join("logs").join("stdout.log").display(),
            root.join("logs").join("stderr.log").display(),
        ),
    )
    .unwrap();
    config_path
}

fn temp_dir(name: &str) -> PathBuf {
    let unique = format!(
        "codex-obsidian-sync-rs-launchd-{name}-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let path = std::env::temp_dir().join(unique);
    fs::create_dir_all(&path).unwrap();
    fs::canonicalize(path).unwrap()
}
