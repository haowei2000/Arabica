//! Recording and pairing for controlled live experiments.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashSet},
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
use structure_protocol::RunId;
use structure_provider::{
    ExperimentControls, ModelProvider, ModelRunRequest, ModelRunResult, OpenAiModelProvider,
    ProviderError,
};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ExperimentConfig {
    pub sampling: ExperimentControls,
    pub shared_first_response: bool,
}

pub fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

type FirstTurns = BTreeMap<usize, (Vec<u8>, ModelRunResult)>;
#[derive(Clone, Default)]
pub struct PairedFirstTurns(Arc<Mutex<FirstTurns>>);

pub trait WireModel: ModelProvider {
    fn request_bytes(&self, request: &ModelRunRequest) -> Result<Vec<u8>, ProviderError>;
}
impl WireModel for OpenAiModelProvider {
    fn request_bytes(&self, request: &ModelRunRequest) -> Result<Vec<u8>, ProviderError> {
        self.experiment_request_bytes(request)
    }
}

pub struct AuditedProvider<P = OpenAiModelProvider> {
    inner: P,
    first: PairedFirstTurns,
    shared: bool,
    seen: HashSet<RunId>,
    ordinal: usize,
    calls: usize,
    root: PathBuf,
}

impl<P> AuditedProvider<P> {
    pub fn new(inner: P, first: PairedFirstTurns, shared: bool, root: PathBuf) -> Self {
        Self {
            inner,
            first,
            shared,
            seen: HashSet::new(),
            ordinal: 0,
            calls: 0,
            root,
        }
    }
}

fn io_error(e: impl std::fmt::Display) -> ProviderError {
    ProviderError::new(format!("experiment archive: {e}"))
}

pub fn write_new(path: &Path, bytes: &[u8]) -> Result<(), ProviderError> {
    use std::io::Write;
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(io_error)?;
    f.write_all(bytes).map_err(io_error)?;
    f.sync_all().map_err(io_error)
}

impl<P: WireModel> ModelProvider for AuditedProvider<P> {
    async fn complete(
        &mut self,
        request: ModelRunRequest,
    ) -> Result<ModelRunResult, ProviderError> {
        let bytes = self.inner.request_bytes(&request)?;
        self.calls += 1;
        let dir = self.root.join(format!("call-{:04}", self.calls));
        fs::create_dir_all(&self.root).map_err(io_error)?;
        fs::create_dir(&dir).map_err(io_error)?;
        write_new(&dir.join("request.json"), &bytes)?;
        write_new(&dir.join("request.sha256"), sha256(&bytes).as_bytes())?;
        let first_call = self.seen.insert(request.run_id.clone());
        if first_call {
            self.ordinal += 1;
        }
        let previous = if first_call {
            self.first
                .0
                .lock()
                .map_err(io_error)?
                .get(&self.ordinal)
                .cloned()
        } else {
            None
        };
        if let Some((expected, _)) = &previous {
            if *expected != bytes {
                write_new(
                    &dir.join("error.txt"),
                    b"First request mismatch; no API call made",
                )?;
                return Err(ProviderError::new(
                    "paired first request differs; refusing model call",
                ));
            }
        }
        let replay = self.shared && previous.is_some();
        let capture = first_call && previous.is_none();
        write_new(&dir.join("origin.json"), &serde_json::to_vec(&serde_json::json!({
            "first_call": first_call, "pair_index": self.ordinal, "replayed": replay,
            "api_call_made": !replay,
            "usage_semantics": if replay { "copied source usage; exclude from actual billed-call totals" } else { "provider telemetry only; not independent KV cache" }
        })).map_err(io_error)?)?;
        let result = if replay {
            Ok(previous.expect("checked above").1)
        } else {
            self.inner.complete(request).await
        };
        match result {
            Ok(result) => {
                let encoded = serde_json::to_vec(&serde_json::json!({"final_output": result.final_output, "prepared_request": result.prepared_request, "response": result.response})).map_err(io_error)?;
                write_new(&dir.join("response.json"), &encoded)?;
                write_new(&dir.join("response.sha256"), sha256(&encoded).as_bytes())?;
                if capture {
                    self.first
                        .0
                        .lock()
                        .map_err(io_error)?
                        .insert(self.ordinal, (bytes, result.clone()));
                }
                Ok(result)
            }
            Err(error) => {
                write_new(&dir.join("error.txt"), error.to_string().as_bytes())?;
                Err(error)
            }
        }
    }
    async fn cancel(&mut self, id: &RunId) -> Result<bool, ProviderError> {
        self.inner.cancel(id).await
    }
}

/// Ideal token-prefix cache: cold per instance, no eviction or block rounding.
/// Inputs must be actual token IDs produced with a fixed tokenizer/template.
#[derive(Default)]
pub struct PrefixCache {
    prefixes: Vec<Vec<u32>>,
}
impl PrefixCache {
    pub fn observe(&mut self, tokens: &[u32]) -> (usize, usize) {
        let hit = self
            .prefixes
            .iter()
            .map(|old| old.iter().zip(tokens).take_while(|(a, b)| a == b).count())
            .max()
            .unwrap_or(0);
        self.prefixes.push(tokens.to_vec());
        (hit, tokens.len() - hit)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    struct Fake(Arc<AtomicUsize>);
    impl WireModel for Fake {
        fn request_bytes(&self, r: &ModelRunRequest) -> Result<Vec<u8>, ProviderError> {
            Ok(r.input.as_bytes().to_vec())
        }
    }
    impl ModelProvider for Fake {
        async fn complete(&mut self, _: ModelRunRequest) -> Result<ModelRunResult, ProviderError> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Ok(ModelRunResult {
                final_output: Some("identical response".into()),
                ..Default::default()
            })
        }
        async fn cancel(&mut self, _: &RunId) -> Result<bool, ProviderError> {
            Ok(false)
        }
    }
    fn request(input: &str) -> ModelRunRequest {
        ModelRunRequest {
            session_id: structure_protocol::SessionId::new("s"),
            run_id: RunId::new("r"),
            input: input.into(),
            short_memory: vec![],
            run_memory: vec![],
            long_memory: vec![],
            tools: vec![],
            tool_choice: structure_model::ToolChoice::Auto,
            continuation: vec![],
            disclosure: structure_protocol::DisclosureLevel::Detail,
            system_instructions: vec![],
        }
    }
    #[tokio::test]
    async fn shared_first_is_identical_and_mismatch_never_calls_api() {
        let root = std::env::temp_dir().join(format!(
            "structure-pair-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let count = Arc::new(AtomicUsize::new(0));
        let first = PairedFirstTurns::default();
        let mut a = AuditedProvider::new(Fake(count.clone()), first.clone(), true, root.join("a"));
        let mut b = AuditedProvider::new(Fake(count.clone()), first.clone(), true, root.join("b"));
        let mut c = AuditedProvider::new(Fake(count.clone()), first, true, root.join("c"));
        let expected = a.complete(request("same")).await.unwrap();
        assert_eq!(b.complete(request("same")).await.unwrap(), expected);
        assert!(c.complete(request("different")).await.is_err());
        assert_eq!(count.load(Ordering::SeqCst), 1);
        assert_eq!(
            fs::read(root.join("a/call-0001/request.sha256")).unwrap(),
            fs::read(root.join("b/call-0001/request.sha256")).unwrap()
        );
        assert!(b.complete(request("second step")).await.is_ok());
        assert_eq!(count.load(Ordering::SeqCst), 2);
        assert!(write_new(&root.join("a/call-0001/request.json"), b"overwrite").is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn cache_stops_at_first_mismatch_and_keeps_branches() {
        let mut c = PrefixCache::default();
        assert_eq!(c.observe(&[1, 2, 3]), (0, 3));
        assert_eq!(c.observe(&[1, 9, 3]), (1, 2));
        assert_eq!(c.observe(&[1, 2, 3, 4]), (3, 1));
        assert_eq!(PrefixCache::default().observe(&[1, 2, 3]), (0, 3));
    }
}
