mod agent;
mod cli;
mod config;
mod error;
mod indexer;
mod markdown;
mod memory;
mod raw;
mod sources;
mod util;
mod vault;
mod wiki;

fn main() {
    if let Err(error) = cli::run() {
        eprintln!("error: {error}");
        std::process::exit(1);
    }
}
