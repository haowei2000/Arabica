//! Dependency-free MCP stdio peer compiled by the stateless conformance tests.
//! Modern responses depend only on the current request, without initialization.

use std::fs::OpenOptions;
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
    if let Some(rest) = value.strip_prefix('"') {
        let end = rest.find('"')? + 2;
        Some(&value[..end])
    } else {
        let end = value.find([',', '}']).unwrap_or(value.len());
        Some(value[..end].trim_end())
    }
}

fn quoted(value: &str) -> String {
    let mut result = String::from("\"");
    for character in value.chars() {
        match character {
            '"' => result.push_str("\\\""),
            '\\' => result.push_str("\\\\"),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            character if character.is_control() => {
                result.push_str(&format!("\\u{:04x}", u32::from(character)));
            }
            character => result.push(character),
        }
    }
    result.push('"');
    result
}

fn main() -> io::Result<()> {
    let legacy = std::env::args().nth(1).as_deref() == Some("legacy");
    let transcript_path = std::env::var_os("MCP_FIXTURE_TRANSCRIPT")
        .expect("the conformance test provides a transcript path");
    let mut transcript = OpenOptions::new()
        .create(true)
        .append(true)
        .open(transcript_path)?;
    let mut initialized = false;
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();
    for line in stdin.lock().lines() {
        let line = line?;
        writeln!(transcript, "{line}")?;
        transcript.flush()?;
        let method = string_field(&line, "method").unwrap_or_default();
        if method == "notifications/initialized" && legacy {
            initialized = true;
        }
        let Some(id) = request_id(&line) else {
            continue;
        };
        if !legacy && method == "tools/call" && string_field(&line, "value") == Some("hang") {
            // Leave this request pending while continuing to read cancellation
            // notifications and independent calls on the same connection.
            continue;
        }
        let result = match method {
            "server/discover" if !legacy => r#"{"resultType":"complete","supportedVersions":["2026-07-28"],"capabilities":{"tools":{}},"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"stateless-fixture","version":"1.0"}},"ttlMs":0,"cacheScope":"private"}"#.to_owned(),
            "initialize" if legacy => {
                let version = string_field(&line, "protocolVersion").unwrap_or_default();
                format!("{{\"protocolVersion\":{},\"capabilities\":{{\"tools\":{{}}}},\"serverInfo\":{{\"name\":\"legacy-fixture\",\"version\":\"1.0\"}}}}", quoted(version))
            }
            "tools/list" | "tools/call" if legacy && !initialized => {
                writeln!(stdout, "{{\"jsonrpc\":\"2.0\",\"id\":{id},\"error\":{{\"code\":-32000,\"message\":\"initialize and initialized must precede legacy operations\"}}}}")?;
                stdout.flush()?;
                continue;
            }
            "tools/list" => {
                let prefix = if legacy { "" } else { "\"resultType\":\"complete\",\"ttlMs\":0,\"cacheScope\":\"private\"," };
                if string_field(&line, "cursor") == Some("second-page") {
                    format!("{{{prefix}\"tools\":[{{\"name\":\"failure\",\"description\":\"Return a tool-level error\",\"inputSchema\":{{\"type\":\"object\"}}}},{{\"name\":\"environment\",\"description\":\"Report environment variable names\",\"inputSchema\":{{\"type\":\"object\"}}}}]}}")
                } else {
                    format!("{{{prefix}\"tools\":[{{\"name\":\"structured\",\"description\":\"Return structured content\",\"inputSchema\":{{\"type\":\"object\",\"properties\":{{\"value\":{{\"type\":\"string\"}}}}}}}}],\"nextCursor\":\"second-page\"}}")
                }
            }
            "tools/call" => {
                let prefix = if legacy { "" } else { "\"resultType\":\"complete\"," };
                if line.contains("\"name\":\"failure\"") {
                    format!("{{{prefix}\"content\":[{{\"type\":\"text\",\"text\":\"fixture rejected requested value\"}}],\"structuredContent\":{{\"code\":\"fixture_rejected\"}},\"isError\":true}}")
                } else if line.contains("\"name\":\"environment\"") {
                    let mut names: Vec<_> = std::env::vars_os()
                        .map(|(name, _)| quoted(&name.to_string_lossy()))
                        .collect();
                    names.sort();
                    let token = std::env::var("MCP_FIXTURE_TOKEN").unwrap_or_default();
                    format!("{{{prefix}\"content\":[],\"structuredContent\":{{\"names\":[{}],\"token\":{}}},\"isError\":false}}", names.join(","), quoted(&token))
                } else {
                    let value = string_field(&line, "value").unwrap_or_default();
                    format!("{{{prefix}\"content\":[{{\"type\":\"text\",\"text\":\"structured result\"}}],\"structuredContent\":{{\"value\":{},\"count\":2}},\"isError\":false}}", quoted(value))
                }
            }
            _ => {
                // A modern peer rejects initialize; a legacy peer rejects
                // server/discover, which must trigger the compatibility path.
                writeln!(stdout, "{{\"jsonrpc\":\"2.0\",\"id\":{id},\"error\":{{\"code\":-32601,\"message\":\"method not found\"}}}}")?;
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
