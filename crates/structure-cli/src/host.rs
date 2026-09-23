//! Composes the production crates into one CLI-shaped runtime.
//!
//! This module owns nothing a UI surface would (see
//! `docs/protocol.md` §1 and `docs/runtime_core_architecture.md` Appendix B):
//! it only wires `structure-provider`, `structure-runner`, and
//! `structure-runtime` into a concrete `CoreRuntime` and picks the fixed
//! policy a coding CLI needs. Terminal chat, `structure acp`, and `structure -p` build on
//! this; neither adds a second way to do it.

use std::collections::VecDeque;
use std::path::Path;

use clap::Args;
use sha2::{Digest, Sha256};
use structure_protocol::{RunId, SessionId};
use structure_provider::{
    ApiModelProvider, ApiProviderConfig, ApiType, ModelProgressSink, ModelProvider,
    ModelRunRequest, ModelRunResult, ProviderError,
};
pub use structure_runner::LocalRunnerPolicy;
use structure_runner::{
    LocalRunner, RunnerEnvironment, RunnerError, ToolExecutionRequest, ToolExecutionResult,
};
use structure_runtime::{
    CoreRuntime, RuntimeArchiveStore, RuntimeCompactionStrategy, ShortMemoryPolicy,
    memory_read_definition, memory_search_definition,
};
use structure_session::IdAllocator;

/// A run cap generous enough for a real coding task, short enough that a
/// runaway loop cannot bill forever. The user can always cancel; see
/// `RunControl` in `structure-runtime`.
const MAX_MODEL_STEPS_PER_RUN: usize = 100;

/// Unique ids across terminal processes sharing one session store.
#[derive(Debug, Default)]
pub struct UuidIds;

impl IdAllocator for UuidIds {
    fn session_id(&mut self) -> SessionId {
        SessionId::new(uuid::Uuid::now_v7().to_string())
    }

    fn run_id(&mut self) -> RunId {
        RunId::new(uuid::Uuid::now_v7().to_string())
    }
}

/// Environment variables read by [`resolve_provider_config`], reusing
/// `structure-server`'s names (`crates/structure-server/src/lib.rs`) so a
/// provider configured for one host works unchanged for the other.
mod env {
    pub const API_KEY: &str = "OPENAI__API_KEY";
    pub const BASE_URL: &str = "OPENAI__BASE_URL";
    pub const MODEL: &str = "OPENAI__MODEL";
    pub const API_TYPE: &str = "STRUCTURE__API_TYPE";
}

/// Model selection, deliberately not the credential. A `--api-key` flag would
/// put the key in shell history and process listings. ACP reads the key from
/// the environment; terminal chat and print mode can also use saved auth.
#[derive(Args, Clone, Debug, Default)]
pub struct HostConfigArgs {
    /// Override OPENAI__MODEL.
    #[arg(long)]
    pub model: Option<String>,
    /// Override STRUCTURE__API_TYPE (open_ai_chat_completions, open_ai_responses,
    /// anthropic_messages, gemini_generate_content, gemini_interactions).
    #[arg(long)]
    pub api_type: Option<String>,
    /// Override OPENAI__BASE_URL.
    #[arg(long)]
    pub base_url: Option<String>,
}

fn require(value: Option<String>, var: &str, flag: &str) -> Result<String, ProviderError> {
    value.ok_or_else(|| ProviderError::new(format!("{var} is required (or pass --{flag})")))
}

/// Resolve provider configuration: a CLI flag overrides its matching
/// environment variable; `OPENAI__API_KEY` has no flag and must be exported.
///
/// Reads through `lookup` rather than `std::env::var` directly, so tests can
/// supply a fixed map instead of mutating the real process environment
/// (`std::env::set_var` is `unsafe` as of Rust 2024, and this workspace
/// forbids `unsafe` outright).
pub fn resolve_provider_config(
    args: &HostConfigArgs,
    lookup: impl Fn(&str) -> Option<String>,
) -> Result<ApiProviderConfig, ProviderError> {
    let api_type = args
        .api_type
        .clone()
        .or_else(|| lookup(env::API_TYPE))
        .unwrap_or_else(|| ApiType::OpenAiChatCompletions.to_string())
        .parse::<ApiType>()?;
    let api_key = lookup(env::API_KEY).ok_or_else(|| {
        ProviderError::new(format!(
            "{} is required; export it in your shell, it is never a flag",
            env::API_KEY
        ))
    })?;
    let base_url = require(
        args.base_url.clone().or_else(|| lookup(env::BASE_URL)),
        env::BASE_URL,
        "base-url",
    )?;
    let model = require(
        args.model.clone().or_else(|| lookup(env::MODEL)),
        env::MODEL,
        "model",
    )?;
    Ok(ApiProviderConfig::new(api_type, api_key, base_url, model))
}

/// The real environment, for `main` to pass as `resolve_provider_config`'s
/// `lookup`.
pub fn process_environment(name: &str) -> Option<String> {
    std::env::var(name).ok()
}

/// The model this host talks to.
///
/// A concrete enum, not a boxed trait object, following `structure-server`'s
/// `ServerModel`: an ACP host's `session/prompt` handler spawns the run on
/// its own task (`cx.spawn`, see Appendix B), and only a concrete type lets
/// the compiler prove that future is `Send`.
#[derive(Debug)]
pub enum HostModel {
    Api(ApiModelProvider),
    StreamingApi(ApiModelProvider, ModelProgressSink),
    /// A fixed sequence of responses, consumed one per call. Used by this
    /// crate's own tests; never selected from user-facing configuration.
    Scripted(ScriptedModel),
}

impl ModelProvider for HostModel {
    async fn complete(
        &mut self,
        request: ModelRunRequest,
    ) -> Result<ModelRunResult, ProviderError> {
        match self {
            Self::Api(model) => model.complete(request).await,
            Self::StreamingApi(model, progress) => {
                model.complete_with_progress(request, progress).await
            }
            Self::Scripted(model) => model.complete(request).await,
        }
    }

    async fn cancel(&mut self, run_id: &RunId) -> Result<bool, ProviderError> {
        match self {
            Self::Api(model) => model.cancel(run_id).await,
            Self::StreamingApi(model, _) => model.cancel(run_id).await,
            Self::Scripted(model) => model.cancel(run_id).await,
        }
    }
}

/// Replays a fixed sequence of results, one per call, erroring once
/// exhausted.
#[derive(Debug, Default)]
pub struct ScriptedModel {
    results: VecDeque<ModelRunResult>,
}

impl ScriptedModel {
    pub fn new(results: impl IntoIterator<Item = ModelRunResult>) -> Self {
        Self {
            results: results.into_iter().collect(),
        }
    }
}

impl ModelProvider for ScriptedModel {
    async fn complete(
        &mut self,
        _request: ModelRunRequest,
    ) -> Result<ModelRunResult, ProviderError> {
        self.results
            .pop_front()
            .ok_or_else(|| ProviderError::new("scripted model has no more responses"))
    }

    async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
        Ok(false)
    }
}

/// Routes local tools to the confined runner and session-scoped MCP tools to
/// their originating server.
#[derive(Debug)]
pub struct HostRunner {
    local: LocalRunner,
    mcp: crate::mcp::McpTools,
}

impl RunnerEnvironment for HostRunner {
    fn classify(
        &self,
        call: &structure_model::ToolCallItem,
    ) -> structure_protocol::ToolInteractionKind {
        if self.mcp.contains(&call.name) {
            self.mcp.classify(call)
        } else {
            self.local.classify(call)
        }
    }

    async fn execute(
        &mut self,
        request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError> {
        if self.mcp.contains(&request.call.name) {
            self.mcp.execute(request).await
        } else {
            self.local.execute(request).await
        }
    }

    async fn cancel(&mut self, run_id: &RunId) -> Result<bool, RunnerError> {
        let local = self.local.cancel(run_id).await?;
        let mcp = self.mcp.cancel(run_id).await?;
        Ok(local || mcp)
    }
}

pub type HostRuntime = CoreRuntime<HostModel, HostRunner>;

/// The standing instruction every coding CLI run carries: where it is, what
/// it is running on, and the one rule the confined tools already enforce.
/// Rendered through `system_instructions`
/// (`crates/structure-runtime/src/lib.rs`), which places it as one message
/// right after the base system prompt, distinct from turn-scoped control
/// language.
pub fn coding_system_instructions(root: &Path) -> String {
    format!(
        "You are Structure, an autonomous coding agent working in {}. \
         The host operating system is {}. Use paths relative to that \
         workspace root for every tool call; the tools refuse absolute \
         paths and any path that would escape the root.",
        root.display(),
        std::env::consts::OS,
    )
}

/// Derives a stable [`structure_protocol::WorkspaceId`] from a working
/// directory: the same `cwd` always maps to the same id, distinct `cwd`s
/// (almost certainly) do not collide, and the id never leaks the path
/// itself. Shared by every host binding that opens a session from a `cwd`
/// (`acp::AcpState::new_session`, `print::run`) so the same project looks
/// like the same workspace regardless of which binding opened it.
pub fn workspace_id_for(cwd: &Path) -> structure_protocol::WorkspaceId {
    let digest = Sha256::digest(cwd.to_string_lossy().as_bytes());
    structure_protocol::WorkspaceId::new(format!("ws-{:.16}", format!("{digest:x}")))
}

/// Build a [`HostRuntime`] fixed to the CLI profile: provider-safe Policy
/// history with file-backed archival under the Structure home, a longer step budget than
/// `CoreRuntime::new`'s default with its no-progress guard disabled (cost is
/// bounded by the step count, and the user can cancel at any time), and the
/// tool list this exact policy both advertises and will execute.
pub fn build_host_runtime(
    model: HostModel,
    runner_root: &Path,
    tool_policy: LocalRunnerPolicy,
    structure_home: &Path,
) -> HostRuntime {
    build_host_runtime_with_mcp(
        model,
        runner_root,
        tool_policy,
        structure_home,
        crate::mcp::McpTools::default(),
    )
}

pub fn build_host_runtime_with_mcp(
    model: HostModel,
    runner_root: &Path,
    tool_policy: LocalRunnerPolicy,
    structure_home: &Path,
    mcp: crate::mcp::McpTools,
) -> HostRuntime {
    let mut tools = structure_runner::tool_definitions(&tool_policy);
    tools.push(memory_search_definition());
    tools.push(memory_read_definition());
    tools.extend_from_slice(mcp.definitions());
    let runner = HostRunner {
        local: LocalRunner::with_policy(runner_root, tool_policy),
        mcp,
    };
    let archive_root = structure_home
        .join("runtime-memory")
        .join(workspace_id_for(runner_root).to_string());
    let mut runtime = CoreRuntime::with_memory_configuration(
        model,
        runner,
        ShortMemoryPolicy::default(),
        false,
        RuntimeArchiveStore::File { root: archive_root },
    );
    runtime.set_compaction_strategy(RuntimeCompactionStrategy::FileBackedGc);
    runtime.set_tools(tools);
    runtime.set_max_model_steps_per_run(MAX_MODEL_STEPS_PER_RUN);
    runtime.set_max_model_steps_without_progress(usize::MAX);
    runtime.set_system_instructions(vec![coding_system_instructions(runner_root)]);
    runtime
}

#[cfg(test)]
mod tests {
    use super::*;
    use structure_runtime::HistoryProjection;

    fn args(model: Option<&str>, api_type: Option<&str>, base_url: Option<&str>) -> HostConfigArgs {
        HostConfigArgs {
            model: model.map(str::to_owned),
            api_type: api_type.map(str::to_owned),
            base_url: base_url.map(str::to_owned),
        }
    }

    /// A fixed environment for tests: no real process state is touched, so
    /// these run with cargo test's default parallelism like anything else.
    fn env_map(vars: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> + use<> {
        let vars: std::collections::HashMap<String, String> = vars
            .iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect();
        move |name: &str| vars.get(name).cloned()
    }

    #[test]
    fn a_cli_flag_overrides_its_matching_environment_variable() {
        let lookup = env_map(&[
            (env::API_KEY, "test-key"),
            (env::BASE_URL, "https://env.example/v1"),
            (env::MODEL, "env-model"),
        ]);
        let config = resolve_provider_config(&args(Some("flag-model"), None, None), lookup)
            .expect("config resolves");
        assert_eq!(config.model, "flag-model");
        assert_eq!(config.base_url, "https://env.example/v1");
        assert_eq!(config.api_type, ApiType::OpenAiChatCompletions);
    }

    #[test]
    fn a_missing_flag_falls_back_to_the_environment_variable() {
        let lookup = env_map(&[
            (env::API_KEY, "test-key"),
            (env::BASE_URL, "u"),
            (env::MODEL, "env-model"),
        ]);
        let config = resolve_provider_config(&args(None, None, None), lookup)
            .expect("config resolves from the environment alone");
        assert_eq!(config.model, "env-model");
    }

    #[test]
    fn a_missing_required_setting_names_both_the_variable_and_the_flag() {
        let lookup = env_map(&[(env::API_KEY, "test-key"), (env::MODEL, "m")]);
        let error = resolve_provider_config(&args(None, None, None), lookup)
            .expect_err("base_url is missing");
        let message = error.to_string();
        assert!(message.contains("OPENAI__BASE_URL"), "{message}");
        assert!(message.contains("--base-url"), "{message}");
    }

    #[test]
    fn the_api_key_has_no_flag_and_must_come_from_the_environment() {
        let lookup = env_map(&[(env::BASE_URL, "u"), (env::MODEL, "m")]);
        let error = resolve_provider_config(&args(None, None, None), lookup)
            .expect_err("the key is missing");
        assert!(error.to_string().contains("OPENAI__API_KEY"));
        // HostConfigArgs simply has no api_key field: the absence of a flag
        // is the safeguard, not a runtime check to bypass.
    }

    #[tokio::test]
    async fn host_model_dispatches_to_the_scripted_variant() {
        let mut model = HostModel::Scripted(ScriptedModel::new([ModelRunResult {
            final_output: Some("done".to_owned()),
            prepared_request: None,
            response: None,
        }]));
        let request = ModelRunRequest {
            session_id: structure_protocol::SessionId::new("s"),
            run_id: RunId::new("r"),
            input: "hi".to_owned(),
            short_memory: Vec::new(),
            run_memory: Vec::new(),
            long_memory: Vec::new(),
            tools: Vec::new(),
            tool_choice: structure_model::ToolChoice::Auto,
            continuation: Vec::new(),
            disclosure: structure_protocol::DisclosureLevel::Detail,
            system_instructions: Vec::new(),
        };
        let result = model.complete(request).await.expect("scripted response");
        assert_eq!(result.final_output.as_deref(), Some("done"));
    }

    fn temp_root(label: &str) -> std::path::PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("structure-cli-host-{label}-{unique}"));
        std::fs::create_dir(&root).expect("test root is created");
        root
    }

    #[test]
    fn build_host_runtime_fixes_the_cli_profile() {
        let root = temp_root("profile");
        let runtime = build_host_runtime(
            HostModel::Scripted(ScriptedModel::default()),
            &root,
            LocalRunnerPolicy::coding(),
            &root.join("state"),
        );

        assert_eq!(
            runtime.compaction_strategy(),
            RuntimeCompactionStrategy::FileBackedGc
        );
        assert_eq!(runtime.history_projection(), HistoryProjection::Policy);
        assert_eq!(
            runtime.archive_store(),
            &RuntimeArchiveStore::File {
                root: root
                    .join("state/runtime-memory")
                    .join(workspace_id_for(&root).to_string()),
            }
        );
        assert_eq!(runtime.max_model_steps_per_run(), MAX_MODEL_STEPS_PER_RUN);
        assert_eq!(runtime.max_model_steps_without_progress(), usize::MAX);
        assert_eq!(runtime.system_instructions().len(), 1);
        assert!(runtime.system_instructions()[0].contains(&root.display().to_string()));
        assert!(runtime.system_instructions()[0].contains(std::env::consts::OS));

        // The CLI advertises recovery tools but does not expose the
        // unrelated runtime_complete control tool.
        let names: Vec<&str> = runtime
            .tools()
            .iter()
            .map(|tool| tool.name.as_str())
            .collect();
        assert_eq!(
            names,
            vec![
                "read_file",
                "list_dir",
                "grep",
                "find_files",
                "write_file",
                "edit_files",
                "delete_file",
                "memory_search",
                "memory_read",
            ]
        );
        assert!(!names.contains(&"runtime_complete"));

        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn build_host_runtime_honors_a_policy_without_shell() {
        let root = temp_root("no-shell");
        let runtime = build_host_runtime(
            HostModel::Scripted(ScriptedModel::default()),
            &root,
            LocalRunnerPolicy::read_only(),
            &root.join("state"),
        );
        assert!(!runtime.tools().iter().any(|tool| tool.name == "shell"));
        std::fs::remove_dir_all(root).expect("cleanup");
    }

    #[test]
    fn workspace_id_is_stable_for_the_same_cwd_and_differs_for_another() {
        let a = workspace_id_for(std::path::Path::new("/workspace/one"));
        let a_again = workspace_id_for(std::path::Path::new("/workspace/one"));
        let b = workspace_id_for(std::path::Path::new("/workspace/two"));
        assert_eq!(a, a_again);
        assert_ne!(a, b);
        assert!(a.to_string().starts_with("ws-"));
    }
}
