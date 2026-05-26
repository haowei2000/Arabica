use structure_local_core::{
    CoreCapability, CorePrimitive, LocalSnapshot, ProductSurface, StructureCoreManifest,
    SurfaceParityReport,
};
use structure_local_runtime::{
    ArtifactPreview, ArtifactRecord, KnowledgeSource, KnowledgeSourcePreview, LocalEvent,
    LocalEvidenceBundle, RunAttempt, RunEvidenceSummary, RunResult, RunSummary, RunTranscript,
    WorkspaceEventFeed, WorkspaceReplay, WorkspaceSummary,
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
    println!("  artifacts: {}", transcript.evidence.artifact_paths.len());
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
