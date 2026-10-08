//! Provider-neutral context identities and payload-free governance evidence.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

#[derive(
    Clone, Copy, Debug, Deserialize, Eq, JsonSchema, Ord, PartialEq, PartialOrd, Serialize,
)]
#[serde(rename_all = "snake_case")]
pub enum ContextSourceKind {
    Tool,
    Mcp,
    Skill,
}

/// Only exposure mode changes; policy enablement remains a separate decision.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextMode {
    Folded,
    #[default]
    Unfolded,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextItemType {
    Tool,
    McpServer,
    McpTool,
    Skill,
}

impl ContextIdentity {
    pub fn item_type(&self) -> ContextItemType {
        match self.kind {
            ContextSourceKind::Tool => ContextItemType::Tool,
            ContextSourceKind::Skill => ContextItemType::Skill,
            ContextSourceKind::Mcp if self.tool_id.is_some() => ContextItemType::McpTool,
            ContextSourceKind::Mcp => ContextItemType::McpServer,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct ContextIdentity {
    pub id: String,
    pub kind: ContextSourceKind,
    pub version: Option<String>,
    pub display_name: String,
    pub server_id: Option<String>,
    pub tool_id: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextDecisionReason {
    LegacyDefault,
    Infrastructure,
    Included,
    NotSelected,
    ExplicitlyExcluded,
    SourceDisabled,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct ContextDecision {
    pub identity: ContextIdentity,
    pub exposed_name: String,
    pub enabled: bool,
    pub reason: ContextDecisionReason,
    pub sets: Vec<String>,
    #[serde(default)]
    pub initial_mode: ContextMode,
}

#[derive(Clone, Debug, Deserialize, Eq, JsonSchema, PartialEq, Serialize)]
pub struct ContextRunSnapshot {
    pub decision_id: String,
    pub policy_id: String,
    pub policy_version: u64,
    pub policy_fingerprint: String,
    pub catalog_fingerprint: String,
    #[serde(default)]
    pub selected_sets: Vec<String>,
    #[serde(default)]
    pub included_ids: Vec<String>,
    #[serde(default)]
    pub excluded_ids: Vec<String>,
    #[serde(default)]
    pub disabled_sources: Vec<ContextSourceKind>,
    pub items: Vec<ContextDecision>,
}
