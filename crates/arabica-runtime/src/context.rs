//! Deterministic flat context sets and immutable run decisions.

use std::collections::{BTreeMap, BTreeSet};

use arabica_model::ToolDefinition;
use arabica_protocol::{
    ContextDecision, ContextDecisionReason, ContextIdentity, ContextMode, ContextRunSnapshot,
    ContextSourceKind,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::{RuntimeError, RuntimeErrorKind};

/// No policy preserves legacy exposure. An explicit policy selects only its
/// sets and includes; source disables and exclusions always take precedence.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ContextPolicy {
    pub policy_id: String,
    pub version: u64,
    #[serde(default)]
    pub sets: BTreeMap<String, BTreeSet<String>>,
    #[serde(default)]
    pub base_sets: BTreeSet<String>,
    #[serde(default)]
    pub include: BTreeSet<String>,
    #[serde(default)]
    pub exclude: BTreeSet<String>,
    #[serde(default)]
    pub disabled_sources: BTreeSet<ContextSourceKind>,
    #[serde(default = "default_folded_mode")]
    pub default_mode: ContextMode,
    #[serde(default)]
    pub unfolded: BTreeSet<String>,
}

fn default_folded_mode() -> ContextMode {
    ContextMode::Folded
}

/// A host-loaded immutable instruction bundle. Resource keys are relative
/// names; hosts validate paths and capture content before handing it to Runtime.
#[derive(Clone, Debug)]
pub struct SkillDefinition {
    pub identity: ContextIdentity,
    pub instructions: String,
    pub requirements: BTreeSet<String>,
    pub resources: BTreeMap<String, String>,
}

/// Hosts own discovery and I/O; Runtime only pins the returned immutable bundle.
pub trait SkillSource: std::fmt::Debug + Send {
    fn load(&self) -> Result<Vec<SkillDefinition>, RuntimeError>;
}

pub fn mcp_server_context_id(server_id: &str) -> String {
    format!(
        "mcp-server:{}",
        serde_json::to_string(server_id).expect("server ID")
    )
}

fn invalid(message: impl Into<String>) -> RuntimeError {
    RuntimeError::new(RuntimeErrorKind::InvalidConfiguration, message)
}

pub(crate) fn fingerprint(value: &impl Serialize) -> String {
    // All values here contain only serializable, provider-neutral data.
    hex::encode(Sha256::digest(
        serde_json::to_vec(value).expect("context serialization"),
    ))
}

pub fn tool_context_identity(tool: &ToolDefinition) -> ContextIdentity {
    ContextIdentity {
        id: format!("tool:{}", tool.name),
        kind: ContextSourceKind::Tool,
        version: Some(fingerprint(&tool.input_schema)),
        display_name: tool.name.clone(),
        server_id: None,
        tool_id: Some(tool.name.clone()),
    }
}

/// JSON tuple encoding avoids ambiguity from separators or sanitized names.
pub fn mcp_context_identity(
    server_id: &str,
    tool: &ToolDefinition,
    tool_id: &str,
) -> ContextIdentity {
    ContextIdentity {
        id: format!(
            "mcp:{}",
            serde_json::to_string(&(server_id, tool_id)).expect("identity")
        ),
        kind: ContextSourceKind::Mcp,
        version: Some(fingerprint(&tool.input_schema)),
        display_name: tool.name.clone(),
        server_id: Some(server_id.to_owned()),
        tool_id: Some(tool_id.to_owned()),
    }
}

impl ContextPolicy {
    pub fn validate(&self) -> Result<(), RuntimeError> {
        if self.policy_id.trim().is_empty() || self.version == 0 {
            return Err(invalid(
                "context policy requires a non-empty ID and positive version",
            ));
        }
        if self.exclude.contains("tool:context_unfold")
            || self.exclude.contains("tool:context_read")
        {
            return Err(invalid(
                "context infrastructure cannot be explicitly excluded",
            ));
        }
        if self.sets.keys().any(|name| name.trim().is_empty()) {
            return Err(invalid("context set names must not be empty"));
        }
        if self
            .base_sets
            .iter()
            .any(|name| !self.sets.contains_key(name))
        {
            return Err(invalid("context policy references an unknown base set"));
        }
        if self
            .include
            .iter()
            .chain(&self.exclude)
            .chain(&self.unfolded)
            .chain(self.sets.values().flatten())
            .any(|id| id.trim().is_empty())
        {
            return Err(invalid("context references must not be empty"));
        }
        Ok(())
    }
}

pub(crate) fn resolve_catalog(
    tools: &[ToolDefinition],
    identities: &BTreeMap<String, ContextIdentity>,
    skills: &[SkillDefinition],
    policy: Option<&ContextPolicy>,
    decision_id: String,
) -> Result<ContextRunSnapshot, RuntimeError> {
    let mut catalog = BTreeMap::new();
    let mut names = BTreeSet::new();
    for tool in tools {
        let identity = identities
            .get(&tool.name)
            .cloned()
            .unwrap_or_else(|| tool_context_identity(tool));
        if tool.name.trim().is_empty()
            || identity.id.trim().is_empty()
            || !names.insert(tool.name.clone())
            || catalog
                .insert(identity.id.clone(), (tool.name.clone(), identity))
                .is_some()
        {
            return Err(invalid(
                "duplicate or empty context identity or exposed tool name",
            ));
        }
    }
    for skill in skills {
        if skill.identity.kind != ContextSourceKind::Skill
            || skill.identity.id.trim().is_empty()
            || skill.identity.display_name.trim().is_empty()
            || catalog
                .insert(
                    skill.identity.id.clone(),
                    (skill.identity.display_name.clone(), skill.identity.clone()),
                )
                .is_some()
        {
            return Err(invalid("duplicate or invalid skill identity"));
        }
    }
    let servers: BTreeSet<_> = identities
        .values()
        .filter_map(|identity| identity.server_id.clone())
        .collect();
    for server_id in servers {
        let id = mcp_server_context_id(&server_id);
        let identity = ContextIdentity {
            id: id.clone(),
            kind: ContextSourceKind::Mcp,
            version: None,
            display_name: server_id.clone(),
            server_id: Some(server_id.clone()),
            tool_id: None,
        };
        if catalog.insert(id, (server_id, identity)).is_some() {
            return Err(invalid("duplicate MCP server identity"));
        }
    }
    let mut typed_names = BTreeSet::new();
    for (name, identity) in catalog.values() {
        let key = format!("{:?}:{name}", identity.item_type());
        if !typed_names.insert(key) {
            return Err(invalid("ambiguous context name and type"));
        }
    }
    if let Some(policy) = policy {
        policy.validate()?;
        for id in policy
            .include
            .iter()
            .chain(&policy.exclude)
            .chain(&policy.unfolded)
            .chain(policy.sets.values().flatten())
        {
            if !catalog.contains_key(id) {
                // Do not echo an untrusted identifier into diagnostics.
                return Err(invalid(
                    "context policy references an unknown or unavailable item",
                ));
            }
        }
    }
    let catalog_fingerprint = fingerprint(&catalog);
    let mut items: Vec<ContextDecision> = catalog
        .into_values()
        .map(|(exposed_name, identity)| {
            let sets: Vec<_> = policy
                .into_iter()
                .flat_map(|p| p.sets.iter())
                .filter(|(_, members)| members.contains(&identity.id))
                .map(|(name, _)| name.clone())
                .collect();
            let server_context = identity.server_id.as_deref().map(mcp_server_context_id);
            let selected = |p: &ContextPolicy, id: &str| {
                p.include.contains(id) || p.base_sets.iter().any(|set| p.sets[set].contains(id))
            };
            let infrastructure = matches!(exposed_name.as_str(), "context_unfold" | "context_read")
                && identity.kind == ContextSourceKind::Tool;
            let reason = match policy {
                _ if infrastructure => ContextDecisionReason::Infrastructure,
                None => ContextDecisionReason::LegacyDefault,
                Some(p)
                    if p.exclude.contains(&identity.id)
                        || server_context
                            .as_ref()
                            .is_some_and(|id| p.exclude.contains(id)) =>
                {
                    ContextDecisionReason::ExplicitlyExcluded
                }
                Some(p) if p.disabled_sources.contains(&identity.kind) => {
                    ContextDecisionReason::SourceDisabled
                }
                Some(p)
                    if selected(p, &identity.id)
                        || server_context.as_ref().is_some_and(|id| selected(p, id)) =>
                {
                    ContextDecisionReason::Included
                }
                Some(_) => ContextDecisionReason::NotSelected,
            };
            let enabled = matches!(
                reason,
                ContextDecisionReason::LegacyDefault
                    | ContextDecisionReason::Included
                    | ContextDecisionReason::Infrastructure
            );
            let initial_mode =
                if infrastructure || exposed_name == crate::RUNTIME_COMPLETE_TOOL_NAME {
                    ContextMode::Unfolded
                } else if let Some(p) = policy {
                    if p.unfolded.contains(&identity.id) {
                        ContextMode::Unfolded
                    } else {
                        p.default_mode
                    }
                } else if identity.kind == ContextSourceKind::Skill {
                    ContextMode::Folded
                } else {
                    ContextMode::Unfolded
                };
            ContextDecision {
                identity,
                exposed_name,
                enabled,
                reason,
                sets,
                initial_mode,
            }
        })
        .collect();
    // A server is discoverable whenever it has an enabled child. Its explicit
    // exclusion/source disable still wins and already disabled the children.
    let enabled_servers: BTreeSet<_> = items
        .iter()
        .filter(|item| item.enabled && item.identity.tool_id.is_some())
        .filter_map(|item| item.identity.server_id.clone())
        .collect();
    for item in &mut items {
        if item.identity.kind == ContextSourceKind::Mcp
            && item.identity.tool_id.is_none()
            && item
                .identity
                .server_id
                .as_ref()
                .is_some_and(|id| enabled_servers.contains(id))
        {
            item.enabled = true;
            if item.reason == ContextDecisionReason::NotSelected {
                item.reason = ContextDecisionReason::Included;
            }
        }
    }
    for skill in skills {
        if items
            .iter()
            .any(|item| item.identity.id == skill.identity.id && item.enabled)
            && skill.requirements.iter().any(|id| {
                !items
                    .iter()
                    .any(|item| &item.identity.id == id && item.enabled)
            })
        {
            return Err(invalid(
                "enabled skill has an unavailable or disabled required context item",
            ));
        }
    }
    Ok(ContextRunSnapshot {
        decision_id,
        policy_id: policy.map_or("legacy", |p| p.policy_id.as_str()).to_owned(),
        policy_version: policy.map_or(0, |p| p.version),
        policy_fingerprint: fingerprint(&policy),
        catalog_fingerprint,
        selected_sets: policy
            .map(|p| p.base_sets.iter().cloned().collect())
            .unwrap_or_default(),
        included_ids: policy
            .map(|p| p.include.iter().cloned().collect())
            .unwrap_or_default(),
        excluded_ids: policy
            .map(|p| p.exclude.iter().cloned().collect())
            .unwrap_or_default(),
        disabled_sources: policy
            .map(|p| p.disabled_sources.iter().copied().collect())
            .unwrap_or_default(),
        items,
    })
}

#[cfg(test)]
fn resolve_context(
    tools: &[ToolDefinition],
    identities: &BTreeMap<String, ContextIdentity>,
    policy: Option<&ContextPolicy>,
    decision_id: String,
) -> Result<ContextRunSnapshot, RuntimeError> {
    resolve_catalog(tools, identities, &[], policy, decision_id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{read_file_definition, write_file_definition};

    fn policy() -> ContextPolicy {
        ContextPolicy {
            policy_id: "readonly".into(),
            version: 1,
            sets: BTreeMap::from([(
                "files".into(),
                BTreeSet::from(["tool:read_file".into(), "tool:write_file".into()]),
            )]),
            base_sets: BTreeSet::from(["files".into()]),
            include: BTreeSet::from(["tool:write_file".into()]),
            exclude: BTreeSet::from(["tool:write_file".into()]),
            disabled_sources: BTreeSet::new(),
            default_mode: ContextMode::Unfolded,
            unfolded: BTreeSet::new(),
        }
    }

    #[test]
    fn exclusions_override_sets_and_inclusions_and_source_disables_win() {
        let tools = vec![read_file_definition(), write_file_definition()];
        let mut p = policy();
        let snapshot = resolve_context(&tools, &BTreeMap::new(), Some(&p), "d".into()).unwrap();
        assert!(snapshot.items[0].enabled);
        assert!(!snapshot.items[1].enabled);
        assert_eq!(
            snapshot.items[1].reason,
            ContextDecisionReason::ExplicitlyExcluded
        );
        p.disabled_sources.insert(ContextSourceKind::Tool);
        assert!(
            resolve_context(&tools, &BTreeMap::new(), Some(&p), "d".into())
                .unwrap()
                .items
                .iter()
                .all(|item| !item.enabled)
        );
    }

    #[test]
    fn snapshots_are_deterministic_and_round_trip() {
        let tools = vec![read_file_definition(), write_file_definition()];
        let a = resolve_context(&tools, &BTreeMap::new(), Some(&policy()), "d".into()).unwrap();
        let b = resolve_context(
            &tools.into_iter().rev().collect::<Vec<_>>(),
            &BTreeMap::new(),
            Some(&policy()),
            "d".into(),
        )
        .unwrap();
        assert_eq!(a, b);
        assert_eq!(
            a,
            serde_json::from_str(&serde_json::to_string(&a).unwrap()).unwrap()
        );
    }

    #[test]
    fn rejects_unknown_references_and_duplicate_names() {
        assert!(
            resolve_context(
                &[read_file_definition()],
                &BTreeMap::new(),
                Some(&policy()),
                "d".into()
            )
            .is_err()
        );
        assert!(
            resolve_context(
                &[read_file_definition(), read_file_definition()],
                &BTreeMap::new(),
                None,
                "d".into()
            )
            .is_err()
        );
    }

    #[test]
    fn mcp_identity_is_unambiguous_and_independent_of_exposed_name() {
        let mut tool = read_file_definition();
        let a = mcp_context_identity("a-b", &tool, "read");
        tool.name = "renamed".into();
        assert_eq!(a.id, mcp_context_identity("a-b", &tool, "read").id);
        assert_ne!(a.id, mcp_context_identity("a_b", &tool, "read").id);
    }
}
