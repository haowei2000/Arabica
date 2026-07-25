//! Provider-neutral model vocabulary owned by Structure.
//!
//! Runtime and provider adapters exchange these types. Provider wire shapes
//! must not leak into Session Management or the Command/Event Protocol.

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeRole {
    System,
    Developer,
    User,
    Assistant,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum ContentBlock {
    Text {
        text: String,
    },
    Json {
        value: Value,
    },
    ImageUrl {
        url: String,
        media_type: Option<String>,
    },
    InlineData {
        media_type: String,
        data_base64: String,
    },
}

impl ContentBlock {
    pub fn text(value: impl Into<String>) -> Self {
        Self::Text { text: value.into() }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct MessageItem {
    pub id: Option<String>,
    pub role: RuntimeRole,
    pub content: Vec<ContentBlock>,
}

impl MessageItem {
    pub fn text(role: RuntimeRole, text: impl Into<String>) -> Self {
        Self {
            id: None,
            role,
            content: vec![ContentBlock::text(text)],
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ToolCallItem {
    pub id: Option<String>,
    pub call_id: String,
    pub name: String,
    pub arguments: Value,
    pub provider_state: Option<ProviderState>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ToolResultItem {
    pub id: Option<String>,
    pub call_id: String,
    pub name: Option<String>,
    pub content: Vec<ContentBlock>,
    pub is_error: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReasoningItem {
    pub id: Option<String>,
    pub summary: Vec<String>,
    pub provider_state: Option<ProviderState>,
}

/// Typed provider continuation state that must survive a same-provider turn.
///
/// These fields are not portable semantics. Adapters for another API dialect
/// must reject unsupported state instead of silently discarding it.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum ProviderState {
    OpenAi {
        item_id: Option<String>,
        encrypted_content: Option<String>,
    },
    Anthropic {
        signature: String,
    },
    Gemini {
        thought_signature: String,
    },
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum RuntimeItem {
    Message(MessageItem),
    ToolCall(ToolCallItem),
    ToolResult(ToolResultItem),
    Reasoning(ReasoningItem),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub input_schema: Value,
    pub strict: Option<bool>,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum ToolChoice {
    #[default]
    Auto,
    None,
    Required,
    Specific {
        name: String,
    },
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RuntimeRequest {
    pub model: String,
    pub items: Vec<RuntimeItem>,
    pub tools: Vec<ToolDefinition>,
    pub tool_choice: ToolChoice,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum FinishReason {
    Stop,
    Length,
    ToolCalls,
    ContentFilter,
    Provider { value: String },
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct RuntimeUsage {
    pub input_tokens: u64,
    pub output_tokens: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct RuntimeResponse {
    pub items: Vec<RuntimeItem>,
    pub finish_reason: Option<FinishReason>,
    pub usage: RuntimeUsage,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum RuntimeDelta {
    Text {
        text: String,
    },
    ToolArguments {
        call_id: String,
        json_fragment: String,
    },
    ReasoningSummary {
        text: String,
    },
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum RuntimeStreamEvent {
    ItemStarted { index: usize, item: RuntimeItem },
    ItemDelta { index: usize, delta: RuntimeDelta },
    ItemCompleted { index: usize, item: RuntimeItem },
    ResponseCompleted { response: RuntimeResponse },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn typed_tool_items_round_trip_without_provider_wire_shapes() {
        let item = RuntimeItem::ToolCall(ToolCallItem {
            id: Some("item-1".to_owned()),
            call_id: "call-1".to_owned(),
            name: "write_file".to_owned(),
            arguments: serde_json::json!({"path": "note.txt", "content": "hello"}),
            provider_state: None,
        });

        let encoded = serde_json::to_value(&item).expect("runtime item serializes");
        let decoded: RuntimeItem =
            serde_json::from_value(encoded).expect("runtime item deserializes");
        assert_eq!(decoded, item);
    }

    #[test]
    fn tool_schema_is_request_level_not_a_message_block() {
        let request = RuntimeRequest {
            model: "model-1".to_owned(),
            items: vec![RuntimeItem::Message(MessageItem::text(
                RuntimeRole::User,
                "write a file",
            ))],
            tools: vec![ToolDefinition {
                name: "write_file".to_owned(),
                description: "Write a file in the workspace".to_owned(),
                input_schema: serde_json::json!({
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]
                }),
                strict: Some(true),
            }],
            tool_choice: ToolChoice::Auto,
        };

        assert_eq!(request.items.len(), 1);
        assert_eq!(request.tools[0].name, "write_file");
    }
}
