use thiserror::Error;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum SyncError {
    #[error("config error")]
    Config,

    #[error("parse error")]
    Parse,

    #[error("discovery error")]
    Discovery,

    #[error("render error")]
    Render,

    #[error("dry-run output error")]
    DryRunOutput,

    #[error("write error")]
    Write,

    #[error("unsupported command")]
    UnsupportedCommand,
}
