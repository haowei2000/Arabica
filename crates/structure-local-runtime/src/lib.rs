mod model;
mod runtime;
mod store;
mod tools;
mod types;

pub use model::{
    selected_synthesis_provider, DeterministicLocalModelProvider, EnvApiModelProvider,
    LocalModelProvider, ModelOutput, ModelPlan, ModelRequest,
};
pub use runtime::{
    ContinuationRequest, LocalAgentRuntime, RunRequest, WorkspaceContinuationRequest,
};
pub use store::SqliteLocalStore;
pub use tools::{BuiltinLocalToolRegistry, LocalToolRegistry};
pub use types::{
    ArtifactPreview, ArtifactRecord, ChatTurn, CoreExecutionTrace, EventGcPreview, EventGcSummary,
    KnowledgeSource, KnowledgeSourcePreview, LocalAgentContext, LocalAgentMode, LocalEvent,
    LocalEvidenceBundle, LocalLlmDiagnostic, LocalRunCompact, LocalRunCoreTrace,
    LocalRunCoreTraceStep, LocalRunPlan, LocalRunPlanStep, LocalRunReview, LocalToolCall,
    LocalToolResult, LocalToolTraceEntry, ModelTokenUsage, ModelUsageSummary, ProposalApplyResult,
    ProposalReview, ProposalReviewCheck, RunAttempt, RunEventKind, RunEvidenceSummary, RunResult,
    RunStatus, RunSummary, RunTranscript, SourceRating, WorkspaceCompact, WorkspaceCompactRun,
    WorkspaceEventFeed, WorkspaceReplay, WorkspaceSummary, WorkspaceUsageRun,
    WorkspaceUsageSummary, WorktreeChange, WorktreeSnapshot,
};

#[cfg(test)]
pub(crate) mod test_env {
    use std::env;
    use std::sync::{Mutex, MutexGuard};

    static OPENAI_ENV_LOCK: Mutex<()> = Mutex::new(());

    pub(crate) struct OpenAiEnvGuard {
        _lock: MutexGuard<'static, ()>,
        previous: Vec<(&'static str, Option<String>)>,
    }

    impl OpenAiEnvGuard {
        pub(crate) fn clear() -> Self {
            let lock = OPENAI_ENV_LOCK.lock().unwrap();
            let previous = openai_env_keys()
                .iter()
                .map(|key| (*key, env::var(key).ok()))
                .collect::<Vec<_>>();
            for key in openai_env_keys() {
                env::remove_var(key);
            }
            Self {
                _lock: lock,
                previous,
            }
        }
    }

    impl Drop for OpenAiEnvGuard {
        fn drop(&mut self) {
            for (key, value) in &self.previous {
                if let Some(value) = value {
                    env::set_var(key, value);
                } else {
                    env::remove_var(key);
                }
            }
        }
    }

    fn openai_env_keys() -> [&'static str; 3] {
        ["OPENAI__API_KEY", "OPENAI__BASE_URL", "OPENAI__MODEL"]
    }
}
