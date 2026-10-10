//! Run-local exposure state; unfolding never changes policy authorization.

use std::collections::BTreeMap;

use arabica_model::{ToolCallItem, ToolDefinition};
use arabica_protocol::{ContextItemType, ContextMode, ContextRunSnapshot};
use serde::Deserialize;

use crate::SkillDefinition;

pub const CONTEXT_UNFOLD: &str = "context_unfold";
pub const CONTEXT_READ: &str = "context_read";

pub(crate) fn control_definition(name: &str) -> ToolDefinition {
    let (description, schema) = if name == CONTEXT_UNFOLD {
        (
            "Unfold an available context item by its exact name and type. Tool schemas and skill instructions become available on the next model request.",
            serde_json::json!({
                "type":"object", "properties": {"name":{"type":"string"}, "type":{"type":"string","enum":["tool","mcp_server","mcp_tool","skill"]}},
                "required":["name","type"], "additionalProperties":false
            }),
        )
    } else {
        (
            "Read a declared resource from an unfolded skill's immutable snapshot. Use its exact skill name and relative resource path.",
            serde_json::json!({
                "type":"object", "properties":{"name":{"type":"string"},"path":{"type":"string"}},
                "required":["name","path"],"additionalProperties":false
            }),
        )
    };
    ToolDefinition {
        name: name.to_owned(),
        description: description.to_owned(),
        input_schema: schema,
        strict: None,
    }
}

pub(crate) struct RunContext {
    pub snapshot: ContextRunSnapshot,
    modes: BTreeMap<String, ContextMode>,
    skills: Vec<SkillDefinition>,
}

pub(crate) struct RequestContext {
    pub tools: Vec<ToolDefinition>,
    pub instructions: Vec<String>,
    pub unfolded_ids: Vec<String>,
    pub folded_ids: Vec<String>,
}

impl RunContext {
    pub fn new(snapshot: ContextRunSnapshot, skills: Vec<SkillDefinition>) -> Self {
        let modes = snapshot
            .items
            .iter()
            .map(|item| (item.identity.id.clone(), item.initial_mode))
            .collect();
        Self {
            snapshot,
            modes,
            skills,
        }
    }

    fn unfolded(&self, id: &str) -> bool {
        self.modes.get(id) == Some(&ContextMode::Unfolded)
    }

    fn discoverable(&self, item: &arabica_protocol::ContextDecision) -> bool {
        if item.identity.item_type() != ContextItemType::McpTool || self.unfolded(&item.identity.id)
        {
            return true;
        }
        item.identity
            .server_id
            .as_deref()
            .is_some_and(|server| self.unfolded(&crate::mcp_server_context_id(server)))
    }

    pub fn prepare(
        &self,
        candidates: &[ToolDefinition],
        base_instructions: &[String],
        completion_only: bool,
    ) -> RequestContext {
        let tools: Vec<_> = if completion_only {
            vec![crate::runtime_complete_tool_definition()]
        } else {
            candidates
                .iter()
                .filter(|tool| {
                    self.snapshot.items.iter().any(|item| {
                        item.enabled
                            && item.exposed_name == tool.name
                            && matches!(
                                item.identity.item_type(),
                                ContextItemType::Tool | ContextItemType::McpTool
                            )
                            && self.unfolded(&item.identity.id)
                    })
                })
                .cloned()
                .collect()
        };
        let mut instructions = base_instructions.to_vec();
        let mut folded_ids = Vec::new();
        let mut unfolded_ids = Vec::new();
        let mut directory = Vec::new();
        for item in self.snapshot.items.iter().filter(|item| item.enabled) {
            let kind = item.identity.item_type();
            if !self.unfolded(&item.identity.id) {
                if !completion_only && self.discoverable(item) {
                    // A folded representation deliberately contains only these two fields.
                    if kind != ContextItemType::McpTool {
                        directory.push(serde_json::json!({"name":item.exposed_name,"type":kind}));
                    }
                    folded_ids.push(item.identity.id.clone());
                }
                continue;
            }
            match kind {
                ContextItemType::Skill => {
                    if let Some(skill) = self
                        .skills
                        .iter()
                        .find(|skill| skill.identity.id == item.identity.id)
                    {
                        let resources: Vec<_> = skill.resources.keys().collect();
                        instructions.push(format!(
                            "Skill {}:\n{}\nDeclared resources (read with context_read): {}",
                            serde_json::to_string(&item.exposed_name).expect("skill name"),
                            skill.instructions,
                            serde_json::to_string(&resources).expect("resources")
                        ));
                        unfolded_ids.push(item.identity.id.clone());
                    }
                }
                ContextItemType::McpServer => {
                    if !completion_only {
                        let children: Vec<_> = self.snapshot.items.iter().filter(|child| child.enabled && child.identity.item_type() == ContextItemType::McpTool && child.identity.server_id == item.identity.server_id)
                            .map(|child| serde_json::json!({"name":child.exposed_name,"type":ContextItemType::McpTool})).collect();
                        instructions.push(format!("MCP server {} tool directory (folded tools require context_unfold before calling):\n{}", serde_json::to_string(&item.exposed_name).expect("server name"), serde_json::to_string(&children).expect("MCP directory")));
                        unfolded_ids.push(item.identity.id.clone());
                    }
                }
                _ => {
                    if tools.iter().any(|tool| tool.name == item.exposed_name) {
                        unfolded_ids.push(item.identity.id.clone());
                    }
                }
            }
        }
        if !directory.is_empty() {
            instructions.push(format!("Available folded context (names and types only):\n{}\nUse context_unfold to expose an item's normal content. A folded tool cannot be called directly. Expanding an MCP server reveals its tool directory; expand each tool separately.", serde_json::to_string(&directory).expect("context directory")));
        }
        RequestContext {
            tools,
            instructions,
            unfolded_ids,
            folded_ids,
        }
    }

    pub fn requested_identity(&self, call: &ToolCallItem) -> Option<String> {
        let name = call.arguments.get("name")?.as_str()?;
        let kind: ContextItemType =
            serde_json::from_value(call.arguments.get("type")?.clone()).ok()?;
        self.snapshot
            .items
            .iter()
            .find(|item| item.exposed_name == name && item.identity.item_type() == kind)
            .map(|item| item.identity.id.clone())
    }

    pub fn unfold(&mut self, call: &ToolCallItem) -> Result<Option<String>, &'static str> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Arguments {
            name: String,
            #[serde(rename = "type")]
            kind: ContextItemType,
        }
        let args: Arguments = serde_json::from_value(call.arguments.clone())
            .map_err(|_| "context_invalid_arguments")?;
        let item = self
            .snapshot
            .items
            .iter()
            .find(|item| item.exposed_name == args.name && item.identity.item_type() == args.kind)
            .ok_or("context_unavailable")?;
        if !item.enabled || !self.discoverable(item) {
            return Err("context_unavailable");
        }
        let id = item.identity.id.clone();
        if self.unfolded(&id) {
            return Ok(None);
        }
        self.modes.insert(id.clone(), ContextMode::Unfolded);
        Ok(Some(id))
    }

    pub fn read(&self, call: &ToolCallItem) -> Result<String, &'static str> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Arguments {
            name: String,
            path: String,
        }
        let args: Arguments = serde_json::from_value(call.arguments.clone())
            .map_err(|_| "context_invalid_arguments")?;
        let skill = self
            .skills
            .iter()
            .find(|skill| skill.identity.display_name == args.name)
            .ok_or("context_unavailable")?;
        if !self.unfolded(&skill.identity.id)
            || !self
                .snapshot
                .items
                .iter()
                .any(|item| item.identity.id == skill.identity.id && item.enabled)
        {
            return Err("context_unavailable");
        }
        skill
            .resources
            .get(&args.path)
            .cloned()
            .ok_or("context_resource_unavailable")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ContextPolicy, context, mcp_context_identity, read_file_definition};
    use arabica_protocol::{ContextIdentity, ContextSourceKind};
    use std::collections::BTreeSet;

    fn skill() -> SkillDefinition {
        SkillDefinition {
            identity: ContextIdentity {
                id: "skill:review".into(),
                kind: ContextSourceKind::Skill,
                version: Some("v1".into()),
                display_name: "review".into(),
                server_id: None,
                tool_id: None,
            },
            instructions: "PRIVATE_SKILL_INSTRUCTIONS".into(),
            requirements: BTreeSet::new(),
            resources: BTreeMap::from([("checklist.md".into(), "PRIVATE_RESOURCE".into())]),
        }
    }

    fn call(name: &str, arguments: serde_json::Value) -> ToolCallItem {
        ToolCallItem {
            id: None,
            call_id: "c".into(),
            name: name.into(),
            arguments,
            provider_state: None,
        }
    }

    fn policy(include: BTreeSet<String>) -> ContextPolicy {
        ContextPolicy {
            policy_id: "test".into(),
            version: 1,
            sets: BTreeMap::new(),
            base_sets: BTreeSet::new(),
            include,
            exclude: BTreeSet::new(),
            disabled_sources: BTreeSet::new(),
            default_mode: ContextMode::Folded,
            unfolded: BTreeSet::new(),
        }
    }

    #[test]
    fn folded_content_is_name_and_type_only_and_unfold_is_idempotent() {
        let skill = skill();
        let tools = vec![
            read_file_definition(),
            control_definition(CONTEXT_UNFOLD),
            control_definition(CONTEXT_READ),
        ];
        let policy = policy(BTreeSet::from([
            "tool:read_file".into(),
            skill.identity.id.clone(),
        ]));
        let snapshot = context::resolve_catalog(
            &tools,
            &BTreeMap::new(),
            std::slice::from_ref(&skill),
            Some(&policy),
            "d".into(),
        )
        .unwrap();
        let mut state = RunContext::new(snapshot, vec![skill]);
        let folded = state.prepare(&tools, &[], false);
        assert!(!folded.tools.iter().any(|tool| tool.name == "read_file"));
        let prompt = folded.instructions.join("\n");
        assert!(prompt.contains("review"));
        assert!(!prompt.contains("PRIVATE_SKILL_INSTRUCTIONS"));
        assert!(!prompt.contains("checklist.md"));
        assert!(!prompt.contains("skill:review"));
        let read = call(
            CONTEXT_READ,
            serde_json::json!({"name":"review","path":"checklist.md"}),
        );
        assert!(state.read(&read).is_err());
        let unfold = call(
            CONTEXT_UNFOLD,
            serde_json::json!({"name":"review","type":"skill"}),
        );
        assert_eq!(state.unfold(&unfold).unwrap(), Some("skill:review".into()));
        assert_eq!(state.unfold(&unfold).unwrap(), None);
        assert!(
            state
                .prepare(&tools, &[], false)
                .instructions
                .join("\n")
                .contains("PRIVATE_SKILL_INSTRUCTIONS")
        );
        assert_eq!(state.read(&read).unwrap(), "PRIVATE_RESOURCE");
        assert!(
            state
                .read(&call(
                    CONTEXT_READ,
                    serde_json::json!({"name":"review","path":"../private"})
                ))
                .is_err()
        );
    }

    #[test]
    fn mcp_server_unfolds_a_directory_without_implicitly_unfolding_tools() {
        let mut tool = read_file_definition();
        tool.name = "mcp__docs__search".into();
        let identity = mcp_context_identity("configured:docs", &tool, "search");
        let identities = BTreeMap::from([(tool.name.clone(), identity.clone())]);
        let tools = vec![tool, control_definition(CONTEXT_UNFOLD)];
        let policy = policy(BTreeSet::from([identity.id.clone()]));
        let snapshot =
            context::resolve_catalog(&tools, &identities, &[], Some(&policy), "d".into()).unwrap();
        let mut state = RunContext::new(snapshot, Vec::new());
        let first = state.prepare(&tools, &[], false);
        assert!(first.instructions.join("\n").contains("configured:docs"));
        assert!(!first.instructions.join("\n").contains("mcp__docs__search"));
        let child = call(
            CONTEXT_UNFOLD,
            serde_json::json!({"name":"mcp__docs__search","type":"mcp_tool"}),
        );
        assert!(state.unfold(&child).is_err());
        state
            .unfold(&call(
                CONTEXT_UNFOLD,
                serde_json::json!({"name":"configured:docs","type":"mcp_server"}),
            ))
            .unwrap();
        let directory = state.prepare(&tools, &[], false);
        assert!(
            directory
                .instructions
                .join("\n")
                .contains("mcp__docs__search")
        );
        assert!(
            !directory
                .tools
                .iter()
                .any(|tool| tool.name == "mcp__docs__search")
        );
        state.unfold(&child).unwrap();
        assert!(
            state
                .prepare(&tools, &[], false)
                .tools
                .iter()
                .any(|tool| tool.name == "mcp__docs__search")
        );
    }

    #[test]
    fn an_unfolded_empty_server_has_a_visible_directory() {
        let server_id = "configured:empty";
        let id = crate::mcp_server_context_id(server_id);
        let identities = BTreeMap::from([(
            id.clone(),
            ContextIdentity {
                id: id.clone(),
                kind: ContextSourceKind::Mcp,
                version: None,
                display_name: server_id.into(),
                server_id: Some(server_id.into()),
                tool_id: None,
            },
        )]);
        let tools = vec![control_definition(CONTEXT_UNFOLD)];
        let policy = policy(BTreeSet::from([id.clone()]));
        let snapshot =
            context::resolve_catalog(&tools, &identities, &[], Some(&policy), "d".into()).unwrap();
        let mut state = RunContext::new(snapshot, Vec::new());
        state
            .unfold(&call(
                CONTEXT_UNFOLD,
                serde_json::json!({"name":server_id,"type":"mcp_server"}),
            ))
            .unwrap();
        let prepared = state.prepare(&tools, &[], false);
        assert!(
            prepared
                .instructions
                .join("\n")
                .contains("configured:empty")
        );
        assert!(prepared.unfolded_ids.contains(&id));
    }

    #[test]
    fn denied_items_cannot_unfold_and_requirements_do_not_grant_permissions() {
        let tools = vec![read_file_definition(), control_definition(CONTEXT_UNFOLD)];
        let mut policy = policy(BTreeSet::from(["tool:read_file".into()]));
        policy.exclude.insert("tool:read_file".into());
        let snapshot =
            context::resolve_catalog(&tools, &BTreeMap::new(), &[], Some(&policy), "d".into())
                .unwrap();
        let mut state = RunContext::new(snapshot, Vec::new());
        assert!(
            state
                .unfold(&call(
                    CONTEXT_UNFOLD,
                    serde_json::json!({"name":"read_file","type":"tool"})
                ))
                .is_err()
        );
        let mut skill = skill();
        skill.requirements.insert("tool:read_file".into());
        policy.include.insert(skill.identity.id.clone());
        assert!(
            context::resolve_catalog(
                &tools,
                &BTreeMap::new(),
                &[skill],
                Some(&policy),
                "d".into()
            )
            .is_err()
        );
    }
}
