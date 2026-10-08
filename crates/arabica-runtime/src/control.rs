//! Per-run control supplied by the host: cooperative cancellation and the tool
//! permission gate.
//!
//! A host cannot cancel a run by sending `run.cancel` while the run executes:
//! Session Management holds one borrow for the whole `message.send`, so the
//! Command would only be processed after the run ended. Control therefore
//! travels with the run as a shared handle the host can signal at any time.

use std::collections::BTreeMap;
use std::sync::Arc;

use arabica_model::ToolCallItem;
use arabica_protocol::{RunId, ToolPermissionOutcome, ToolPermissionScope, ToolPermissionSource};
use tokio::sync::{mpsc, oneshot, watch};

/// A cancellation signal for one run, shared between the host and Runtime.
///
/// Cloning shares the signal. Cancelling is idempotent and never blocks, so a
/// host may call it from a notification handler that must not wait on the run.
#[derive(Clone, Debug)]
pub struct RunCancellation(Arc<watch::Sender<bool>>);

impl RunCancellation {
    pub fn new() -> Self {
        let (sender, _) = watch::channel(false);
        Self(Arc::new(sender))
    }

    pub fn cancel(&self) {
        self.0.send_replace(true);
    }

    pub fn is_cancelled(&self) -> bool {
        *self.0.borrow()
    }

    /// Resolve once the run is cancelled; immediately if it already is.
    pub async fn cancelled(&self) {
        let mut receiver = self.0.subscribe();
        // The sender lives as long as any clone of this handle, including
        // `self`, so the channel cannot close while this future is polled.
        let _ = receiver.wait_for(|cancelled| *cancelled).await;
    }
}

impl Default for RunCancellation {
    fn default() -> Self {
        Self::new()
    }
}

/// What the gate does with a call to one tool.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum ToolPermissionRule {
    /// Run without asking and without recording a decision.
    Allow,
    /// Ask the host's approver first. The default, because a gate that is
    /// configured but silent should fail safe.
    #[default]
    Ask,
    /// Refuse without asking.
    Deny,
}

/// Which rule applies to each tool, keyed by tool name.
///
/// Rules are keyed by name, never by the runner's interaction classifier:
/// that classifier is a memory-retention heuristic, and `cat notes | sh`
/// classifies as inspection while executing arbitrary code.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ToolPermissionPolicy {
    pub default: ToolPermissionRule,
    pub by_tool: BTreeMap<String, ToolPermissionRule>,
    /// First matching argument rule takes precedence over the tool and default rules.
    pub argument_rules: Vec<ToolArgumentPermissionRule>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolArgumentPermissionRule {
    pub tool: String,
    /// Dot-separated object path, or `*` to match the serialized arguments.
    pub parameter: String,
    pub pattern: String,
    pub rule: ToolPermissionRule,
}

impl ToolPermissionPolicy {
    pub fn rule_for(&self, tool: &str, arguments: &serde_json::Value) -> ToolPermissionRule {
        for rule in &self.argument_rules {
            if rule.tool != tool {
                continue;
            }
            let Some(value) = argument_value(arguments, &rule.parameter) else {
                continue;
            };
            if regex::Regex::new(&rule.pattern).is_ok_and(|pattern| pattern.is_match(&value)) {
                return rule.rule;
            }
        }
        self.by_tool.get(tool).copied().unwrap_or(self.default)
    }
}

fn argument_value(arguments: &serde_json::Value, parameter: &str) -> Option<String> {
    let value = if parameter == "*" {
        arguments
    } else {
        parameter.split('.').try_fold(arguments, |value, segment| {
            value
                .as_object()
                .and_then(|object| object.get(segment))
                .or_else(|| {
                    segment
                        .parse::<usize>()
                        .ok()
                        .and_then(|index| value.get(index))
                })
        })?
    };
    match value {
        serde_json::Value::String(value) => Some(value.clone()),
        _ => Some(value.to_string()),
    }
}

/// A decision about one call.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PermissionDecision {
    pub outcome: ToolPermissionOutcome,
    pub scope: ToolPermissionScope,
    pub source: ToolPermissionSource,
}

impl PermissionDecision {
    /// A person allowed this call only.
    pub const fn allow_once() -> Self {
        Self {
            outcome: ToolPermissionOutcome::Allowed,
            scope: ToolPermissionScope::Once,
            source: ToolPermissionSource::User,
        }
    }

    /// A person allowed this tool for the rest of the Session.
    pub const fn allow_for_session() -> Self {
        Self {
            outcome: ToolPermissionOutcome::Allowed,
            scope: ToolPermissionScope::Session,
            source: ToolPermissionSource::User,
        }
    }

    /// A person refused this call.
    pub const fn deny_once() -> Self {
        Self {
            outcome: ToolPermissionOutcome::Denied,
            scope: ToolPermissionScope::Once,
            source: ToolPermissionSource::User,
        }
    }

    /// The turn was cancelled while the decision was pending.
    pub const fn cancelled() -> Self {
        Self {
            outcome: ToolPermissionOutcome::Cancelled,
            scope: ToolPermissionScope::Once,
            source: ToolPermissionSource::User,
        }
    }

    pub(crate) const fn approver_unavailable() -> Self {
        Self {
            outcome: ToolPermissionOutcome::Denied,
            scope: ToolPermissionScope::Once,
            source: ToolPermissionSource::ApproverUnavailable,
        }
    }
}

/// One call awaiting a decision, sent to the host's approver.
///
/// The host answers on `reply`. Dropping `reply` without answering counts as an
/// unavailable approver, so the gate fails closed.
#[derive(Debug)]
pub struct PermissionRequest {
    pub run_id: RunId,
    pub call: ToolCallItem,
    pub reply: oneshot::Sender<PermissionDecision>,
}

/// The permission gate a host attaches to a run.
#[derive(Clone, Debug, Default)]
pub struct ToolPermissionGate {
    pub policy: ToolPermissionPolicy,
    /// Where `Ask` decisions go. Absent means nobody can be asked, and every
    /// `Ask` is denied as `approver_unavailable`.
    pub approver: Option<mpsc::UnboundedSender<PermissionRequest>>,
}

/// Everything a host may attach to one run.
///
/// The default attaches nothing, and a run executed with it emits exactly the
/// Events it emitted before control existed. Recorded benchmark campaigns
/// depend on that.
#[derive(Clone, Debug, Default)]
pub struct RunControl {
    pub cancellation: Option<RunCancellation>,
    /// Absent means no gate at all: every call runs, and no permission Event
    /// is recorded.
    pub permissions: Option<ToolPermissionGate>,
}

impl RunControl {
    pub fn is_cancelled(&self) -> bool {
        self.cancellation
            .as_ref()
            .is_some_and(RunCancellation::is_cancelled)
    }
}
