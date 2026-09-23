//! Minimal standalone stdio MCP peer for the ACP integration test.
//! Compiled with `rustc` by the test, so it has no workspace dependencies.

use std::io::{self, BufRead, Write};

fn string_field<'a>(line: &'a str, name: &str) -> Option<&'a str> {
    let key = format!("\"{name}\":");
    let value = line.split_once(&key)?.1.trim_start();
    value
        .strip_prefix('"')?
        .split_once('"')
        .map(|(text, _)| text)
}

fn request_id(line: &str) -> Option<&str> {
    let value = line.split_once("\"id\":")?.1.trim_start();
    if value.starts_with('"') {
        let (_, rest) = value.split_at(1);
        let end = rest.find('"')? + 2;
        Some(&value[..end])
    } else {
        let end = value.find([',', '}']).unwrap_or(value.len());
        Some(value[..end].trim_end())
    }
}

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();
    for line in stdin.lock().lines() {
        let line = line?;
        let Some(id) = request_id(&line) else {
            continue;
        };
        let result = match string_field(&line, "method") {
            Some("initialize") => {
                let version = string_field(&line, "protocolVersion").unwrap_or("2025-06-18");
                format!("{{\"protocolVersion\":\"{version}\",\"capabilities\":{{\"tools\":{{}}}},\"serverInfo\":{{\"name\":\"fixture\",\"version\":\"1.0\"}}}}")
            }
            Some("tools/list") => r#"{"tools":[{"name":"echo","description":"Echo one value","inputSchema":{"type":"object","properties":{"value":{"type":"string"}},"required":["value"]}}]}"#.to_owned(),
            Some("tools/call") => {
                let value = string_field(&line, "value").unwrap_or_default();
                let marker = std::env::var("MCP_TEST_MARKER")
                    .expect("test supplies MCP_TEST_MARKER");
                std::fs::write(marker, value)?;
                format!("{{\"content\":[{{\"type\":\"text\",\"text\":\"{value}\"}}]}}")
            }
            _ => {
                writeln!(stdout, "{{\"jsonrpc\":\"2.0\",\"id\":{id},\"error\":{{\"code\":-32601,\"message\":\"unknown method\"}}}}")?;
                stdout.flush()?;
                continue;
            }
        };
        writeln!(
            stdout,
            "{{\"jsonrpc\":\"2.0\",\"id\":{id},\"result\":{result}}}"
        )?;
        stdout.flush()?;
    }
    Ok(())
}
