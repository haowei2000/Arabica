//! Read-only diagnostics for the terminal context view.

use std::error::Error;
use std::fmt::Write;

use arabica_adapters::FileSessionStore;
use arabica_model::{MemoryLoadState, RuntimeItem, RuntimeRequest, RuntimeRole, RuntimeUsage};
use arabica_protocol::{Event, EventEnvelope};
use arabica_runtime::{
    LongMemoryStore, RuntimeCompactionStrategy, ShortMemoryPolicy, ShortMemoryProjector,
};

use crate::host::workspace_id_for;
use crate::interactive::InteractiveSession;

pub(crate) fn report(session: &InteractiveSession) -> Result<String, Box<dyn Error>> {
    let workspace = workspace_id_for(&session.runner_root);
    let stored =
        FileSessionStore::read_session(&session.arabica_home, &workspace, &session.session_id)?;
    let runtime = session.manager.runtime();
    let archives = runtime
        .long_memory(&workspace)
        .map(LongMemoryStore::archive_count)
        .transpose()?;
    let mut report = format_report(
        &stored.events,
        runtime.short_memory_policy(),
        runtime.compaction_strategy(),
        archives,
    );
    report.push_str(if runtime.async_file_backed_gc_completed() {
        "Background archive preparation: finished; checked at next model step\n"
    } else if runtime.async_file_backed_gc_pending() {
        "Background archive preparation: running\n"
    } else {
        "Background archive preparation: idle\n"
    });
    if let Some(error) = runtime.async_file_backed_gc_error() {
        report.push_str(&format!(
            "Last observed archive preparation error: {error}\n"
        ));
    }
    let Some(worker) = crate::evaluation::worker(&session.arabica_home) else {
        report.push_str("Background evaluation unavailable; Agent execution is unaffected.\n");
        return Ok(report);
    };
    if let Some(event) = stored.events.iter().rev().find(|event| {
        matches!(
            event.event,
            Event::RunCompleted { .. } | Event::RunFailed { .. } | Event::RunCancelled
        )
    }) {
        worker.submit(arabica_adapters::EvaluationCheckpoint {
            workspace_id: workspace.clone(),
            session_id: session.session_id.clone(),
            sequence: event.sequence,
        });
    }
    let status = worker.status();
    let _ = writeln!(
        report,
        "\nBACKGROUND EVALUATION · completed={} failed={} dropped={}",
        status.completed, status.failed, status.dropped
    );
    let Some(saved) = worker.latest(&workspace, &session.session_id) else {
        report.push_str("No cached evaluation yet; evaluation is queued asynchronously.\n");
        return Ok(report);
    };
    let _ = writeln!(
        report,
        "Cached evaluation through event {} (may be stale).",
        saved.checkpoint.sequence
    );
    let context = saved.context;
    let models = saved.model;
    let _ = writeln!(
        report,
        "\nCONTEXT EVALUATION · {} v{}",
        context.plugin.id, context.plugin.version
    );
    for diagnostic in &context.data.diagnostics {
        let _ = writeln!(report, "Diagnostic: {diagnostic:?}");
    }
    for group in context.data.groups {
        let _ = writeln!(
            report,
            "Group: {} · runs: {}",
            group.group_id, group.sample_runs
        );
        let metrics = group.report;
        let _ = writeln!(
            report,
            "Runs without context telemetry: {}",
            metrics.runs_without_snapshot
        );
        let _ = writeln!(
            report,
            "Unknown context call rejections: {}",
            metrics.unknown_context_rejections
        );
        for (identity, counts) in metrics.items {
            let _ = writeln!(
                report,
                "{identity}: eligible={} enabled={} folded={} unfolded={} unfold_count={} activated={} calls={} success={} failure={} unknown={} rejected={} permission_denied={} reused={}",
                counts.eligible_runs,
                counts.enabled_runs,
                counts.folded_runs,
                counts.exposed_runs,
                counts.unfold_count,
                counts.activated_runs,
                counts.started_calls,
                counts.successful_calls,
                counts.failed_calls,
                counts.unknown_call_outcomes,
                counts.rejected_calls,
                counts.permission_denied_calls,
                counts.reused_calls
            );
        }
    }
    let _ = writeln!(
        report,
        "\nMODEL EVALUATION · {} v{}",
        models.plugin.id, models.plugin.version
    );
    for diagnostic in &models.data.diagnostics {
        let _ = writeln!(report, "Diagnostic: {diagnostic:?}");
    }
    for group in models.data.groups {
        let _ = writeln!(
            report,
            "Group: {} · runs: {} · policy: {} · registry: {}",
            group.group_id,
            group.sample_runs,
            group.routing_policy_fingerprint,
            group.model_registry_fingerprint
        );
        for (alias, counts) in group.report.models {
            let _ = writeln!(
                report,
                "{alias}: selected={} observed={} provider_failures={} unknown={} elapsed_ms={} input_tokens={} output_tokens={}",
                counts.selected_calls,
                counts.observed_calls,
                counts.provider_failures,
                counts.calls_with_unknown_outcome,
                counts.elapsed_ms_total,
                counts.input_tokens,
                counts.output_tokens
            );
        }
    }
    report.push_str("Run outcomes after exposure are associations, not causal contribution.\n");
    Ok(report)
}

fn format_report(
    events: &[EventEnvelope],
    policy: &ShortMemoryPolicy,
    strategy: RuntimeCompactionStrategy,
    archives: Option<usize>,
) -> String {
    let mut out = String::from("LAST MODEL REQUEST · ACTUAL\n");
    if let Some((index, envelope, step, request)) =
        events
            .iter()
            .enumerate()
            .rev()
            .find_map(|(index, envelope)| match &envelope.event {
                Event::ModelRequestPrepared {
                    model_step,
                    request,
                } => Some((index, envelope, model_step, request)),
                _ => None,
            })
    {
        let _ = writeln!(out, "Model: {} · step: {}", request.model, step);
        let _ = writeln!(
            out,
            "Request items: {} · tools: {}",
            request.items.len(),
            request.tools.len()
        );
        let (system, developer, user, assistant, calls, results, reasoning) = item_counts(request);
        let _ = writeln!(
            out,
            "Messages: system {system}, developer {developer}, user {user}, assistant {assistant}"
        );
        let _ = writeln!(
            out,
            "Other items: tool calls {calls}, tool results {results}, reasoning {reasoning}"
        );
        let usage = events[index + 1..].iter().find_map(|candidate| {
            if candidate.run_id == envelope.run_id {
                match &candidate.event {
                    Event::ModelResponseCompleted {
                        model_step, usage, ..
                    } if model_step == step => Some(usage),
                    _ => None,
                }
            } else {
                None
            }
        });
        match usage {
            Some(usage) if usage.input_tokens > 0 || usage.output_tokens > 0 => {
                write_usage(&mut out, usage);
            }
            _ => out.push_str("Tokens: provider did not report usage for this request\n"),
        }
    } else {
        out.push_str("No model request recorded yet.\n");
    }

    out.push_str("\nNEXT TURN · POLICY PREVIEW\n");
    out.push_str(
        "Estimate from saved events; excludes your next message and FileBackedGC admission.\n",
    );
    let mut effective = policy.clone();
    if strategy == RuntimeCompactionStrategy::FileBackedGc {
        effective.batch_compaction_enabled = false;
    }
    let projection = ShortMemoryProjector::materialize(events, None, &effective);
    let counts = projection
        .batches
        .iter()
        .fold((0, 0, 0), |mut counts, batch| {
            match batch.load_state {
                MemoryLoadState::LoadAll => counts.0 += 1,
                MemoryLoadState::LoadKey => counts.1 += 1,
                MemoryLoadState::NoLoad => counts.2 += 1,
            }
            counts
        });
    let visible = projection
        .visibility
        .iter()
        .filter(|decision| decision.visible)
        .count();
    let _ = writeln!(
        out,
        "Recorded events: {} · policy visible: {visible}",
        events.len()
    );
    let _ = writeln!(
        out,
        "Batches: {} full · {} key · {} omitted",
        counts.0, counts.1, counts.2
    );
    for batch in projection.batches.iter().rev().take(20).rev() {
        let state = match batch.load_state {
            MemoryLoadState::LoadAll => "full",
            MemoryLoadState::LoadKey => "key",
            MemoryLoadState::NoLoad => "omitted",
        };
        let _ = writeln!(
            out,
            "  {state:7} · {:?} · {} events · {}",
            batch.context_kind, batch.event_count, batch.context_key
        );
    }
    if projection.batches.len() > 20 {
        let _ = writeln!(
            out,
            "  … {} older batches hidden",
            projection.batches.len() - 20
        );
    }
    out.push_str("\nARCHIVE · WORKSPACE WIDE\n");
    match archives {
        Some(count) => {
            let _ = writeln!(out, "Stored exact archives: {count}");
        }
        None => out.push_str("Archive store unavailable\n"),
    }
    out.push_str("Use memory_search and memory_read to recover archived content.\n");
    out
}

fn item_counts(request: &RuntimeRequest) -> (usize, usize, usize, usize, usize, usize, usize) {
    let mut counts = (0, 0, 0, 0, 0, 0, 0);
    for item in &request.items {
        match item {
            RuntimeItem::Message(message) => match message.role {
                RuntimeRole::System => counts.0 += 1,
                RuntimeRole::Developer => counts.1 += 1,
                RuntimeRole::User => counts.2 += 1,
                RuntimeRole::Assistant => counts.3 += 1,
            },
            RuntimeItem::ToolCall(_) => counts.4 += 1,
            RuntimeItem::ToolResult(_) => counts.5 += 1,
            RuntimeItem::Reasoning(_) => counts.6 += 1,
        }
    }
    counts
}

fn write_usage(out: &mut String, usage: &RuntimeUsage) {
    let _ = writeln!(
        out,
        "Tokens reported: input {} · output {}",
        usage.input_tokens, usage.output_tokens
    );
    if usage.cached_input_tokens > 0 || usage.cache_creation_input_tokens > 0 {
        let _ = writeln!(
            out,
            "Cache: read {} · written {}",
            usage.cached_input_tokens, usage.cache_creation_input_tokens
        );
    }
    if usage.reasoning_output_tokens > 0 {
        let _ = writeln!(
            out,
            "Reasoning tokens (included in output): {}",
            usage.reasoning_output_tokens
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arabica_model::{ContentBlock, MessageItem, RuntimeGenerationConfig, ToolChoice};
    use arabica_protocol::{CommandId, EventId, EventMetadata, RunId, SessionId, WorkspaceId};

    fn event(sequence: u64, run: &str, event: Event) -> EventEnvelope {
        EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("event-{sequence}")),
                command_id: CommandId::new("command"),
                workspace_id: WorkspaceId::new("workspace"),
                session_id: SessionId::new("session"),
                run_id: Some(RunId::new(run)),
                sequence,
                occurred_at_ms: sequence,
            },
            event,
        )
    }

    #[test]
    fn empty_report_does_not_invent_usage() {
        let report = format_report(
            &[],
            &ShortMemoryPolicy::default(),
            RuntimeCompactionStrategy::FileBackedGc,
            Some(0),
        );
        assert!(report.contains("No model request recorded yet."));
        assert!(report.contains("0 full · 0 key · 0 omitted"));
        assert!(report.contains("Stored exact archives: 0"));
    }

    #[test]
    fn report_matches_usage_to_last_request_run_and_step() {
        let request = RuntimeRequest {
            model: "test-model".into(),
            items: vec![RuntimeItem::Message(MessageItem {
                id: None,
                role: RuntimeRole::User,
                content: vec![ContentBlock::text("hello")],
                provider_state: None,
            })],
            tools: Vec::new(),
            tool_choice: ToolChoice::Auto,
            generation: RuntimeGenerationConfig::default(),
        };
        let events = vec![
            event(
                1,
                "old",
                Event::ModelResponseCompleted {
                    model_step: 0,
                    finish_reason: None,
                    usage: RuntimeUsage {
                        input_tokens: 999,
                        ..RuntimeUsage::default()
                    },
                    provider_state: None,
                },
            ),
            event(
                2,
                "new",
                Event::ModelRequestPrepared {
                    model_step: 1,
                    request,
                },
            ),
            event(
                3,
                "other",
                Event::ModelResponseCompleted {
                    model_step: 1,
                    finish_reason: None,
                    usage: RuntimeUsage {
                        input_tokens: 888,
                        ..RuntimeUsage::default()
                    },
                    provider_state: None,
                },
            ),
            event(
                4,
                "new",
                Event::ModelResponseCompleted {
                    model_step: 1,
                    finish_reason: None,
                    usage: RuntimeUsage {
                        input_tokens: 123,
                        output_tokens: 45,
                        cached_input_tokens: 10,
                        ..RuntimeUsage::default()
                    },
                    provider_state: None,
                },
            ),
        ];
        let report = format_report(
            &events,
            &ShortMemoryPolicy::default(),
            RuntimeCompactionStrategy::FileBackedGc,
            Some(2),
        );
        assert!(report.contains("Model: test-model · step: 1"));
        assert!(report.contains("user 1"));
        assert!(report.contains("input 123 · output 45"));
        assert!(report.contains("Cache: read 10"));
        assert!(!report.contains("999"));
        assert!(!report.contains("888"));
    }
}
