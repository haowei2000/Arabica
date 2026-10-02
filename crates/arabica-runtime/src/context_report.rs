//! Reproducible observational metrics derived only from canonical history.

use std::collections::{BTreeMap, BTreeSet};

use arabica_protocol::{Event, EventEnvelope, RunId, ToolPermissionOutcome};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct ContextItemMetrics {
    pub eligible_runs: usize,
    pub enabled_runs: usize,
    pub exposed_runs: usize,
    pub exposed_model_calls: usize,
    pub folded_runs: usize,
    pub folded_model_calls: usize,
    pub unfold_count: usize,
    pub activated_runs: usize,
    pub started_calls: usize,
    pub successful_calls: usize,
    pub failed_calls: usize,
    pub unknown_call_outcomes: usize,
    pub rejected_calls: usize,
    pub permission_denied_calls: usize,
    pub reused_calls: usize,
    /// Association after exposure, never an estimate of causal contribution.
    pub completed_exposed_runs: usize,
    pub failed_exposed_runs: usize,
    pub cancelled_exposed_runs: usize,
    pub unknown_exposed_run_outcomes: usize,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct ContextReport {
    /// IDs absent from the report were not observed, not necessarily unused.
    pub items: BTreeMap<String, ContextItemMetrics>,
    pub runs_without_snapshot: usize,
    pub unknown_context_rejections: usize,
}

pub fn context_report(history: &[EventEnvelope]) -> ContextReport {
    let mut runs: BTreeMap<RunId, Vec<&Event>> = BTreeMap::new();
    for envelope in history {
        if let Some(run_id) = &envelope.run_id {
            runs.entry(run_id.clone())
                .or_default()
                .push(&envelope.event);
        }
    }
    let mut report = ContextReport::default();
    for events in runs.into_values() {
        report.unknown_context_rejections += events
            .iter()
            .filter(|event| {
                matches!(
                    event,
                    Event::ContextCallRejected {
                        context_id: None,
                        ..
                    }
                )
            })
            .count();
        let Some(snapshot) = events.iter().find_map(|event| match event {
            Event::ContextRunResolved { snapshot } => Some(snapshot),
            _ => None,
        }) else {
            report.runs_without_snapshot += 1;
            continue;
        };
        for item in &snapshot.items {
            // Version and policy-specific series must not be silently pooled.
            let key = serde_json::to_string(&(
                &item.identity.id,
                &item.identity.version,
                &snapshot.policy_fingerprint,
            ))
            .expect("context report key");
            let metrics = report.items.entry(key).or_default();
            metrics.eligible_runs += 1;
            metrics.enabled_runs += usize::from(item.enabled);
            let mut started = BTreeSet::new();
            let mut calls = BTreeSet::new();
            let mut exposed = false;
            let mut folded = false;
            for event in &events {
                match event {
                    Event::ContextRequestExposed { context_ids, .. }
                        if context_ids.contains(&item.identity.id) =>
                    {
                        exposed = true;
                        metrics.exposed_model_calls += 1;
                    }
                    Event::ContextRequestExposed { folded_ids, .. }
                        if folded_ids.contains(&item.identity.id) =>
                    {
                        folded = true;
                        metrics.folded_model_calls += 1;
                    }
                    Event::ContextItemUnfolded { context_id, .. }
                        if context_id == &item.identity.id =>
                    {
                        metrics.unfold_count += 1;
                    }
                    Event::ToolCallRequested { call_id, name, .. }
                        if name == &item.exposed_name
                            && matches!(
                                item.identity.item_type(),
                                arabica_protocol::ContextItemType::Tool
                                    | arabica_protocol::ContextItemType::McpTool
                            ) =>
                    {
                        calls.insert(call_id.as_str());
                    }
                    Event::ContextCallStarted {
                        call_id,
                        context_id,
                        ..
                    } if context_id == &item.identity.id => {
                        started.insert(call_id.as_str());
                    }
                    Event::ContextCallRejected {
                        context_id: Some(id),
                        ..
                    } if id == &item.identity.id => {
                        metrics.rejected_calls += 1;
                    }
                    _ => {}
                }
            }
            metrics.exposed_runs += usize::from(exposed);
            metrics.folded_runs += usize::from(folded);
            metrics.activated_runs += usize::from(!started.is_empty());
            metrics.started_calls += started.len();
            for call_id in started {
                match events.iter().find_map(|event| match event {
                    Event::ToolCallCompleted {
                        call_id: id,
                        is_error,
                        ..
                    } if id == call_id => Some(*is_error),
                    _ => None,
                }) {
                    Some(false) => metrics.successful_calls += 1,
                    Some(true) => metrics.failed_calls += 1,
                    None => metrics.unknown_call_outcomes += 1,
                }
            }
            for event in &events {
                match event {
                    Event::ToolCallPermissionResolved {
                        call_id,
                        outcome: ToolPermissionOutcome::Denied,
                        ..
                    } if calls.contains(call_id.as_str()) => metrics.permission_denied_calls += 1,
                    Event::ToolCallReused { call_id, .. } if calls.contains(call_id.as_str()) => {
                        metrics.reused_calls += 1
                    }
                    _ => {}
                }
            }
            if exposed {
                match events.iter().rev().find(|event| {
                    matches!(
                        event,
                        Event::RunCompleted { .. } | Event::RunFailed { .. } | Event::RunCancelled
                    )
                }) {
                    Some(Event::RunCompleted { .. }) => metrics.completed_exposed_runs += 1,
                    Some(Event::RunFailed { .. }) => metrics.failed_exposed_runs += 1,
                    Some(Event::RunCancelled) => metrics.cancelled_exposed_runs += 1,
                    _ => metrics.unknown_exposed_run_outcomes += 1,
                }
            }
        }
    }
    report
}
