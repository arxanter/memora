mod agent;
mod cli;
mod config;
mod error;
mod freshness;
mod indexer;
mod markdown;
mod memory;
mod raw;
mod session;
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
