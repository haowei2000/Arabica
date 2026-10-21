# Session metadata for ACP clients

Session history and titles remain authoritative in the Rust session store.
Clients may offer local project and sidebar preferences without duplicating
the runtime or writing directly to its database.

## Rename a conversation

The ACP initialize response advertises the optional `arabica.session` extension
under `_meta`:

```json
{"arabica.session":{"schemaVersion":1,"rename":"_arabica/session/rename","maxTitleLength":200}}
```

Clients must check this capability before enabling durable rename. The typed
request and response use camelCase:

```json
{"jsonrpc":"2.0","id":1,"method":"_arabica/session/rename","params":{"sessionId":"ACP_SESSION_ID","title":"A useful conversation title"}}
{"jsonrpc":"2.0","id":1,"result":{"sessionId":"ACP_SESSION_ID","title":"A useful conversation title"}}
```

The returned title is the committed value, with leading and trailing whitespace
removed. Titles must contain 1 to 200 Unicode characters after trimming and no
control characters. Invalid IDs, unknown sessions, ambiguous IDs, malformed
parameters, and invalid titles return invalid-params errors. Store or ownership
failures return an internal error; clients must keep their previous title when
the request fails.

Rename works on sessions registered to this connection and historical sessions
listed by the same configured session store. Historical rename acquires normal
exclusive writer ownership and cannot modify a session held by another ACP
connection. It does not load or resume the session, construct or call a model,
append events, or alter its working directory. It preserves the canonical
store's title-update behavior, including updating its activity timestamp.
Renaming a registered session does not acquire its runtime manager lock, so it
can complete while that session is running.

This extension only changes a display title. ACP `session/close` continues to
suspend and release a connection's session while preserving its history; it
does not archive or delete the session.
