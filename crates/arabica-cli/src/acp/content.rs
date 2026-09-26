//! Renders an ACP prompt into the single string `Command::MessageSend`
//! expects.
//!
//! Structure's own protocol carries a user turn as one `content: String`
//! (`crates/arabica-protocol/src/lib.rs`); ACP's `PromptRequest.prompt` is
//! `Vec<ContentBlock>`. This is the one place that gap is bridged, so every
//! prompt handler renders identically regardless of what the client sent.

use agent_client_protocol::schema::v1::ContentBlock;

/// Render a prompt's content blocks as plain text.
///
/// Every agent MUST accept [`ContentBlock::Text`] and
/// [`ContentBlock::ResourceLink`] (the ACP baseline); this agent's
/// `initialize` response advertises no other prompt capability, so a
/// compliant client sends nothing else. A block outside the baseline is
/// rendered as a short marker rather than silently dropped, since a
/// non-compliant client sending one is still better served by the model
/// knowing something was attached than by losing it without a trace.
pub fn prompt_text(blocks: &[ContentBlock]) -> String {
    blocks
        .iter()
        .map(render_block)
        .collect::<Vec<_>>()
        .join("\n")
}

fn render_block(block: &ContentBlock) -> String {
    match block {
        ContentBlock::Text(text) => text.text.clone(),
        ContentBlock::ResourceLink(link) => match &link.description {
            Some(description) => format!("@{} ({}): {description}", link.name, link.uri),
            None => format!("@{} ({})", link.name, link.uri),
        },
        ContentBlock::Image(_) => "[image content, not supported by this agent]".to_owned(),
        ContentBlock::Audio(_) => "[audio content, not supported by this agent]".to_owned(),
        ContentBlock::Resource(_) => "[embedded resource, not supported by this agent]".to_owned(),
        // `ContentBlock` is `#[non_exhaustive]`: a future protocol revision
        // may add a variant this agent does not know about yet. Same
        // treatment as the other unsupported kinds above, not a panic.
        _ => "[unrecognized content, not supported by this agent]".to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use agent_client_protocol::schema::v1::{ImageContent, ResourceLink, TextContent};

    #[test]
    fn text_blocks_join_with_newlines() {
        let blocks = vec![
            ContentBlock::Text(TextContent::new("first")),
            ContentBlock::Text(TextContent::new("second")),
        ];
        assert_eq!(prompt_text(&blocks), "first\nsecond");
    }

    #[test]
    fn a_resource_link_renders_as_an_at_mention() {
        let blocks = vec![ContentBlock::ResourceLink(ResourceLink::new(
            "main.rs",
            "file:///repo/src/main.rs",
        ))];
        assert_eq!(prompt_text(&blocks), "@main.rs (file:///repo/src/main.rs)");
    }

    #[test]
    fn an_unsupported_block_becomes_a_visible_marker_not_silence() {
        let blocks = vec![ContentBlock::Image(ImageContent::new(
            String::new(),
            "image/png",
        ))];
        assert!(prompt_text(&blocks).contains("not supported"));
    }

    #[test]
    fn an_empty_prompt_is_an_empty_string() {
        assert_eq!(prompt_text(&[]), "");
    }
}
