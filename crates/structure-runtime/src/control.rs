//! Per-run control supplied by the host: cooperative cancellation today, the
//! permission gate next.
//!
//! A host cannot cancel a run by sending `run.cancel` while the run executes:
//! Session Management holds one borrow for the whole `message.send`, so the
//! Command would only be processed after the run ended. Control therefore
//! travels with the run as a shared handle the host can signal at any time.

use std::sync::Arc;

use tokio::sync::watch;

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

/// Everything a host may attach to one run.
///
/// The default attaches nothing, and a run executed with it emits exactly the
/// Events it emitted before control existed. Recorded benchmark campaigns
/// depend on that.
#[derive(Clone, Debug, Default)]
pub struct RunControl {
    pub cancellation: Option<RunCancellation>,
}

impl RunControl {
    pub fn is_cancelled(&self) -> bool {
        self.cancellation
            .as_ref()
            .is_some_and(RunCancellation::is_cancelled)
    }
}
