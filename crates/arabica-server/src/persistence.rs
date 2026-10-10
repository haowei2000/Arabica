//! Server composition of backend-independent session persistence ports.
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use arabica_protocol::{EventEnvelope, SessionId};
use arabica_session::{EventVisibility, SessionEventObserver};
use arabica_session::{NewSession, SessionStore, SessionWriter, StoreError};

#[derive(Debug)]
pub(crate) struct Persistence {
    pub(crate) repository: Arc<dyn SessionStore>,
    pub(crate) cwd: PathBuf,
    pub(crate) stores: Mutex<BTreeMap<SessionId, Arc<dyn SessionWriter>>>,
    pub(crate) failure: Mutex<Option<String>>,
}

impl Persistence {
    fn append(
        &self,
        envelope: &EventEnvelope,
        visibility: EventVisibility,
    ) -> Result<(), StoreError> {
        let mut stores = self.stores.lock().expect("store registry poisoned");
        let store = match stores.get(&envelope.session_id) {
            Some(store) => Arc::clone(store),
            None => {
                let store = self.repository.create(NewSession {
                    session_id: &envelope.session_id,
                    workspace_id: &envelope.workspace_id,
                    cwd: &self.cwd,
                    profile: None,
                    instructions_sha256: None,
                })?;
                stores.insert(envelope.session_id.clone(), Arc::clone(&store));
                store
            }
        };
        store.append(envelope, visibility)
    }
}

impl SessionEventObserver for Persistence {
    fn observe(&self, envelope: &EventEnvelope, visibility: EventVisibility) {
        if let Err(error) = self.append(envelope, visibility) {
            *self
                .failure
                .lock()
                .expect("persistence failure lock poisoned") = Some(error.to_string());
        }
    }
}

#[derive(Debug)]
pub(crate) struct UuidIds;

impl arabica_session::IdAllocator for UuidIds {
    fn session_id(&mut self) -> SessionId {
        SessionId::new(uuid::Uuid::now_v7().to_string())
    }
    fn run_id(&mut self) -> arabica_protocol::RunId {
        arabica_protocol::RunId::new(uuid::Uuid::now_v7().to_string())
    }
}
