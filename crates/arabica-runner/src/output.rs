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
    let max_search_chars = half.min(500);

    // Byte index of character `half`
    let head_byte_idx = value
        .char_indices()
        .nth(half)
        .map(|(idx, _)| idx)
        .unwrap_or(value.len());

    let head_slice = &value[..head_byte_idx];
    let (head, head_char_count) = if let Some(last_nl) = head_slice.rfind('\n') {
        let trimmed_head = &head_slice[..last_nl];
        let trimmed_head = trimmed_head.strip_suffix('\r').unwrap_or(trimmed_head);
        let trimmed_chars = trimmed_head.chars().count();
        if half.saturating_sub(trimmed_chars) <= max_search_chars {
            (trimmed_head, trimmed_chars)
        } else {
            (head_slice, half)
        }
    } else {
        (head_slice, half)
    };

    // Byte index of character `chars - half`
    let tail_start_char = chars.saturating_sub(half);
    let tail_byte_idx = value
        .char_indices()
        .nth(tail_start_char)
        .map(|(idx, _)| idx)
        .unwrap_or(0);

    let tail_slice = &value[tail_byte_idx..];
    let (tail, tail_char_count) = if let Some(first_nl) = tail_slice.find('\n') {
        let trimmed_tail = &tail_slice[first_nl + 1..];
        let skipped_chars = tail_slice[..=first_nl].chars().count();
        if skipped_chars <= max_search_chars {
            (trimmed_tail, half.saturating_sub(skipped_chars))
        } else {
            (tail_slice, half)
        }
    } else {
        (tail_slice, half)
    };

    let omitted_chars = chars.saturating_sub(head_char_count + tail_char_count);
    format!(
        "{head}\n...[truncated {omitted_chars} characters; rerun a narrower command to inspect them]...\n{tail}"
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

    #[test]
    fn output_truncation_aligns_to_line_boundaries() {
        let lines: Vec<String> = (0..500).map(|i| format!("line {i}: content {i}")).collect();
        let value = lines.join("\n");
        let truncated = truncate_output(value);
        assert!(truncated.starts_with("line 0: content 0"));
        assert!(truncated.ends_with("line 499: content 499"));
        assert!(truncated.contains("[truncated"));
        for line in truncated.lines() {
            if line.starts_with("...[truncated") {
                continue;
            }
            assert!(
                line.starts_with("line ") && line.contains(": content "),
                "broken line: {line}"
            );
        }
    }

    #[test]
    fn output_truncation_handles_unicode() {
        let line = "你好世界，这是一段中文测试内容。\n";
        let value = line.repeat(500);
        let truncated = truncate_output(value);
        assert!(truncated.starts_with("你好世界"));
        assert!(truncated.trim_end().ends_with("这是一段中文测试内容。"));
        assert!(truncated.contains("[truncated"));
        for l in truncated.lines() {
            if l.starts_with("...[truncated") {
                continue;
            }
            assert_eq!(l, "你好世界，这是一段中文测试内容。");
        }
    }
}
