use structure_local_core::{
    CoreCapability, CorePrimitive, LocalSnapshot, ProductSurface, StructureCoreManifest,
    SurfaceParityReport,
};
use structure_local_runtime::ProposalReview;
use structure_local_runtime::{
    ArtifactPreview, ArtifactRecord, EventGcPreview, KnowledgeSource, KnowledgeSourcePreview,
    LocalAgentContext, LocalEvent, LocalEvidenceBundle, LocalRunCompact, LocalRunCoreTrace,
    LocalRunPlan, LocalRunReview, LocalToolTraceEntry, RunAttempt, RunEvidenceSummary, RunResult,
    RunSummary, RunTranscript, SourceRating, WorkspaceEventFeed, WorkspaceReplay, WorkspaceSummary,
    WorktreeSnapshot,
};

pub(crate) fn print_snapshot(snapshot: &LocalSnapshot) {
    println!("Structure local");
    println!("  repo:       {}", snapshot.repo_root);
    println!("  runtime:    {}", snapshot.runtime_dir);
    println!("  frontend:   {}", snapshot.frontend_dir);
    println!("  local only: {}", snapshot.local_only);
    println!("  surfaces:   {}", snapshot.product_surfaces.len());
    println!(
        "  llm api:    {}",
        if snapshot.llm_config.configured {
            "configured"
        } else {
            "missing"
        }
    );
    println!(
        "  llm env:    key={}, base_url={}, model={}",
        snapshot.llm_config.api_key.source,
        snapshot.llm_config.base_url.source,
        snapshot.llm_config.model.source
    );
    if let Some(model) = &snapshot.llm_config.model_name {
        println!("  llm model:  {model}");
    }
}

pub(crate) fn print_worktree_snapshot(worktree: &WorktreeSnapshot) {
    println!("Structure local worktree");
    println!("  available: {}", worktree.available);
    println!(
        "  status:    {}",
        if worktree.clean { "clean" } else { "dirty" }
    );
    println!(
        "  branch:    {}",
        worktree.branch.as_deref().unwrap_or("unknown")
    );
    println!("  changes:   {}", worktree.changed_files.len());
    if let Some(error) = &worktree.error {
        println!("  error:     {error}");
    }
    if !worktree.changed_files.is_empty() {
        println!();
        println!("Changed files");
        for change in &worktree.changed_files {
            println!("  {:<3} {}", change.status, change.path);
        }
    }
}

pub(crate) fn print_surfaces(surfaces: &[ProductSurface]) {
    println!("Structure surfaces");
    for surface in surfaces {
        println!();
        println!("{} ({})", surface.name, surface.id);
        println!("  entrypoint: {}", surface.entrypoint);
        println!("  audience:   {}", surface.audience);
        println!("  runtime:    {}", surface.runtime_model);
        println!("  boundary:   {}", surface.boundary);
        println!("  jobs:");
        for job in &surface.primary_jobs {
            println!("    - {job}");
        }
    }
}

pub(crate) fn print_core_manifest(manifest: &StructureCoreManifest) {
    println!("Structure core {}", manifest.schema_version);
    println!("  thesis: {}", manifest.paper_anchor.thesis);
    println!(
        "  paper sections: {}",
        manifest.paper_anchor.sections.join(", ")
    );
    println!();
    println!("Canonical flow");
    for (index, step) in manifest.canonical_flow.iter().enumerate() {
        println!("  {}. {} ({})", index + 1, step.name, step.id);
        println!("     {}", step.description);
    }
    println!();
    println!("Core primitives");
    for primitive in &manifest.primitives {
        print_core_primitive(primitive);
    }
    println!();
    print_core_capabilities(&manifest.capabilities);
    println!();
    print_surfaces(&manifest.surfaces);
}

pub(crate) fn print_core_capabilities(capabilities: &[CoreCapability]) {
    println!("Core capabilities");
    for capability in capabilities {
        println!(
            "  {} ({}) -> {}",
            capability.name, capability.id, capability.primitive_id
        );
        println!("    {}", capability.description);
        for status in &capability.surface_status {
            println!(
                "    - {} [{}]: {}",
                status.surface_id, status.status, status.entrypoint
            );
        }
    }
}

pub(crate) fn print_surface_parity_report(report: &SurfaceParityReport) {
    println!("Surface parity");
    println!("  schema:       {}", report.schema_version);
    println!("  passed:       {}", report.passed);
    println!("  surfaces:     {}", report.surface_count);
    println!("  primitives:   {}", report.primitive_count);
    println!("  capabilities: {}", report.capability_count);
    println!("  checks:");
    for check in &report.checks {
        println!(
            "    [{}] {} - {}",
            if check.passed { "ok" } else { "fail" },
            check.id,
            check.summary
        );
        for detail in &check.details {
            println!("      - {detail}");
        }
    }
}

pub(crate) fn print_run_result(result: &RunResult) {
    println!("Structure local run");
    println!("  run:       {}", result.run.run_id);
    println!("  workspace: {}", result.run.workspace_id);
    println!("  status:    {}", result.run.status);
    println!("  artifact:  {}", result.artifact_path);
    println!("  events:    {}", result.events.len());
    println!();
    println!("{}", result.final_response);
}

pub(crate) fn print_run_attempt(attempt: &RunAttempt) {
    if let Some(result) = &attempt.result {
        print_run_result(result);
        return;
    }
    println!("Structure local run");
    println!("  run:       {}", attempt.run.run_id);
    println!("  workspace: {}", attempt.run.workspace_id);
    println!("  status:    {}", attempt.run.status);
    println!("  events:    {}", attempt.events.len());
    if let Some(error) = &attempt.error {
        println!("  error:     {error}");
    }
    if !attempt.events.is_empty() {
        println!();
        println!("Event trace");
        for event in &attempt.events {
            println!(
                "  #{:<4} {:<26} {:<10} {}",
                event.sequence,
                event.kind,
                event.canonical_flow_id,
                event.run_id.as_deref().unwrap_or("workspace")
            );
        }
    }
}

pub(crate) fn print_workspaces(workspaces: &[WorkspaceSummary]) {
    if workspaces.is_empty() {
        println!("No local workspaces found.");
        return;
    }
    println!("Local workspaces");
    for workspace in workspaces {
        println!(
            "  {}  {}  {}",
            workspace.workspace_id, workspace.name, workspace.root_path
        );
    }
}

pub(crate) fn print_workspace(workspace: &WorkspaceSummary) {
    println!("Local workspace");
    println!("  id:      {}", workspace.workspace_id);
    println!("  name:    {}", workspace.name);
    println!("  root:    {}", workspace.root_path);
    println!("  created: {}", workspace.created_at_ms);
    println!("  updated: {}", workspace.updated_at_ms);
}

pub(crate) fn print_runs(runs: &[RunSummary]) {
    if runs.is_empty() {
        println!("No local agent runs found.");
        return;
    }
    println!("Local agent runs");
    for run in runs {
        println!(
            "  {}  {}  {}  {}",
            run.run_id,
            run.status,
            run.workspace_id,
            first_line(&run.prompt)
        );
    }
}

pub(crate) fn print_run_summary(run: &RunSummary) {
    println!("Local agent run");
    println!("  run:       {}", run.run_id);
    println!("  workspace: {}", run.workspace_id);
    println!("  status:    {}", run.status);
    println!("  prompt:    {}", run.prompt);
    if let Some(response) = &run.final_response {
        println!();
        println!("{response}");
    }
}

pub(crate) fn print_events(events: &[LocalEvent]) {
    if events.is_empty() {
        println!("No events found.");
        return;
    }
    println!("Run events");
    for event in events {
        println!(
            "  #{:<4} {:<26} {:<10} {:<22} {}",
            event.sequence, event.kind, event.canonical_flow_id, event.primitive_id, event.payload
        );
    }
}

pub(crate) fn print_run_evidence_summary(evidence: &RunEvidenceSummary) {
    println!("Run evidence");
    println!("  run:       {}", evidence.run.run_id);
    println!("  workspace: {}", evidence.run.workspace_id);
    println!("  status:    {}", evidence.run.status);
    println!("  events:    {}", evidence.event_count);
    println!("  tools:     {}", evidence.tool_call_count);
    println!(
        "  model:     {} req / {} resp / {} net",
        evidence.model_usage.model_request_count,
        evidence.model_usage.model_response_count,
        evidence.model_usage.network_request_count
    );
    if evidence.model_usage.total_tokens > 0 {
        println!(
            "  tokens:    {} prompt / {} completion / {} total",
            evidence.model_usage.prompt_tokens,
            evidence.model_usage.completion_tokens,
            evidence.model_usage.total_tokens
        );
    }
    println!("  instr:     {}", evidence.agent_instruction_paths.len());
    if let Some(worktree) = &evidence.worktree {
        println!(
            "  worktree:  {} / {} changes",
            if worktree.clean { "clean" } else { "dirty" },
            worktree.changed_files.len()
        );
    }
    println!("  refs:      {}", evidence.prompt_references.len());
    println!("  knowledge: {}", evidence.knowledge_sources.len());
    println!("  ratings:   {}", evidence.source_ratings.len());
    println!(
        "  event gc:  {} retained / {} filtered ({})",
        evidence.event_gc.retained_event_count,
        evidence.event_gc.filtered_event_count,
        evidence.event_gc.policy_id
    );
    println!("  response:  {} chars", evidence.final_response_chars);
    println!(
        "  core:      {} / schema {}",
        if evidence.core_trace.core_aligned {
            "aligned"
        } else {
            "drift"
        },
        evidence.core_trace.manifest_schema_version
    );
    if !evidence.artifact_paths.is_empty() {
        println!("  artifacts:");
        for artifact in &evidence.artifacts {
            println!(
                "    {}  {} bytes  {}",
                artifact.artifact_id, artifact.size_bytes, artifact.path
            );
        }
        for path in evidence.artifact_paths.iter().filter(|path| {
            !evidence
                .artifacts
                .iter()
                .any(|artifact| artifact.path == **path)
        }) {
            println!("    {path}");
        }
    }
    if !evidence.prompt_references.is_empty() {
        println!("  prompt refs:");
        for reference in &evidence.prompt_references {
            println!("    @{reference}");
        }
    }
    if !evidence.agent_instruction_paths.is_empty() {
        println!("  instructions:");
        for path in &evidence.agent_instruction_paths {
            println!("    {path}");
        }
    }
    if let Some(worktree) = &evidence.worktree {
        if !worktree.changed_files.is_empty() {
            println!("  changed files:");
            for change in worktree.changed_files.iter().take(12) {
                println!("    {} {}", change.status, change.path);
            }
        }
    }
    if !evidence.knowledge_sources.is_empty() {
        println!("  sources:");
        for source in &evidence.knowledge_sources {
            println!("    {}  {}", source.title, source.path);
        }
    }
    if !evidence.source_ratings.is_empty() {
        println!("  source ratings:");
        for rating in &evidence.source_ratings {
            println!(
                "    {}  {}/5  {}",
                rating.source_title,
                rating.rating,
                if rating.note.is_empty() {
                    "no note"
                } else {
                    rating.note.as_str()
                }
            );
        }
    }
    if !evidence.event_kinds.is_empty() {
        println!("  event kinds: {}", evidence.event_kinds.join(", "));
    }
    if !evidence.canonical_flow_ids.is_empty() {
        println!("  flows:       {}", evidence.canonical_flow_ids.join(", "));
    }
    if !evidence.primitive_ids.is_empty() {
        println!("  primitives:  {}", evidence.primitive_ids.join(", "));
    }
}

pub(crate) fn print_model_usage_summary(evidence: &RunEvidenceSummary) {
    println!("Model usage");
    println!("  run:       {}", evidence.run.run_id);
    println!("  status:    {}", evidence.run.status);
    println!(
        "  requests:  {} model / {} network",
        evidence.model_usage.model_request_count, evidence.model_usage.network_request_count
    );
    println!("  responses: {}", evidence.model_usage.model_response_count);
    println!(
        "  tokens:    {} prompt / {} completion / {} total",
        evidence.model_usage.prompt_tokens,
        evidence.model_usage.completion_tokens,
        evidence.model_usage.total_tokens
    );
    println!("  chars:     {}", evidence.model_usage.response_chars);
    println!(
        "  core:      {} / schema {}",
        if evidence.core_trace.core_aligned {
            "aligned"
        } else {
            "drift"
        },
        evidence.core_trace.manifest_schema_version
    );
}

pub(crate) fn print_run_transcript(transcript: &RunTranscript) {
    println!("Run transcript");
    println!("  run:       {}", transcript.run.run_id);
    println!("  workspace: {}", transcript.run.workspace_id);
    println!("  status:    {}", transcript.run.status);
    println!("  events:    {}", transcript.events.len());
    println!(
        "  flows:     {}",
        transcript.evidence.canonical_flow_ids.join(", ")
    );
    println!(
        "  primitives: {}",
        transcript.evidence.primitive_ids.join(", ")
    );
    println!(
        "  core:      {} / schema {}",
        if transcript.evidence.core_trace.core_aligned {
            "aligned"
        } else {
            "drift"
        },
        transcript.evidence.core_trace.manifest_schema_version
    );
    println!("  tools:     {}", transcript.evidence.tool_call_count);
    println!(
        "  model:     {} req / {} resp / {} net",
        transcript.evidence.model_usage.model_request_count,
        transcript.evidence.model_usage.model_response_count,
        transcript.evidence.model_usage.network_request_count
    );
    if transcript.evidence.model_usage.total_tokens > 0 {
        println!(
            "  tokens:    {} prompt / {} completion / {} total",
            transcript.evidence.model_usage.prompt_tokens,
            transcript.evidence.model_usage.completion_tokens,
            transcript.evidence.model_usage.total_tokens
        );
    }
    println!(
        "  instr:     {}",
        transcript.evidence.agent_instruction_paths.len()
    );
    if let Some(worktree) = &transcript.evidence.worktree {
        println!(
            "  worktree:  {} / {} changes",
            if worktree.clean { "clean" } else { "dirty" },
            worktree.changed_files.len()
        );
    }
    println!(
        "  refs:      {}",
        transcript.evidence.prompt_references.len()
    );
    println!(
        "  knowledge: {}",
        transcript.evidence.knowledge_sources.len()
    );
    println!("  ratings:   {}", transcript.evidence.source_ratings.len());
    println!(
        "  event gc:  {} retained / {} filtered ({})",
        transcript.evidence.event_gc.retained_event_count,
        transcript.evidence.event_gc.filtered_event_count,
        transcript.evidence.event_gc.policy_id
    );
    println!("  artifacts: {}", transcript.evidence.artifact_paths.len());
    if !transcript.evidence.prompt_references.is_empty() {
        println!("  prompt refs:");
        for reference in &transcript.evidence.prompt_references {
            println!("    @{reference}");
        }
    }
    if !transcript.evidence.agent_instruction_paths.is_empty() {
        println!("  instructions:");
        for path in &transcript.evidence.agent_instruction_paths {
            println!("    {path}");
        }
    }
    if let Some(worktree) = &transcript.evidence.worktree {
        if !worktree.changed_files.is_empty() {
            println!("  changed files:");
            for change in worktree.changed_files.iter().take(12) {
                println!("    {} {}", change.status, change.path);
            }
        }
    }
    if !transcript.evidence.knowledge_sources.is_empty() {
        println!("  sources:");
        for source in &transcript.evidence.knowledge_sources {
            println!("    {}  {}", source.title, source.path);
        }
    }
    if !transcript.evidence.source_ratings.is_empty() {
        println!("  source ratings:");
        for rating in &transcript.evidence.source_ratings {
            println!(
                "    {}  {}/5  {}",
                rating.source_title,
                rating.rating,
                if rating.note.is_empty() {
                    "no note"
                } else {
                    rating.note.as_str()
                }
            );
        }
    }
    if !transcript.evidence.artifact_paths.is_empty() {
        println!("  artifacts:");
        for artifact in &transcript.evidence.artifacts {
            println!(
                "    {}  {} bytes  {}",
                artifact.artifact_id, artifact.size_bytes, artifact.path
            );
        }
        for path in transcript.evidence.artifact_paths.iter().filter(|path| {
            !transcript
                .evidence
                .artifacts
                .iter()
                .any(|artifact| artifact.path == **path)
        }) {
            println!("    {path}");
        }
    }
    if let Some(turn) = &transcript.chat_turn {
        println!();
        println!("User");
        println!("{}", turn.user_message);
    }
    if let Some(response) = &transcript.final_response {
        println!();
        println!("Assistant");
        println!("{response}");
    }
    if !transcript.events.is_empty() {
        println!();
        println!("Events");
        for event in &transcript.events {
            println!(
                "  #{:<4} {:<26} {:<10} {:<22} {}",
                event.sequence,
                event.kind,
                event.canonical_flow_id,
                event.primitive_id,
                event.run_id.as_deref().unwrap_or("workspace")
            );
        }
    }
}

pub(crate) fn print_knowledge_sources(sources: &[KnowledgeSource]) {
    if sources.is_empty() {
        println!("No knowledge sources registered.");
        return;
    }
    println!("Knowledge sources");
    for source in sources {
        println!(
            "  {}  {} bytes  {}",
            source.source_id, source.size_bytes, source.path
        );
    }
}

pub(crate) fn print_knowledge_source(source: &KnowledgeSource) {
    println!("Knowledge source");
    println!("  id:        {}", source.source_id);
    println!("  workspace: {}", source.workspace_id);
    println!("  title:     {}", source.title);
    println!("  bytes:     {}", source.size_bytes);
    println!("  path:      {}", source.path);
}

pub(crate) fn print_knowledge_preview(preview: &KnowledgeSourcePreview) {
    print_knowledge_source(&preview.source);
    println!("  read:      {} bytes", preview.bytes_read);
    println!("  truncated: {}", preview.truncated);
    println!();
    print!("{}", preview.preview);
    if preview.truncated && !preview.preview.ends_with('\n') {
        println!();
    }
}

pub(crate) fn print_source_rating(rating: &SourceRating) {
    println!("Source rating");
    println!("  source:    {}", rating.source_id);
    println!("  workspace: {}", rating.workspace_id);
    println!(
        "  run:       {}",
        rating.run_id.as_deref().unwrap_or("workspace")
    );
    println!("  rating:    {}/5", rating.rating);
    println!("  title:     {}", rating.source_title);
    println!("  path:      {}", rating.source_path);
    if !rating.note.is_empty() {
        println!("  note:      {}", rating.note);
    }
}

pub(crate) fn print_event_gc_preview(preview: &EventGcPreview) {
    println!("Event GC preview");
    println!("  run:       {}", preview.run.run_id);
    println!("  workspace: {}", preview.run.workspace_id);
    println!("  policy:    {}", preview.summary.policy_id);
    println!("  retain:    last {} events", preview.summary.retain_last);
    println!("  retained:  {}", preview.summary.retained_event_count);
    println!("  filtered:  {}", preview.summary.filtered_event_count);
    if !preview.summary.retained_sequences.is_empty() {
        println!(
            "  retained sequences: {}",
            sequence_summary(&preview.summary.retained_sequences)
        );
    }
    if !preview.summary.filtered_sequences.is_empty() {
        println!(
            "  filtered sequences: {}",
            sequence_summary(&preview.summary.filtered_sequences)
        );
    }
    if !preview.retained_events.is_empty() {
        println!("  retained events:");
        for event in &preview.retained_events {
            println!(
                "    #{:<4} {:<26} {:<10} {}",
                event.sequence, event.kind, event.canonical_flow_id, event.primitive_id
            );
        }
    }
}

pub(crate) fn print_tool_trace(trace: &[LocalToolTraceEntry]) {
    if trace.is_empty() {
        println!("No tool calls recorded.");
        return;
    }
    println!("Tool trace");
    for entry in trace {
        println!(
            "  {}  {}  req={}  done={}  {}",
            entry.call_id,
            entry.name,
            entry
                .requested_sequence
                .map(|sequence| sequence.to_string())
                .unwrap_or_else(|| "none".to_string()),
            entry
                .completed_sequence
                .map(|sequence| sequence.to_string())
                .unwrap_or_else(|| "none".to_string()),
            match entry.success {
                Some(true) => "ok",
                Some(false) => "failed",
                None => "pending",
            }
        );
        println!("    input:  {}", compact_json(&entry.input));
        if let Some(output) = &entry.output {
            println!("    output: {}", compact_json(output));
        }
        if let Some(error) = &entry.error {
            println!("    error:  {error}");
        }
    }
}

pub(crate) fn print_run_core_trace(trace: &LocalRunCoreTrace) {
    println!("Structure core trace");
    println!("  run:       {}", trace.run.run_id);
    println!("  workspace: {}", trace.run.workspace_id);
    println!("  status:    {}", trace.run.status);
    println!("  schema:    {}", trace.manifest_schema_version);
    println!(
        "  core:      {}",
        if trace.core_aligned {
            "aligned"
        } else {
            "drift"
        }
    );
    println!("  events:    {}", trace.event_count);
    println!(
        "  flow path: {}",
        if trace.flow_path.is_empty() {
            "none".to_string()
        } else {
            trace.flow_path.join(" -> ")
        }
    );
    println!(
        "  primitive path: {}",
        if trace.primitive_path.is_empty() {
            "none".to_string()
        } else {
            trace.primitive_path.join(" -> ")
        }
    );
    if !trace.steps.is_empty() {
        println!();
        println!("Steps");
        for step in &trace.steps {
            println!(
                "  #{:<4} {:<26} {:<10} {:<22} {}",
                step.sequence,
                step.kind,
                step.canonical_flow_id,
                step.primitive_id,
                step.payload_summary
            );
        }
    }
}

pub(crate) fn print_run_plan(plan: &LocalRunPlan) {
    println!("Run plan");
    println!("  run:       {}", plan.run.run_id);
    println!("  workspace: {}", plan.run.workspace_id);
    println!("  status:    {}", plan.status);
    println!(
        "  steps:     {} total / {} completed",
        plan.step_count, plan.completed_step_count
    );
    println!("  model:     {} requests", plan.model_request_count);
    println!("  tools:     {} requested", plan.tool_call_count);
    if !plan.steps.is_empty() {
        println!();
        println!("Steps");
        for step in &plan.steps {
            let iteration = step
                .iteration
                .map(|value| format!(" iter {value}"))
                .unwrap_or_default();
            println!(
                "  #{:<4} [{}]{} {}",
                step.sequence, step.status, iteration, step.title
            );
            println!(
                "        flow={} primitive={} tools={} total_results={}",
                step.canonical_flow_id,
                step.primitive_id,
                step.tool_call_count,
                step.total_tool_results
            );
            if !step.prompt_references.is_empty() {
                println!("        refs: @{}", step.prompt_references.join(", @"));
            }
        }
    }
}

pub(crate) fn print_run_review(review: &LocalRunReview) {
    println!("Run review");
    println!("  run:       {}", review.run.run_id);
    println!("  workspace: {}", review.run.workspace_id);
    println!("  status:    {}", review.status);
    println!(
        "  core:      {}",
        if review.core_aligned {
            "aligned"
        } else {
            "drift"
        }
    );
    println!("  events:    {}", review.event_count);
    println!(
        "  tools:     {} total / {} failed",
        review.tool_call_count, review.failed_tool_call_count
    );
    println!(
        "  model:     {} req / {} resp / {} net",
        review.model_usage.model_request_count,
        review.model_usage.model_response_count,
        review.model_usage.network_request_count
    );
    if review.model_usage.total_tokens > 0 {
        println!(
            "  tokens:    {} prompt / {} completion / {} total",
            review.model_usage.prompt_tokens,
            review.model_usage.completion_tokens,
            review.model_usage.total_tokens
        );
    }
    println!("  response:  {} chars", review.final_response_chars);
    println!(
        "  flow path: {}",
        if review.flow_path.is_empty() {
            "none".to_string()
        } else {
            review.flow_path.join(" -> ")
        }
    );
    println!(
        "  primitive path: {}",
        if review.primitive_path.is_empty() {
            "none".to_string()
        } else {
            review.primitive_path.join(" -> ")
        }
    );
    if let Some(artifact) = &review.response_artifact {
        println!("  response artifact: {}", artifact.path);
    }
    if let Some(artifact) = &review.proposal_artifact {
        println!("  proposal:  {}  {}", artifact.artifact_id, artifact.path);
    }
    if !review.next_actions.is_empty() {
        println!();
        println!("Next actions");
        for action in &review.next_actions {
            println!("  - {action}");
        }
    }
}

fn compact_json(value: &serde_json::Value) -> String {
    let raw = serde_json::to_string(value).unwrap_or_else(|_| "null".to_string());
    if raw.chars().count() > 600 {
        format!("{}...", raw.chars().take(600).collect::<String>())
    } else {
        raw
    }
}

fn sequence_summary(sequences: &[i64]) -> String {
    match (sequences.first(), sequences.last()) {
        (Some(first), Some(last)) if first != last => {
            format!("{first}..{last} ({} events)", sequences.len())
        }
        (Some(only), _) => only.to_string(),
        _ => "none".to_string(),
    }
}

pub(crate) fn print_agent_context(context: &LocalAgentContext) {
    println!("Agent context");
    println!("  workspace: {}", context.workspace_id);
    println!("  mode:      {}", context.mode);
    println!("  repo:      {}", context.repo_root);
    println!("  runtime:   {}", context.runtime_db);
    println!(
        "  worktree:  {} / {} changes",
        if context.worktree.clean {
            "clean"
        } else {
            "dirty"
        },
        context.worktree.changed_files.len()
    );
    println!("  instr:     {}", context.agent_instructions.len());
    println!("  knowledge: {}", context.knowledge_sources.len());
    println!("  turns:     {}", context.recent_turns.len());
    println!("  limit:     {}", context.context_replay_limit);
    if !context.agent_instructions.is_empty() {
        println!("  instructions:");
        for instruction in &context.agent_instructions {
            println!(
                "    {}  {} bytes  truncated: {}",
                instruction.path, instruction.size_bytes, instruction.truncated
            );
        }
    }
    if !context.knowledge_sources.is_empty() {
        println!("  sources:");
        for source in &context.knowledge_sources {
            println!("    {}  {}", source.title, source.path);
        }
    }
    if !context.recent_turns.is_empty() {
        println!("  recent turns:");
        for turn in context.recent_turns.iter().take(5) {
            println!(
                "    {}  {}  {} events",
                turn.run_id, turn.status, turn.event_count
            );
        }
    }
}

pub(crate) fn print_artifacts(artifacts: &[ArtifactRecord]) {
    if artifacts.is_empty() {
        println!("No local artifacts indexed.");
        return;
    }
    println!("Local artifacts");
    for artifact in artifacts {
        println!(
            "  {}  {}  {} bytes  {}",
            artifact.artifact_id, artifact.kind, artifact.size_bytes, artifact.path
        );
    }
}

pub(crate) fn print_run_compact(compact: &LocalRunCompact) {
    println!("Run compact");
    println!("  run:       {}", compact.run.run_id);
    println!("  workspace: {}", compact.run.workspace_id);
    println!("  status:    {}", compact.status);
    println!(
        "  core:      {}",
        if compact.core_aligned {
            "aligned"
        } else {
            "drift"
        }
    );
    println!("  events:    {}", compact.event_count);
    println!("  tools:     {}", compact.tool_call_count);
    println!(
        "  model:     {} req / {} resp / {} net",
        compact.model_usage.model_request_count,
        compact.model_usage.model_response_count,
        compact.model_usage.network_request_count
    );
    if compact.model_usage.total_tokens > 0 {
        println!(
            "  tokens:    {} prompt / {} completion / {} total",
            compact.model_usage.prompt_tokens,
            compact.model_usage.completion_tokens,
            compact.model_usage.total_tokens
        );
    }
    println!();
    println!("Summary");
    println!("  {}", compact.summary);
    if !compact.carry_forward_items.is_empty() {
        println!();
        println!("Carry Forward");
        for item in &compact.carry_forward_items {
            println!("  - {item}");
        }
    }
    if !compact.next_actions.is_empty() {
        println!();
        println!("Next Actions");
        for action in &compact.next_actions {
            println!("  - {action}");
        }
    }
    if !compact.artifact_paths.is_empty() {
        println!();
        println!("Artifacts");
        for path in &compact.artifact_paths {
            println!("  - {path}");
        }
    }
    println!();
    println!("Continuation Context");
    println!("{}", compact.continuation_context);
}

pub(crate) fn print_artifact_preview(preview: &ArtifactPreview) {
    println!("Artifact");
    println!("  id:        {}", preview.artifact.artifact_id);
    println!("  run:       {}", preview.artifact.run_id);
    println!("  workspace: {}", preview.artifact.workspace_id);
    println!("  kind:      {}", preview.artifact.kind);
    println!("  bytes:     {}", preview.artifact.size_bytes);
    println!("  path:      {}", preview.artifact.path);
    println!("  read:      {} bytes", preview.bytes_read);
    println!("  truncated: {}", preview.truncated);
    println!();
    print!("{}", preview.preview);
    if preview.truncated && !preview.preview.ends_with('\n') {
        println!();
    }
}

pub(crate) fn print_proposal_review(review: &ProposalReview) {
    println!("Proposal review");
    println!("  artifact:  {}", review.artifact.artifact_id);
    println!("  run:       {}", review.artifact.run_id);
    println!("  target:    {}", review.target_path);
    println!("  risk:      {}", review.risk_level);
    println!("  can apply: {}", review.can_apply);
    println!("  dry-run:   required");
    println!("  new file:  {}", review.new_file);
    println!("  exists:    {}", review.target_exists);
    println!("  hunks:     {}", review.hunk_count);
    println!(
        "  lines:     {} added / {} removed",
        review.added_lines, review.removed_lines
    );
    if !review.checks.is_empty() {
        println!();
        println!("Checks");
        for check in &review.checks {
            println!("  [{}] {} - {}", check.status, check.id, check.message);
        }
    }
}

pub(crate) fn print_workspace_replay(replay: &WorkspaceReplay) {
    println!("Workspace replay");
    println!("  workspace: {}", replay.workspace_id);
    println!("  events:    {}", replay.events.len());
    println!("  runs:      {}", replay.runs.len());
    println!("  knowledge: {}", replay.knowledge_sources.len());
    println!("  artifacts: {}", replay.artifacts.len());
    if let Some(sequence) = replay.last_sequence {
        println!("  last seq:  {sequence}");
    }
    if !replay.events.is_empty() {
        println!("  event stream:");
        for event in &replay.events {
            println!(
                "    #{:<4} {:<26} {:<10} {}",
                event.sequence,
                event.kind,
                event.canonical_flow_id,
                event.run_id.as_deref().unwrap_or("workspace")
            );
        }
    }
}

pub(crate) fn print_workspace_event_feed(feed: &WorkspaceEventFeed) {
    println!("Workspace event feed");
    println!("  workspace:  {}", feed.workspace_id);
    println!("  after seq:  {}", feed.after_sequence);
    println!("  events:     {}", feed.events.len());
    println!("  next after: {}", feed.next_after_sequence);
    if let Some(sequence) = feed.last_sequence {
        println!("  last seq:   {sequence}");
    }
    if !feed.events.is_empty() {
        println!("  event stream:");
        for event in &feed.events {
            println!(
                "    #{:<4} {:<26} {:<10} {}",
                event.sequence,
                event.kind,
                event.canonical_flow_id,
                event.run_id.as_deref().unwrap_or("workspace")
            );
        }
    }
}

pub(crate) fn print_local_evidence_bundle(bundle: &LocalEvidenceBundle) {
    println!("Local evidence bundle");
    println!("  schema:       {}", bundle.schema_version);
    println!("  workspace:    {}", bundle.workspace_id);
    println!("  generated:    {}", bundle.generated_at_ms);
    println!("  parity:       {}", bundle.parity_report.passed);
    println!("  events:       {}", bundle.workspace_replay.events.len());
    println!("  runs:         {}", bundle.workspace_replay.runs.len());
    println!(
        "  knowledge:    {}",
        bundle.workspace_replay.knowledge_sources.len()
    );
    println!(
        "  artifacts:    {}",
        bundle.workspace_replay.artifacts.len()
    );
    println!("  run evidence: {}", bundle.run_evidence.len());
}

fn print_core_primitive(primitive: &CorePrimitive) {
    println!("  {} ({})", primitive.name, primitive.id);
    println!("    paper:    {}", primitive.paper_section);
    println!("    invariant: {}", primitive.invariant);
    println!("    contract:  {}", primitive.implementation_contract);
}

fn first_line(text: &str) -> &str {
    text.lines().next().unwrap_or(text)
}
