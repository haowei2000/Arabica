//! Versioned, payload-light event trace projection.
use std::collections::{BTreeMap, HashMap};

use crate::{Event, EventEnvelope};

/// UTF-8 percent encoding keeps record and path separators unambiguous.
pub fn trace_atom_v1(value: &str) -> String {
    let mut output = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-') {
            output.push(char::from(byte));
        } else {
            use std::fmt::Write;
            write!(output, "%{byte:02X}").expect("writing to a String cannot fail");
        }
    }
    output
}

// Conservatively recognize a literal executable. Compound commands and shell
// expansion never inherit the label of their first executable.
fn shell_label(command: &str) -> Option<&str> {
    if command.contains([
        ';', '|', '&', '$', '`', '(', ')', '<', '>', '\n', '\r', '\\',
    ]) {
        return None;
    }
    let executable = command.split_whitespace().next()?;
    if !executable
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || b"_-/ .".contains(&byte))
    {
        return None;
    }
    executable
        .rsplit('/')
        .next()
        .filter(|value| !value.is_empty())
}

/// Project an ordered, single-session event stream. Returns no partial trace
/// when its one MiB bound is exceeded. Source envelopes remain authoritative.
/// Every protocol event has a record, including variants added in the future.
pub fn event_trace_v1(events: &[&EventEnvelope]) -> Option<String> {
    let mut ordered = events.to_vec();
    ordered.sort_by_key(|event| event.sequence);
    if ordered.windows(2).any(|pair| {
        pair[0].session_id != pair[1].session_id || pair[0].sequence == pair[1].sequence
    }) {
        return None;
    }
    let mut calls = HashMap::new();
    let mut output = String::new();
    for envelope in ordered {
        let mut fields = BTreeMap::new();
        // The protocol's serde tag is the canonical event type. Only explicit
        // scalar metadata below is exposed; arbitrary payloads are excluded.
        let value = serde_json::to_value(&envelope.event).ok()?;
        let event_type = value.get("type")?.as_str()?;
        let mut path = event_type.to_owned();
        fields.insert("type", event_type.to_owned());
        fields.insert("seq", envelope.sequence.to_string());
        fields.insert("event", envelope.event_id.0.clone());
        if let Some(run) = &envelope.run_id {
            fields.insert("run", run.0.clone());
        }
        if let Some(payload) = value.get("payload") {
            for key in [
                "model_step",
                "is_error",
                "provider_succeeded",
                "outcome",
                "kind",
                "repeat_count",
            ] {
                if let Some(value) = payload.get(key)
                    && (value.is_string() || value.is_boolean() || value.is_number())
                {
                    fields.insert(
                        key,
                        value
                            .as_str()
                            .map_or_else(|| value.to_string(), str::to_owned),
                    );
                }
            }
        }
        match &envelope.event {
            Event::ToolCallRequested {
                call_id,
                name,
                arguments,
                ..
            } => {
                let mut label = trace_atom_v1(name);
                if name == "shell" {
                    if let Some(command) = arguments
                        .get("command")
                        .and_then(|value| value.as_str())
                        .and_then(shell_label)
                    {
                        label.push('.');
                        label.push_str(&trace_atom_v1(command));
                    } else {
                        fields.insert("command_kind", "unknown".into());
                    }
                }
                calls.insert(
                    (envelope.run_id.clone(), call_id.clone()),
                    (name.clone(), label.clone()),
                );
                fields.insert("call", call_id.clone());
                path = format!("toolcall.{label}");
            }
            Event::ToolCallCompleted {
                call_id,
                name,
                is_error,
                ..
            } => {
                fields.insert("call", call_id.clone());
                let label = calls
                    .get(&(envelope.run_id.clone(), call_id.clone()))
                    .filter(|(requested_name, _)| requested_name == name)
                    .map_or_else(|| trace_atom_v1(name), |(_, label)| label.clone());
                path = format!(
                    "toolresult.{label}.{}",
                    if *is_error { "error" } else { "success" }
                );
            }
            Event::ToolExecutionObserved { call_id, .. }
            | Event::ToolCallPermissionRequested { call_id }
            | Event::ToolCallPermissionResolved { call_id, .. }
            | Event::ToolCallClassified { call_id, .. }
            | Event::ToolCallReused { call_id, .. }
            | Event::ToolCallLoopBlocked { call_id, .. } => {
                fields.insert("call", call_id.clone());
            }
            _ => {}
        }
        output.push_str(&path);
        for (key, value) in fields {
            output.push('|');
            output.push_str(key);
            output.push('=');
            output.push_str(&trace_atom_v1(&value));
        }
        output.push('\n');
        if output.len() > 1024 * 1024 {
            return None;
        }
    }
    Some(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delimiters_and_unicode_are_encoded() {
        assert_eq!(trace_atom_v1("a.b|\n中"), "a%2Eb%7C%0A%E4%B8%AD");
    }

    #[test]
    fn compound_commands_do_not_inherit_a_safe_prefix() {
        assert_eq!(shell_label("sed -n '1p' file"), Some("sed"));
        assert_eq!(shell_label("/usr/bin/rg pattern"), Some("rg"));
        for command in [
            "sed x; rm file",
            "sed $(rm file)",
            "X=1 sed x",
            "sed x | sh",
        ] {
            assert_eq!(shell_label(command), None);
        }
    }
}
