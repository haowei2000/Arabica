//! The stdio transport for `arabica acp`, with a bounded shutdown.
//!
//! A client that closes stdin has ended the connection. The SDK then drains
//! outgoing messages before `serve` returns; this module lets that drain run
//! for a short grace period and then stops waiting, so a stdio child never
//! outlives its pipe whatever work is still pending.

use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Duration;

use agent_client_protocol::{ByteStreams, Result as AcpResult};
use tokio::io::{AsyncRead, ReadBuf};
use tokio::sync::watch;
use tokio_util::compat::{Compat, TokioAsyncReadCompatExt, TokioAsyncWriteCompatExt};

/// How long shutdown may continue after stdin reaches end of file.
pub(super) const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);

/// Reports when the wrapped reader reaches end of file.
pub(super) struct EofSignal<R> {
    inner: R,
    closed: watch::Sender<bool>,
}

impl<R> EofSignal<R> {
    pub(super) fn new(inner: R) -> (Self, watch::Receiver<bool>) {
        let (closed, receiver) = watch::channel(false);
        (Self { inner, closed }, receiver)
    }
}

impl<R: AsyncRead + Unpin> AsyncRead for EofSignal<R> {
    fn poll_read(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
        buffer: &mut ReadBuf<'_>,
    ) -> Poll<std::io::Result<()>> {
        let before = buffer.filled().len();
        let poll = Pin::new(&mut self.inner).poll_read(context, buffer);
        if let Poll::Ready(Ok(())) = &poll
            && buffer.filled().len() == before
            && buffer.remaining() > 0
        {
            self.closed.send_replace(true);
        }
        poll
    }
}

type StdioStreams = ByteStreams<Compat<tokio::io::Stdout>, Compat<EofSignal<tokio::io::Stdin>>>;

/// The process's stdin and stdout as an ACP transport, plus a signal that
/// fires when stdin closes.
pub(super) fn transport() -> (StdioStreams, watch::Receiver<bool>) {
    let (stdin, closed) = EofSignal::new(tokio::io::stdin());
    (
        ByteStreams::new(tokio::io::stdout().compat_write(), stdin.compat()),
        closed,
    )
}

/// Run `serve` to completion, or until `grace` after `closed` reports end of
/// file, whichever comes first.
pub(super) async fn serve_until_shutdown(
    serve: impl Future<Output = AcpResult<()>>,
    mut closed: watch::Receiver<bool>,
    grace: Duration,
) -> AcpResult<()> {
    let deadline = async move {
        if closed.wait_for(|closed| *closed).await.is_err() {
            // The reader is gone without reaching end of file; only `serve`
            // can end the connection.
            std::future::pending::<()>().await;
        }
        tokio::time::sleep(grace).await;
    };
    tokio::select! {
        result = serve => result,
        () = deadline => {
            eprintln!(
                "structure acp: stdin closed {} ms ago and shutdown has not finished; exiting without waiting for the remaining work",
                grace.as_millis()
            );
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn end_of_file_is_reported_once_the_reader_drains() {
        let (mut writer, reader) = tokio::io::duplex(64);
        let (mut reader, closed) = EofSignal::new(reader);
        writer.write_all(b"line\n").await.unwrap();
        let mut buffer = [0; 5];
        reader.read_exact(&mut buffer).await.unwrap();
        assert!(!*closed.borrow());
        drop(writer);
        assert_eq!(reader.read(&mut buffer).await.unwrap(), 0);
        assert!(*closed.borrow());
    }

    #[tokio::test]
    async fn stuck_shutdown_ends_one_grace_period_after_end_of_file() {
        let (sender, closed) = watch::channel(false);
        let started = tokio::time::Instant::now();
        let stuck = std::future::pending::<AcpResult<()>>();
        let run = tokio::spawn(serve_until_shutdown(
            stuck,
            closed,
            Duration::from_millis(50),
        ));
        tokio::time::sleep(Duration::from_millis(20)).await;
        assert!(!run.is_finished(), "an open connection is never cut short");
        sender.send_replace(true);
        run.await.unwrap().unwrap();
        assert!(started.elapsed() >= Duration::from_millis(70));
    }

    #[tokio::test]
    async fn a_finished_connection_returns_its_own_result() {
        let (_sender, closed) = watch::channel(false);
        let failed = async { Err(agent_client_protocol::Error::internal_error()) };
        assert!(
            serve_until_shutdown(failed, closed, Duration::from_secs(60))
                .await
                .is_err()
        );
    }
}
