//! Translate ACP prompts into canonical text and image inputs.

use agent_client_protocol::schema::v1::ContentBlock;

pub fn prompt_images(blocks: &[ContentBlock]) -> Result<Vec<arabica_model::ImageInput>, String> {
    let images: Vec<_> = blocks
        .iter()
        .filter_map(|block| match block {
            ContentBlock::Image(image) => Some(arabica_model::ImageInput {
                media_type: image.mime_type.clone(),
                data_base64: image.data.clone(),
            }),
            _ => None,
        })
        .collect();
    if images.len() > arabica_model::ImageInput::MAX_IMAGES {
        return Err("At most 4 images are allowed per message".to_owned());
    }
    for image in &images {
        image.validate()?;
    }
    Ok(images)
}

/// Render text and resource links; image bytes are carried separately.
pub fn prompt_text(blocks: &[ContentBlock]) -> String {
    blocks
        .iter()
        .map(render_block)
        .filter(|text| !text.is_empty())
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
        ContentBlock::Image(_) => String::new(),
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
    fn images_are_carried_separately_from_text() {
        let blocks = vec![ContentBlock::Image(ImageContent::new(
            "aGVsbG8=".to_owned(),
            "image/png",
        ))];
        assert!(prompt_text(&blocks).is_empty());
        assert_eq!(prompt_images(&blocks).unwrap()[0].data_base64, "aGVsbG8=");
    }

    #[test]
    fn invalid_or_excessive_images_are_rejected_without_losing_text() {
        let image = |data: &str, mime: &str| ContentBlock::Image(ImageContent::new(data, mime));
        assert!(prompt_images(&[image("not base64!", "image/png")]).is_err());
        assert!(prompt_images(&[image("", "image/png")]).is_err());
        assert!(prompt_images(&[image("aGVsbG8=", "application/pdf")]).is_err());
        assert!(prompt_images(&vec![image("aGVsbG8=", "image/png"); 5]).is_err());
        let blocks = vec![
            ContentBlock::Text(TextContent::new("describe")),
            image("aGVsbG8=", "image/png"),
        ];
        assert_eq!(prompt_text(&blocks).trim(), "describe");
        assert_eq!(prompt_images(&blocks).unwrap().len(), 1);
    }

    #[test]
    fn an_empty_prompt_is_an_empty_string() {
        assert_eq!(prompt_text(&[]), "");
    }
}
