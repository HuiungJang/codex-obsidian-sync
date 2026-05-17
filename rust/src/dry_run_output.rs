use std::fs;
use std::io::{self, Write};
use std::path::{Component, Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(unix)]
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};

use serde::Serialize;

use crate::config::SyncConfig;
use crate::error::SyncError;
use crate::state_store::{FileFingerprint, file_fingerprint_from_metadata};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DryRunOutput {
    root: PathBuf,
    root_canonical: PathBuf,
    vault_root: PathBuf,
    temp_state_file: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DryRunOutputSummary {
    pub dry_run: bool,
    pub output_dir: String,
    pub temp_state_file: String,
    pub planned_writes: usize,
}

impl DryRunOutput {
    pub fn prepare(config: &SyncConfig, requested: Option<&Path>) -> Result<Self, SyncError> {
        let root = match requested {
            Some(path) => validate_requested_output_root(path, config)?,
            None => create_default_output_root(config)?,
        };
        let root_canonical = validate_prepared_output_root(&root, config)?;
        let vault_root = fs::canonicalize(&config.vault).map_err(|_| SyncError::DryRunOutput)?;
        Ok(Self {
            temp_state_file: root.join("sync-state.json"),
            root,
            root_canonical,
            vault_root,
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn temp_state_file(&self) -> &Path {
        &self.temp_state_file
    }

    pub fn summary(&self, planned_writes: usize) -> DryRunOutputSummary {
        DryRunOutputSummary {
            dry_run: true,
            output_dir: self.root.to_string_lossy().into_owned(),
            temp_state_file: self.temp_state_file.to_string_lossy().into_owned(),
            planned_writes,
        }
    }

    pub fn read_relative(&self, relative_path: &str) -> Result<Option<String>, SyncError> {
        let output_path = self.resolve_output_path(relative_path, false)?;
        if output_path.exists() {
            return fs::read_to_string(output_path)
                .map(Some)
                .map_err(|_| SyncError::DryRunOutput);
        }

        let vault_path = resolve_read_path(&self.vault_root, relative_path)?;
        if vault_path.exists() {
            fs::read_to_string(vault_path)
                .map(Some)
                .map_err(|_| SyncError::DryRunOutput)
        } else {
            Ok(None)
        }
    }

    pub fn exists_relative(&self, relative_path: &str) -> Result<bool, SyncError> {
        Ok(self.existing_path(relative_path)?.is_some())
    }

    pub fn fingerprint_relative(
        &self,
        relative_path: &str,
    ) -> Result<Option<FileFingerprint>, SyncError> {
        let Some(path) = self.existing_path(relative_path)? else {
            return Ok(None);
        };
        let metadata = fs::metadata(path).map_err(|_| SyncError::DryRunOutput)?;
        Ok(Some(file_fingerprint_from_metadata(&metadata)))
    }

    pub fn write_relative(&self, relative_path: &str, content: &str) -> Result<PathBuf, SyncError> {
        let output_path = self.resolve_output_path(relative_path, true)?;
        write_private_file(&output_path, content)?;
        Ok(output_path)
    }

    fn existing_path(&self, relative_path: &str) -> Result<Option<PathBuf>, SyncError> {
        let output_path = self.resolve_output_path(relative_path, false)?;
        if output_path.exists() {
            return Ok(Some(output_path));
        }

        let vault_path = resolve_read_path(&self.vault_root, relative_path)?;
        if vault_path.exists() {
            Ok(Some(vault_path))
        } else {
            Ok(None)
        }
    }

    fn resolve_output_path(
        &self,
        relative_path: &str,
        create_parent: bool,
    ) -> Result<PathBuf, SyncError> {
        let relative = validate_relative_path(relative_path)?;
        let target = self.root.join(relative);
        if fs::symlink_metadata(&target).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
            return Err(SyncError::DryRunOutput);
        }

        let parent = target.parent().ok_or(SyncError::DryRunOutput)?;
        if create_parent {
            create_parent_dirs(parent, &self.root, &self.root_canonical)?;
        } else if !target.exists() && !parent.exists() {
            return Ok(target);
        }
        let resolved_parent = fs::canonicalize(parent).map_err(|_| SyncError::DryRunOutput)?;
        if !is_within_root(&resolved_parent, &self.root_canonical) {
            return Err(SyncError::DryRunOutput);
        }
        Ok(target)
    }
}

fn validate_requested_output_root(path: &Path, config: &SyncConfig) -> Result<PathBuf, SyncError> {
    if !path.is_absolute() {
        return Err(SyncError::DryRunOutput);
    }
    if fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err(SyncError::DryRunOutput);
    }

    reject_forbidden_output_root(path, config)?;

    if path.exists() {
        if !path.is_dir() {
            return Err(SyncError::DryRunOutput);
        }
        let mut entries = fs::read_dir(path).map_err(|_| SyncError::DryRunOutput)?;
        if entries.next().is_some() {
            return Err(SyncError::DryRunOutput);
        }
    } else {
        create_private_dir_all(path).map_err(|_| SyncError::DryRunOutput)?;
    }

    Ok(path.to_path_buf())
}

fn validate_prepared_output_root(path: &Path, config: &SyncConfig) -> Result<PathBuf, SyncError> {
    reject_symlink_or_non_directory(path)?;
    let canonical = fs::canonicalize(path).map_err(|_| SyncError::DryRunOutput)?;
    reject_resolved_forbidden_output_root(&canonical, config)?;
    reject_symlink_or_non_directory(path)?;
    let mut entries = fs::read_dir(path).map_err(|_| SyncError::DryRunOutput)?;
    if entries.next().is_some() {
        return Err(SyncError::DryRunOutput);
    }
    Ok(canonical)
}

fn reject_symlink_or_non_directory(path: &Path) -> Result<(), SyncError> {
    let metadata = fs::symlink_metadata(path).map_err(|_| SyncError::DryRunOutput)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(SyncError::DryRunOutput);
    }
    Ok(())
}

fn validate_existing_output_root(path: &Path, config: &SyncConfig) -> Result<PathBuf, SyncError> {
    if !path.is_absolute() || !path.is_dir() {
        return Err(SyncError::DryRunOutput);
    }
    reject_forbidden_output_root(path, config)?;
    let mut entries = fs::read_dir(path).map_err(|_| SyncError::DryRunOutput)?;
    if entries.next().is_some() {
        return Err(SyncError::DryRunOutput);
    }
    Ok(path.to_path_buf())
}

fn create_default_output_root(config: &SyncConfig) -> Result<PathBuf, SyncError> {
    create_default_output_root_in_base(config, &std::env::temp_dir())
}

fn create_default_output_root_in_base(
    config: &SyncConfig,
    base: &Path,
) -> Result<PathBuf, SyncError> {
    for attempt in 0..100 {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| SyncError::DryRunOutput)?
            .as_nanos();
        let candidate = base.join(format!(
            "codex-obsidian-sync-rs-dry-run-{}-{now}-{attempt}",
            std::process::id()
        ));
        reject_forbidden_output_root(&candidate, config)?;
        match create_private_dir(&candidate) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(_) => return Err(SyncError::DryRunOutput),
        }
        match validate_existing_output_root(&candidate, config) {
            Ok(path) => return Ok(path),
            Err(error) => {
                let _ = fs::remove_dir(&candidate);
                return Err(error);
            }
        }
    }
    Err(SyncError::DryRunOutput)
}

fn reject_forbidden_output_root(path: &Path, config: &SyncConfig) -> Result<(), SyncError> {
    let output_root = resolved_path_for_validation(path)?;
    reject_resolved_forbidden_output_root(&output_root, config)
}

fn reject_resolved_forbidden_output_root(
    output_root: &Path,
    config: &SyncConfig,
) -> Result<(), SyncError> {
    for forbidden in forbidden_output_roots(config)? {
        if is_within_root(output_root, &forbidden) {
            return Err(SyncError::DryRunOutput);
        }
    }
    Ok(())
}

fn forbidden_output_roots(config: &SyncConfig) -> Result<Vec<PathBuf>, SyncError> {
    let mut roots = Vec::new();
    roots.push(resolved_path_for_validation(&config.vault)?);
    roots.push(resolved_path_for_validation(&config.codex_home)?);
    if let Some(state_dir) = config.state_file.parent() {
        roots.push(resolved_path_for_validation(state_dir)?);
    }
    Ok(roots)
}

fn resolve_read_path(root: &Path, relative_path: &str) -> Result<PathBuf, SyncError> {
    let relative = validate_relative_path(relative_path)?;
    let target = root.join(relative);
    if fs::symlink_metadata(&target).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err(SyncError::DryRunOutput);
    }
    let parent = target.parent().ok_or(SyncError::DryRunOutput)?;
    validate_parent_chain(parent, root)?;
    Ok(target)
}

fn validate_relative_path(relative_path: &str) -> Result<PathBuf, SyncError> {
    let relative = Path::new(relative_path);
    if relative.as_os_str().is_empty() || relative.is_absolute() {
        return Err(SyncError::DryRunOutput);
    }
    let mut saw_normal = false;
    for component in relative.components() {
        match component {
            Component::Normal(_) => saw_normal = true,
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err(SyncError::DryRunOutput);
            }
            Component::CurDir => {}
        }
    }
    if !saw_normal {
        return Err(SyncError::DryRunOutput);
    }
    Ok(relative.to_path_buf())
}

fn resolved_path_for_validation(path: &Path) -> Result<PathBuf, SyncError> {
    if path.exists() {
        fs::canonicalize(path).map_err(|_| SyncError::DryRunOutput)
    } else {
        let (ancestor, missing) = nearest_existing_ancestor(path)?;
        let mut resolved = fs::canonicalize(ancestor).map_err(|_| SyncError::DryRunOutput)?;
        for component in missing.iter().rev() {
            resolved.push(component);
        }
        Ok(resolved)
    }
}

fn nearest_existing_ancestor(path: &Path) -> Result<(&Path, Vec<std::ffi::OsString>), SyncError> {
    if !path.is_absolute() {
        return Err(SyncError::DryRunOutput);
    }
    let mut current = path;
    let mut missing = Vec::new();
    while !current.exists() {
        let name = current.file_name().ok_or(SyncError::DryRunOutput)?;
        missing.push(name.to_os_string());
        current = current.parent().ok_or(SyncError::DryRunOutput)?;
    }
    Ok((current, missing))
}

fn validate_parent_chain(parent: &Path, root: &Path) -> Result<(), SyncError> {
    let (existing, _) = nearest_existing_ancestor(parent)?;
    let resolved_existing = fs::canonicalize(existing).map_err(|_| SyncError::DryRunOutput)?;
    if !is_within_root(&resolved_existing, root) {
        return Err(SyncError::DryRunOutput);
    }
    Ok(())
}

fn create_parent_dirs(parent: &Path, root: &Path, canonical_root: &Path) -> Result<(), SyncError> {
    let relative_parent = parent
        .strip_prefix(root)
        .map_err(|_| SyncError::DryRunOutput)?;
    let mut current = root.to_path_buf();
    for component in relative_parent.components() {
        let Component::Normal(name) = component else {
            return Err(SyncError::DryRunOutput);
        };
        current.push(name);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                return Err(SyncError::DryRunOutput);
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                let parent = current.parent().ok_or(SyncError::DryRunOutput)?;
                validate_parent_chain(parent, canonical_root)?;
                match create_private_dir(&current) {
                    Ok(()) => {}
                    Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
                    Err(_) => return Err(SyncError::DryRunOutput),
                }
            }
            Err(_) => return Err(SyncError::DryRunOutput),
        }

        let resolved_current = fs::canonicalize(&current).map_err(|_| SyncError::DryRunOutput)?;
        if !is_within_root(&resolved_current, canonical_root) {
            return Err(SyncError::DryRunOutput);
        }
    }
    Ok(())
}

fn create_private_dir_all(path: &Path) -> io::Result<()> {
    if path.exists() {
        return Ok(());
    }
    if let Some(parent) = path.parent() {
        if !parent.exists() {
            create_private_dir_all(parent)?;
        }
    }
    match create_private_dir(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => Ok(()),
        Err(error) => Err(error),
    }
}

#[cfg(unix)]
fn create_private_dir(path: &Path) -> io::Result<()> {
    fs::DirBuilder::new().mode(0o700).create(path)
}

#[cfg(not(unix))]
fn create_private_dir(path: &Path) -> io::Result<()> {
    fs::create_dir(path)
}

fn write_private_file(path: &Path, content: &str) -> Result<(), SyncError> {
    let mut file = open_private_file(path).map_err(|_| SyncError::DryRunOutput)?;
    file.write_all(content.as_bytes())
        .map_err(|_| SyncError::DryRunOutput)
}

#[cfg(unix)]
fn open_private_file(path: &Path) -> io::Result<fs::File> {
    fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
}

#[cfg(not(unix))]
fn open_private_file(path: &Path) -> io::Result<fs::File> {
    fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
}

fn is_within_root(target: &Path, root: &Path) -> bool {
    target == root || target.starts_with(root)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_output_rejects_temp_base_inside_vault_before_creation() {
        let root = temp_dir("default-inside-vault");
        let config = sync_config(&root);

        let result = create_default_output_root_in_base(&config, &config.vault);

        assert!(result.is_err());
        assert_eq!(fs::read_dir(&config.vault).unwrap().count(), 0);
    }

    fn sync_config(root: &Path) -> SyncConfig {
        let vault = root.join("vault");
        let codex_home = root.join(".codex");
        let state_dir = root.join("state");
        fs::create_dir_all(&vault).unwrap();
        fs::create_dir_all(&codex_home).unwrap();
        fs::create_dir_all(&state_dir).unwrap();

        SyncConfig {
            codex_home,
            vault,
            state_file: state_dir.join("sync-state.json"),
            lock_file: state_dir.join("sync-state.lock"),
            include_subagents: false,
            interval_seconds: 10,
            recent_days: 30,
            candidate_file_limit: 100,
            candidate_bytes_limit: 500 * 1024 * 1024,
            log_level: "INFO".to_owned(),
        }
    }

    fn temp_dir(name: &str) -> PathBuf {
        let unique = format!(
            "codex-obsidian-sync-rs-dry-run-output-unit-{name}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let path = std::env::temp_dir().join(unique);
        if path.exists() {
            fs::remove_dir_all(&path).unwrap();
        }
        fs::create_dir_all(&path).unwrap();
        path
    }
}
