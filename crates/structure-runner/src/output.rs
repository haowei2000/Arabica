//! Bounded tool output for model-facing results.
//!
//! Tool output reaches the model inside a single tool result, so an unbounded
//! command can exhaust the context window. Truncation keeps both ends: the head
//! carries the command's own framing and the tail carries the failure most
//! commands report last.

/// Maximum characters of tool output handed to the model before truncation.
pub const MAX_TOOL_OUTPUT_CHARS: usize = 8_000;

pub fn truncate_output(value: String) -> String {
    let chars = value.chars().count();
    if chars <= MAX_TOOL_OUTPUT_CHARS {
        return value;
    }
    let half = MAX_TOOL_OUTPUT_CHARS / 2;
    let head: String = value.chars().take(half).collect();
    let tail: String = value.chars().skip(chars.saturating_sub(half)).collect();
    format!(
        "{head}\n...[truncated {} characters; rerun a narrower command to inspect them]...\n{tail}",
        chars - MAX_TOOL_OUTPUT_CHARS
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn output_truncation_keeps_both_ends() {
        let value = format!("HEAD{}TAIL", "x".repeat(MAX_TOOL_OUTPUT_CHARS));
        let truncated = truncate_output(value);
        assert!(truncated.starts_with("HEAD"));
        assert!(truncated.ends_with("TAIL"));
        assert!(truncated.contains("[truncated"));
    }
}
