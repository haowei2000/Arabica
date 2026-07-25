use std::error::Error;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};

use structure_server::{AppState, app};
use tokio::net::TcpListener;

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let _ = dotenvy::dotenv();
    let port = std::env::var("STRUCTURE__PORT")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(4096);
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
    let listener = TcpListener::bind(address).await?;
    println!("Structure server listening on http://{address}");
    axum::serve(listener, app(AppState::from_api_env()?))
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    Ok(())
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
}
