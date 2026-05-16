use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::Command;

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

use crate::config::{ServicePaths, default_launchd_label};
use crate::error::SyncError;
use crate::writer::write_state_file;

const DEFAULT_THROTTLE_INTERVAL: u64 = 1;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchdStatus {
    pub label: String,
    pub loaded: bool,
    pub plist_path: PathBuf,
    pub target: String,
}

pub fn render_launch_agent_plist(
    config_path: &Path,
    paths: &ServicePaths,
    start_interval: u64,
    binary_path: &Path,
) -> Result<String, SyncError> {
    if !config_path.is_absolute() {
        return Err(SyncError::Config);
    }
    let binary_path = fs::canonicalize(binary_path).map_err(|_| SyncError::Config)?;
    let arguments = [
        binary_path.to_string_lossy().into_owned(),
        "--config".to_owned(),
        config_path.to_string_lossy().into_owned(),
        "service-run".to_owned(),
    ];
    Ok(render_plist_xml(
        &arguments,
        paths,
        start_interval.max(1),
        DEFAULT_THROTTLE_INTERVAL,
    ))
}

pub fn ensure_launch_agent_dirs(paths: &ServicePaths) -> Result<(), SyncError> {
    ensure_output_parent(&paths.launchd_plist_path)?;
    ensure_output_parent(&paths.launchd_stdout_path)?;
    ensure_output_parent(&paths.launchd_stderr_path)?;
    Ok(())
}

pub fn write_launch_agent_plist(plist_path: &Path, content: &str) -> Result<bool, SyncError> {
    ensure_output_parent(plist_path)?;
    if fs::read_to_string(plist_path).is_ok_and(|existing| existing == content) {
        return Ok(false);
    }
    write_state_file(plist_path, content)?;
    Ok(true)
}

pub fn query_launchd_status(plist_path: Option<&Path>) -> LaunchdStatus {
    let label = default_launchd_label().to_owned();
    let target = launchd_target(&label);
    let loaded = Command::new("launchctl")
        .args(["print", &target])
        .output()
        .is_ok_and(|output| output.status.success());
    LaunchdStatus {
        label,
        loaded,
        plist_path: plist_path
            .map(Path::to_path_buf)
            .unwrap_or_else(default_launchd_plist_path),
        target,
    }
}

pub fn bootstrap_launch_agent(plist_path: &Path) -> Result<(), SyncError> {
    let status = Command::new("launchctl")
        .arg("bootstrap")
        .arg(launchd_domain())
        .arg(plist_path)
        .status()
        .map_err(|_| SyncError::Launchd)?;
    if status.success() {
        Ok(())
    } else {
        Err(SyncError::Launchd)
    }
}

pub fn bootout_launch_agent() -> Result<(), SyncError> {
    let target = launchd_target(default_launchd_label());
    let status = Command::new("launchctl")
        .args(["bootout", &target])
        .status()
        .map_err(|_| SyncError::Launchd)?;
    if status.success() {
        Ok(())
    } else {
        Err(SyncError::Launchd)
    }
}

pub fn launchd_domain() -> String {
    format!("gui/{}", current_uid())
}

pub fn launchd_target(label: &str) -> String {
    format!("{}/{}", launchd_domain(), label)
}

fn render_plist_xml(
    arguments: &[String; 4],
    paths: &ServicePaths,
    start_interval: u64,
    throttle_interval: u64,
) -> String {
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>{label}</string>
	<key>ProgramArguments</key>
	<array>
		<string>{arg0}</string>
		<string>{arg1}</string>
		<string>{arg2}</string>
		<string>{arg3}</string>
	</array>
	<key>StartInterval</key>
	<integer>{start_interval}</integer>
	<key>RunAtLoad</key>
	<false/>
	<key>KeepAlive</key>
	<false/>
	<key>ThrottleInterval</key>
	<integer>{throttle_interval}</integer>
	<key>StandardOutPath</key>
	<string>{stdout}</string>
	<key>StandardErrorPath</key>
	<string>{stderr}</string>
</dict>
</plist>
"#,
        label = xml_escape(default_launchd_label()),
        arg0 = xml_escape(&arguments[0]),
        arg1 = xml_escape(&arguments[1]),
        arg2 = xml_escape(&arguments[2]),
        arg3 = xml_escape(&arguments[3]),
        stdout = xml_escape(&paths.launchd_stdout_path.to_string_lossy()),
        stderr = xml_escape(&paths.launchd_stderr_path.to_string_lossy()),
        throttle_interval = throttle_interval.max(1),
    )
}

fn ensure_output_parent(path: &Path) -> Result<(), SyncError> {
    if !path.is_absolute() {
        return Err(SyncError::Config);
    }
    let parent = path.parent().ok_or(SyncError::Config)?;
    create_private_dirs_without_symlinks(parent)?;
    reject_world_writable(parent)?;
    reject_target_symlink(path)
}

fn create_private_dirs_without_symlinks(path: &Path) -> Result<(), SyncError> {
    let mut current = PathBuf::new();
    for component in path.components() {
        match component {
            Component::RootDir => current.push(component.as_os_str()),
            Component::Normal(name) => {
                current.push(name);
                match fs::symlink_metadata(&current) {
                    Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                        return Err(SyncError::Config);
                    }
                    Ok(_) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                        fs::create_dir(&current).map_err(|_| SyncError::Config)?;
                        set_private_dir_permissions(&current)?;
                    }
                    Err(_) => return Err(SyncError::Config),
                }
            }
            Component::CurDir => {}
            Component::ParentDir | Component::Prefix(_) => return Err(SyncError::Config),
        }
    }
    Ok(())
}

fn reject_target_symlink(path: &Path) -> Result<(), SyncError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(SyncError::Config),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(SyncError::Config),
    }
}

fn reject_world_writable(path: &Path) -> Result<(), SyncError> {
    #[cfg(unix)]
    {
        let mode = fs::metadata(path)
            .map_err(|_| SyncError::Config)?
            .permissions()
            .mode();
        if mode & 0o002 != 0 {
            return Err(SyncError::Config);
        }
    }
    Ok(())
}

fn set_private_dir_permissions(path: &Path) -> Result<(), SyncError> {
    #[cfg(unix)]
    {
        let permissions = fs::Permissions::from_mode(0o700);
        fs::set_permissions(path, permissions).map_err(|_| SyncError::Config)?;
    }
    Ok(())
}

fn default_launchd_plist_path() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
        .join("Library")
        .join("LaunchAgents")
        .join(format!("{}.plist", default_launchd_label()))
}

#[cfg(unix)]
fn current_uid() -> u32 {
    uzers::get_current_uid()
}

#[cfg(not(unix))]
fn current_uid() -> u32 {
    0
}

fn xml_escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}
