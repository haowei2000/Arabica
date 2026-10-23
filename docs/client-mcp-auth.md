# Dynamic authentication for remote MCP servers

Static `headers` on an HTTP MCP server are sent unchanged for the life of an
ACP session. A platform that rotates or revokes credentials can instead
declare a dynamic mode. Arabica then resolves the access token for every HTTP
request. A rotated credential is used by the next request on the same MCP
connection, with no new `session/new`, restore, or local proxy.

| Mode | Declared by | Who holds refresh tokens |
| --- | --- | --- |
| `broker` | ACP client only | The ACP client or its credential broker |
| `oauth` | ACP client or `config.toml` | Arabica, under `$ARABICA_HOME/mcp-oauth` |
| `command` | `config.toml` only | The external program |

A declared mode never falls back to an unauthenticated request. If no
credential is available, the server's connection or tool call fails with an
actionable error.

## ACP capability

The initialize response advertises the extension under `_meta`:

```json
{"arabica.mcpAuth":{"schemaVersion":1,"metaKey":"arabica.auth","modes":["broker","oauth"],"credential":"_arabica/mcp/credential"}}
```

Standard ACP `McpServerHttp` fields cannot install a token callback, so the
mode goes in the server's `_meta` under `arabica.auth`. Clients must check
the capability before sending it; an older Arabica connects such a server
with static headers only.

## Broker mode

```json
{
  "type": "http",
  "name": "crm",
  "url": "https://mcp.example.com/mcp",
  "headers": [{"name": "X-Tenant", "value": "acme"}],
  "_meta": {"arabica.auth": {"mode": "broker", "binding": "acct_42"}}
}
```

`binding` is an opaque reference, 1 to 200 characters, that the client uses
to find the account or grant. It is not a secret. When Arabica needs a token,
it sends the client a request:

```json
{"jsonrpc":"2.0","id":7,"method":"_arabica/mcp/credential","params":{"server":"crm","url":"https://mcp.example.com/mcp","binding":"acct_42","reason":"initial"}}
```

`reason` is one of the following:

- `initial`: the first token for this connection.
- `expiring`: the cached token is within 30 seconds of the expiry the client
  reported.
- `rejected`: the MCP server answered 401 to the cached token.

The client answers with one of these outcomes:

```json
{"jsonrpc":"2.0","id":7,"result":{"outcome":"granted","accessToken":"…","expiresInSeconds":300}}
{"jsonrpc":"2.0","id":7,"result":{"outcome":"needsAuthorization","message":"Reconnect the CRM account"}}
{"jsonrpc":"2.0","id":7,"result":{"outcome":"unavailable","message":"Credential service is unavailable"}}
```

The client may omit `expiresInSeconds`, in which case Arabica uses the token
until the server rejects it. A `message` is shown to the user, truncated to
200 characters, and must not contain secrets. A JSON-RPC error, a malformed
reply, or no reply within 30 seconds counts as `unavailable`.

The broker must return only tokens issued for this MCP server by its
authorization server: Arabica cannot check a token's audience, and MCP
forbids sending a server any other token. The broker keeps refresh tokens
and upstream secrets. Arabica sees only the
short-lived access token it sends as `Authorization: Bearer …`. This is a
trusted Agent boundary, not a claim that the agent cannot read that token.
Arabica never sends a rejected token back to the client, never puts tokens
in prompts, tool results, or diagnostics, and never logs them.

The first token is requested while `session/new`, `session/load`, or
`session/resume` is still pending, before MCP discovery starts. The client
must answer `_arabica/mcp/credential` while one of those requests is
outstanding.

## OAuth mode

```json
"_meta": {"arabica.auth": {"mode": "oauth", "binding": "work", "clientId": "optional-preregistered-id", "scopes": ["mcp"]}}
```

`clientMetadataUrl` may be given instead of `clientId`: an HTTPS URL, with a
path, of a Client ID Metadata Document that the platform hosts.

The grant must already exist. Create it once on the agent's machine:

```sh
arabica mcp login --url https://mcp.example.com/mcp --binding work
```

The flow follows the MCP 2026-07-28 authorization specification, and rmcp's
authorization manager carries out the OAuth steps:

- An unauthenticated request obtains the server's `WWW-Authenticate`
  challenge. Its `resource_metadata` and `scope` guide discovery.
- The authorization server must be found through RFC 9728 Protected
  Resource Metadata. Login fails rather than guess endpoints when a server
  publishes none.
- The client is identified, in order, by the given client ID, a Client ID
  Metadata Document when the server supports them, or dynamic registration.
- Scopes come from the challenge when it names any, otherwise from
  `scopes_supported`. Scopes the grant already holds are always kept, along
  with any `--scope` given.
- PKCE uses S256, with `state` and a loopback redirect. The RFC 8707
  `resource` parameter is sent on authorization and token requests.
- The RFC 9207 `iss` response parameter is checked against the recorded
  issuer before the code is exchanged. A missing `iss` is rejected when the
  server advertises it. The grant is stored owner-only in a file keyed by the canonical
resource URL and the binding, so two bindings for the same URL never share
a token. Refreshes take a file lock, so sessions and processes that share a
grant do not refresh it at the same time. When the issuer changes, the
stored tokens are discarded.

When the grant is missing or the refresh token is revoked, the connection or
tool call fails with `MCP authorization required` and the login command to
run. A 403 `insufficient_scope` names the required scope and the step-up
command, for example `arabica mcp login search --scope 'files:write'`; the
new grant keeps the old scopes. `arabica mcp logout` deletes a grant.

## User config

```toml
[[mcp]]
name = "search"
url = "https://mcp.example.test/mcp"

[mcp.auth]
mode = "oauth"          # then run: arabica mcp login search
binding = "work"        # optional, defaults to "default"
client_id = "..."       # optional; or client_metadata_url = "https://…"
scopes = ["mcp"]        # optional

[[mcp]]
name = "internal"
url = "https://mcp.internal.test/mcp"

[mcp.auth]
mode = "command"
command = ["/usr/local/bin/mcp-token", "--account", "work"]
```

A credential command prints one of the outcome objects above on stdout. It
receives `ARABICA_MCP_SERVER`, `ARABICA_MCP_URL`, `ARABICA_MCP_BINDING`, and
`ARABICA_MCP_REASON`, plus a minimal base environment. Arabica discards its
stderr. A non-zero exit status counts as `unavailable`.

An `[mcp.auth]` table applies only to `url` servers, and it cannot be
combined with an `Authorization` entry in `headers`. Other custom headers
are still sent.

## Retry boundary

- Only an HTTP 401 is retried. Arabica retries once, with a replacement
  credential, and only when the replacement differs from the rejected
  token. The server refused that request before handling it, so replaying
  it cannot run a tool twice.
- Timeouts, dropped connections, other HTTP statuses, and failures partway
  through an SSE response are never replayed, because the server may already
  have run the request.
- A 403 `insufficient_scope` is reported, not retried.
- Concurrent requests that find the token expired or rejected share one
  credential request. Six parallel tool calls after an expiry cause exactly
  one `expiring` request.

## Protocol behaviour

Authentication does not change the MCP lifecycle. Arabica still prefers MCP
2026-07-28 and falls back to the legacy initialization handshake only for a
server that rejects `server/discover` in the legacy way or does not answer it
in time. A 401 or 403 never counts as a legacy server. The first credential is
resolved before discovery starts, so a slow credential provider cannot run
out the discovery deadline and cause a downgrade. Protocol headers and the same-origin 307/308
redirect policy stay SDK-owned, so a bearer token never follows a
cross-origin redirect.

## Scope

Credential state belongs to one MCP connection in one ACP session, keyed by
server and binding. OAuth grants are shared only by sessions that use the
same resource URL and binding.

Per-task attribution is a separate concern. A platform that needs per-task
identity should encode it in the binding or token it issues; OAuth alone
does not provide it.
