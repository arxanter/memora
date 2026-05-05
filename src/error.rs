pub type Result<T> = std::result::Result<T, MemoraError>;

#[derive(Debug, thiserror::Error)]
pub enum MemoraError {
    #[error("home directory could not be resolved; set MEMORA_HOME or HOME")]
    HomeNotFound,

    #[error("invalid argument: {0}")]
    InvalidArgument(String),

    #[error("not found: {0}")]
    NotFound(String),

    #[error("command is not implemented yet: {0}")]
    NotImplemented(&'static str),

    #[error("{0}")]
    Message(String),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("yaml error: {0}")]
    Yaml(#[from] serde_yaml::Error),

    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
}
