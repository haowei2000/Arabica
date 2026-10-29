//! Mutually authenticated TLS 1.3 for LRP connections.
//!
//! Both sides present a certificate and verify the peer against a pinned CA
//! bundle; neither trusts the system roots. The crypto provider is passed
//! explicitly because the workspace links more than one rustls backend, so a
//! process-wide default cannot be inferred.

use std::path::Path;
use std::sync::Arc;

use arabica_runner::RunnerError;
use rustls::server::WebPkiClientVerifier;
use rustls::{ClientConfig, RootCertStore, ServerConfig};
use rustls_pki_types::pem::PemObject;
use rustls_pki_types::{CertificateDer, PrivateKeyDer};

/// PEM-encoded material for one side of a connection.
#[derive(Clone)]
pub struct TlsIdentity {
    /// This side's certificate chain, leaf first.
    pub cert_chain_pem: Vec<u8>,
    /// This side's private key.
    pub private_key_pem: Vec<u8>,
    /// CA certificates the peer's certificate must chain to.
    pub peer_ca_pem: Vec<u8>,
}

impl std::fmt::Debug for TlsIdentity {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // The private key is never formatted.
        formatter
            .debug_struct("TlsIdentity")
            .finish_non_exhaustive()
    }
}

impl TlsIdentity {
    pub fn from_files(
        cert_chain: &Path,
        private_key: &Path,
        peer_ca: &Path,
    ) -> Result<Self, RunnerError> {
        let read = |path: &Path, what: &str| {
            std::fs::read(path).map_err(|error| {
                RunnerError::new(format!("cannot read {what} {}: {error}", path.display()))
            })
        };
        Ok(Self {
            cert_chain_pem: read(cert_chain, "certificate")?,
            private_key_pem: read(private_key, "private key")?,
            peer_ca_pem: read(peer_ca, "CA bundle")?,
        })
    }

    /// The runner side: requires and verifies a loop client certificate.
    pub fn server_config(&self) -> Result<Arc<ServerConfig>, RunnerError> {
        let provider = provider();
        let verifier =
            WebPkiClientVerifier::builder_with_provider(Arc::new(self.roots()?), provider.clone())
                .build()
                .map_err(tls_error)?;
        let config = ServerConfig::builder_with_provider(provider)
            .with_protocol_versions(&[&rustls::version::TLS13])
            .map_err(tls_error)?
            .with_client_cert_verifier(verifier)
            .with_single_cert(self.cert_chain()?, self.private_key()?)
            .map_err(tls_error)?;
        Ok(Arc::new(config))
    }

    /// A TLS acceptor for [`crate::RunnerHost::serve`].
    pub fn acceptor(&self) -> Result<tokio_rustls::TlsAcceptor, RunnerError> {
        self.server_config().map(tokio_rustls::TlsAcceptor::from)
    }

    /// The loop side: verifies the runner against the pinned CA and presents
    /// its own client certificate.
    pub fn client_config(&self) -> Result<Arc<ClientConfig>, RunnerError> {
        let config = ClientConfig::builder_with_provider(provider())
            .with_protocol_versions(&[&rustls::version::TLS13])
            .map_err(tls_error)?
            .with_root_certificates(self.roots()?)
            .with_client_auth_cert(self.cert_chain()?, self.private_key()?)
            .map_err(tls_error)?;
        Ok(Arc::new(config))
    }

    fn cert_chain(&self) -> Result<Vec<CertificateDer<'static>>, RunnerError> {
        let chain = CertificateDer::pem_slice_iter(&self.cert_chain_pem)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| RunnerError::new(format!("invalid certificate PEM: {error}")))?;
        if chain.is_empty() {
            return Err(RunnerError::new("certificate PEM holds no certificate"));
        }
        Ok(chain)
    }

    fn private_key(&self) -> Result<PrivateKeyDer<'static>, RunnerError> {
        PrivateKeyDer::from_pem_slice(&self.private_key_pem)
            .map_err(|_| RunnerError::new("invalid private key PEM"))
    }

    fn roots(&self) -> Result<RootCertStore, RunnerError> {
        let mut roots = RootCertStore::empty();
        for cert in CertificateDer::pem_slice_iter(&self.peer_ca_pem) {
            let cert =
                cert.map_err(|error| RunnerError::new(format!("invalid CA PEM: {error}")))?;
            roots.add(cert).map_err(tls_error)?;
        }
        if roots.is_empty() {
            return Err(RunnerError::new("CA bundle holds no certificate"));
        }
        Ok(roots)
    }
}

fn provider() -> Arc<rustls::crypto::CryptoProvider> {
    Arc::new(rustls::crypto::ring::default_provider())
}

fn tls_error(error: impl std::fmt::Display) -> RunnerError {
    RunnerError::new(format!("tls configuration: {error}"))
}
