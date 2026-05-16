use std::fs;
use std::io::{self, Write};
use std::path::{Component, Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(unix)]
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};

use crate::error::SyncError;
use crate::models::TranscriptMessage;
use crate::render::{
    extract_transcript_body, merge_managed_section, render_transcript_append_text,
};
use crate::state_store::{FileFingerprint, file_fingerprint_from_metadata};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RealWriter {
    vault_root: PathBuf,
}

impl RealWriter {
    pub fn prepare(vault_root: &Path) -> Result<Self, SyncError> {
        Ok(Self {
            vault_root: validate_vault_root(vault_root)?,
        })
    }

    pub fn read_relative(&self, relative_path: &str) -> Result<Option<String>, SyncError> {
        let target = self.resolve_read_path(relative_path)?;
        if !target.exists() {
            return Ok(None);
        }
        fs::read_to_string(target)
            .map(Some)
            .map_err(|_| SyncError::Write)
    }

    pub fn exists_relative(&self, relative_path: &str) -> Result<bool, SyncError> {
        Ok(self.resolve_read_path(relative_path)?.exists())
    }

    pub fn fingerprint_relative(
        &self,
        relative_path: &str,
    ) -> Result<Option<FileFingerprint>, SyncError> {
        let target = self.resolve_read_path(relative_path)?;
        if !target.exists() {
            return Ok(None);
        }
        let metadata = fs::metadata(target).map_err(|_| SyncError::Write)?;
        Ok(Some(file_fingerprint_from_metadata(&metadata)))
    }

    pub fn write_managed_file(
        &self,
        relative_path: &str,
        content: &str,
    ) -> Result<PathBuf, SyncError> {
        let target = self.resolve_note_path(relative_path)?;
        write_atomic(&target, content)?;
        Ok(target)
    }

    pub fn append_conversation_transcript(
        &self,
        relative_path: &str,
        messages: &[TranscriptMessage],
    ) -> Result<bool, SyncError> {
        let Some(content) = render_transcript_append_text(messages) else {
            return Ok(false);
        };
        let target = self.resolve_note_path(relative_path)?;
        append_file(&target, &content)?;
        Ok(true)
    }

    pub fn rewrite_conversation_header(
        &self,
        relative_path: &str,
        header: &str,
    ) -> Result<PathBuf, SyncError> {
        let target = self.resolve_note_path(relative_path)?;
        let existing = fs::read_to_string(&target).map_err(|_| SyncError::Write)?;
        let transcript_body = extract_transcript_body(&existing).ok_or(SyncError::Write)?;
        write_atomic(
            &target,
            &(format!("{header}{transcript_body}").trim_end().to_owned() + "\n"),
        )?;
        Ok(target)
    }

    pub fn write_sectioned_note(
        &self,
        relative_path: &str,
        header: &str,
        managed_content: &str,
    ) -> Result<PathBuf, SyncError> {
        let target = self.resolve_note_path(relative_path)?;
        let existing = if target.exists() {
            fs::read_to_string(&target).map_err(|_| SyncError::Write)?
        } else {
            String::new()
        };
        let base = if existing.is_empty() {
            format!("{header}\n")
        } else {
            existing
        };
        let merged = merge_managed_section(&base, managed_content);
        write_atomic(&target, &merged)?;
        Ok(target)
    }

    pub fn resolve_note_path(&self, relative_path: &str) -> Result<PathBuf, SyncError> {
        let relative = validate_relative_path(relative_path)?;
        let target = self.vault_root.join(relative);
        reject_target_symlink(&target)?;
        let parent = target.parent().ok_or(SyncError::Write)?;
        create_parent_dirs(parent, &self.vault_root)?;
        reject_target_symlink(&target)?;
        Ok(target)
    }

    fn resolve_read_path(&self, relative_path: &str) -> Result<PathBuf, SyncError> {
        let relative = validate_relative_path(relative_path)?;
        let target = self.vault_root.join(relative);
        reject_target_symlink(&target)?;
        let parent = target.parent().ok_or(SyncError::Write)?;
        validate_parent_chain(parent, &self.vault_root)?;
        Ok(target)
    }
}

pub fn validate_vault_root(vault_root: &Path) -> Result<PathBuf, SyncError> {
    if vault_root.as_os_str().is_empty() || !vault_root.is_absolute() {
        return Err(SyncError::Write);
    }
    let resolved = fs::canonicalize(vault_root).map_err(|_| SyncError::Write)?;
    let metadata = fs::metadata(&resolved).map_err(|_| SyncError::Write)?;
    if !metadata.is_dir() {
        return Err(SyncError::Write);
    }
    Ok(resolved)
}

pub fn write_state_file(path: &Path, content: &str) -> Result<(), SyncError> {
    if !path.is_absolute() {
        return Err(SyncError::Write);
    }
    let parent = path.parent().ok_or(SyncError::Write)?;
    create_absolute_dirs_without_symlinks(parent)?;
    reject_target_symlink(path)?;
    write_atomic(path, content)
}

pub fn write_atomic(target: &Path, content: &str) -> Result<(), SyncError> {
    let parent = target.parent().ok_or(SyncError::Write)?;
    let temp = temp_path(parent);
    let result = (|| {
        let mut file = open_new_private_file(&temp).map_err(|_| SyncError::Write)?;
        file.write_all(content.as_bytes())
            .map_err(|_| SyncError::Write)?;
        file.sync_all().map_err(|_| SyncError::Write)?;
        reject_target_symlink(target)?;
        fs::rename(&temp, target).map_err(|_| SyncError::Write)?;
        fsync_directory(parent);
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

fn append_file(target: &Path, content: &str) -> Result<(), SyncError> {
    reject_target_symlink(target)?;
    let mut file = open_append_file(target).map_err(|_| SyncError::Write)?;
    file.write_all(content.as_bytes())
        .map_err(|_| SyncError::Write)?;
    file.sync_all().map_err(|_| SyncError::Write)
}

fn validate_relative_path(relative_path: &str) -> Result<PathBuf, SyncError> {
    let relative = Path::new(relative_path);
    if relative.as_os_str().is_empty() || relative.is_absolute() {
        return Err(SyncError::Write);
    }
    let mut saw_normal = false;
    for component in relative.components() {
        match component {
            Component::Normal(_) => saw_normal = true,
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err(SyncError::Write);
            }
            Component::CurDir => {}
        }
    }
    if !saw_normal {
        return Err(SyncError::Write);
    }
    Ok(relative.to_path_buf())
}

fn reject_target_symlink(target: &Path) -> Result<(), SyncError> {
    match fs::symlink_metadata(target) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(SyncError::Write),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(SyncError::Write),
    }
}

fn create_parent_dirs(parent: &Path, root: &Path) -> Result<(), SyncError> {
    let relative_parent = parent.strip_prefix(root).map_err(|_| SyncError::Write)?;
    let mut current = root.to_path_buf();
    for component in relative_parent.components() {
        let Component::Normal(name) = component else {
            return Err(SyncError::Write);
        };
        current.push(name);
        match fs::symlink_metadata(&current) {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                return Err(SyncError::Write);
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                let current_parent = current.parent().ok_or(SyncError::Write)?;
                validate_parent_chain(current_parent, root)?;
                create_private_dir(&current).map_err(|_| SyncError::Write)?;
            }
            Err(_) => return Err(SyncError::Write),
        }
        validate_parent_chain(&current, root)?;
    }
    Ok(())
}

fn validate_parent_chain(parent: &Path, root: &Path) -> Result<(), SyncError> {
    let existing = nearest_existing_ancestor(parent)?;
    let resolved = fs::canonicalize(existing).map_err(|_| SyncError::Write)?;
    if is_within_root(&resolved, root) {
        Ok(())
    } else {
        Err(SyncError::Write)
    }
}

fn nearest_existing_ancestor(path: &Path) -> Result<&Path, SyncError> {
    let mut current = path;
    while !current.exists() {
        current = current.parent().ok_or(SyncError::Write)?;
    }
    Ok(current)
}

fn create_absolute_dirs_without_symlinks(path: &Path) -> Result<(), SyncError> {
    if !path.is_absolute() {
        return Err(SyncError::Write);
    }
    let mut current = PathBuf::new();
    for component in path.components() {
        match component {
            Component::RootDir => current.push(component.as_os_str()),
            Component::Normal(name) => {
                current.push(name);
                match fs::symlink_metadata(&current) {
                    Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                        return Err(SyncError::Write);
                    }
                    Ok(_) => {}
                    Err(error) if error.kind() == io::ErrorKind::NotFound => {
                        create_private_dir(&current).map_err(|_| SyncError::Write)?;
                    }
                    Err(_) => return Err(SyncError::Write),
                }
            }
            Component::CurDir => {}
            Component::ParentDir | Component::Prefix(_) => return Err(SyncError::Write),
        }
    }
    Ok(())
}

#[cfg(unix)]
fn create_private_dir(path: &Path) -> io::Result<()> {
    fs::DirBuilder::new().mode(0o700).create(path)
}

#[cfg(not(unix))]
fn create_private_dir(path: &Path) -> io::Result<()> {
    fs::create_dir(path)
}

fn temp_path(parent: &Path) -> PathBuf {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or(0);
    parent.join(format!(
        ".codex-obsidian-sync-rs-{}-{now}.tmp",
        std::process::id()
    ))
}

#[cfg(unix)]
fn open_new_private_file(path: &Path) -> io::Result<fs::File> {
    fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
}

#[cfg(not(unix))]
fn open_new_private_file(path: &Path) -> io::Result<fs::File> {
    fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
}

#[cfg(unix)]
fn open_append_file(path: &Path) -> io::Result<fs::File> {
    fs::OpenOptions::new()
        .append(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
}

#[cfg(not(unix))]
fn open_append_file(path: &Path) -> io::Result<fs::File> {
    fs::OpenOptions::new().append(true).create(true).open(path)
}

fn fsync_directory(directory: &Path) {
    let Ok(directory) = fs::File::open(directory) else {
        return;
    };
    let _ = directory.sync_all();
}

fn is_within_root(target: &Path, root: &Path) -> bool {
    target == root || target.starts_with(root)
}
