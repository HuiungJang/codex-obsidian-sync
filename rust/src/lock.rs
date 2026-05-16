use std::fs;
use std::io::{self, Seek, SeekFrom, Write};
use std::path::Path;

#[cfg(unix)]
use std::os::fd::AsRawFd;
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

use crate::error::SyncError;

pub struct ProcessLock {
    file: fs::File,
}

impl ProcessLock {
    pub fn try_acquire(path: &Path) -> Result<Self, SyncError> {
        if !path.is_absolute() {
            return Err(SyncError::Lock);
        }
        let parent = path.parent().ok_or(SyncError::Lock)?;
        fs::create_dir_all(parent).map_err(|_| SyncError::Lock)?;
        reject_symlink(path)?;
        let mut file = open_lock_file(path).map_err(|_| SyncError::Lock)?;
        acquire_nonblocking(&file)?;
        file.set_len(0).map_err(|_| SyncError::Lock)?;
        file.seek(SeekFrom::Start(0)).map_err(|_| SyncError::Lock)?;
        writeln!(
            file,
            "{}",
            path.file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("sync.lock")
        )
        .map_err(|_| SyncError::Lock)?;
        file.flush().map_err(|_| SyncError::Lock)?;
        Ok(Self { file })
    }
}

#[cfg(unix)]
impl Drop for ProcessLock {
    fn drop(&mut self) {
        unsafe {
            libc::flock(self.file.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

#[cfg(not(unix))]
impl Drop for ProcessLock {
    fn drop(&mut self) {}
}

fn reject_symlink(path: &Path) -> Result<(), SyncError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(SyncError::Lock),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(SyncError::Lock),
    }
}

#[cfg(unix)]
fn open_lock_file(path: &Path) -> io::Result<fs::File> {
    fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .append(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
}

#[cfg(not(unix))]
fn open_lock_file(path: &Path) -> io::Result<fs::File> {
    fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .append(false)
        .open(path)
}

#[cfg(unix)]
fn acquire_nonblocking(file: &fs::File) -> Result<(), SyncError> {
    let rc = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
    if rc == 0 {
        return Ok(());
    }
    let error = io::Error::last_os_error();
    if error.kind() == io::ErrorKind::WouldBlock {
        Err(SyncError::LockContention)
    } else {
        Err(SyncError::Lock)
    }
}

#[cfg(not(unix))]
fn acquire_nonblocking(_file: &fs::File) -> Result<(), SyncError> {
    Ok(())
}
