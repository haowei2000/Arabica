//! SQLite composition of the backend-independent session persistence ports.
use std::path::PathBuf;
use std::sync::Arc;

use arabica_protocol::{EventEnvelope, SessionId, WorkspaceId};
use arabica_session::{
    EventVisibility, NewSession, SessionListing, SessionStore, SessionWriter, StoreError,
    StoredSession,
};

use crate::SqliteSessionStore;

/// Repository configuration; construction does not open the database.
#[derive(Clone, Debug)]
pub struct SqliteSessionRepository {
    home: PathBuf,
}

impl SqliteSessionRepository {
    pub fn new(home: impl Into<PathBuf>) -> Self {
        Self { home: home.into() }
    }
}

impl SessionStore for SqliteSessionRepository {
    fn create(&self, session: NewSession<'_>) -> Result<Arc<dyn SessionWriter>, StoreError> {
        Ok(Arc::new(SqliteSessionStore::create(&self.home, session)?))
    }

    fn open(
        &self,
        workspace_id: &WorkspaceId,
        session_id: &SessionId,
    ) -> Result<Arc<dyn SessionWriter>, StoreError> {
        // Validate at the ID boundary before constructing backend locators.
        crate::file_session_store::validate_path_component(
            &workspace_id.to_string(),
            "workspace_id",
        )?;
        crate::file_session_store::validate_path_component(&session_id.to_string(), "session_id")?;
        let path = SqliteSessionStore::session_path(&self.home, workspace_id, session_id);
        Ok(Arc::new(SqliteSessionStore::open_existing(&path)?))
    }

    fn read(
        &self,
        workspace_id: &WorkspaceId,
        session_id: &SessionId,
    ) -> Result<StoredSession, StoreError> {
        SqliteSessionStore::read_session(&self.home, workspace_id, session_id)
    }

    fn list(&self, workspace_id: Option<&WorkspaceId>) -> Result<Vec<SessionListing>, StoreError> {
        Ok(SqliteSessionStore::list_sessions(&self.home, workspace_id)?
            .into_iter()
            .map(|listing| SessionListing {
                header: listing.header,
                updated_at_ms: listing.modified_at_ms,
            })
            .collect())
    }
}

impl SessionWriter for SqliteSessionStore {
    fn append(
        &self,
        envelope: &EventEnvelope,
        visibility: EventVisibility,
    ) -> Result<(), StoreError> {
        SqliteSessionStore::append(self, envelope, visibility)
    }

    fn set_title(&self, title: &str) -> Result<(), StoreError> {
        SqliteSessionStore::set_title(self, title)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arabica_protocol::{CommandId, Event, EventId, PROTOCOL_VERSION};
    use arabica_session::SessionEventObserver;

    #[test]
    fn repository_port_preserves_writer_ownership_and_metadata_across_instances() {
        let home = std::env::temp_dir().join(format!(
            "arabica-repository-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let repository: Arc<dyn SessionStore> = Arc::new(SqliteSessionRepository::new(&home));
        let other: Arc<dyn SessionStore> = Arc::new(SqliteSessionRepository::new(&home));
        let workspace = WorkspaceId::new("workspace");
        let session = SessionId::new("session");
        let writer = repository
            .create(NewSession {
                session_id: &session,
                workspace_id: &workspace,
                cwd: std::path::Path::new("/workspace"),
                profile: None,
                instructions_sha256: None,
            })
            .unwrap();
        let observer: Arc<dyn SessionEventObserver> =
            Arc::clone(&writer) as Arc<dyn SessionEventObserver>;
        drop(writer);
        assert!(other.open(&workspace, &session).is_err());
        observer.observe(
            &EventEnvelope {
                protocol_version: PROTOCOL_VERSION.to_owned(),
                event_id: EventId::new("event"),
                command_id: CommandId::new("create"),
                workspace_id: workspace.clone(),
                session_id: session.clone(),
                run_id: None,
                sequence: 1,
                occurred_at_ms: 0,
                event: Event::SessionCreated {
                    workspace_id: workspace.clone(),
                },
            },
            EventVisibility::Client,
        );
        assert_eq!(other.read(&workspace, &session).unwrap().events.len(), 1);
        drop(observer);
        let writer = other.open(&workspace, &session).unwrap();
        writer.set_title("Saved title").unwrap();
        let listing = repository.list(Some(&workspace)).unwrap();
        assert_eq!(listing[0].header.title.as_deref(), Some("Saved title"));
        assert_eq!(listing[0].header.id, session);
        assert!(listing[0].updated_at_ms.is_some());
        assert!(
            other
                .open(&workspace, &SessionId::new("../escape"))
                .is_err()
        );
        drop(writer);
        drop(repository);
        drop(other);
        std::fs::remove_dir_all(home).unwrap();
    }
}
