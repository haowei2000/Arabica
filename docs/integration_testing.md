# Integration Testing Guide

This guide defines how Structure integration tests should be executed, what flows
must be covered, and which artifacts must be persisted after every run.

## Goals

Integration testing must validate the real user experience across the full
system boundary:

- Real Docker services, not mocked infrastructure.
- Real browser interaction with the frontend.
- Real API, database, Redis, object storage, and worker behavior.
- Real chat execution, including streaming, context usage, token accounting, and
  run lifecycle transitions.

Unit tests and mocked component tests are useful, but they are not substitutes
for this integration test suite.

## Test Environment

Run integration tests against the same service topology used in local
deployment. The expected environment is:

- Docker Compose for infrastructure and application services.
- PostgreSQL with pgvector enabled.
- Redis for streams, cache, and worker coordination.
- S3-compatible storage such as RustFS or MinIO.
- Backend API service.
- Worker service.
- Frontend service.
- A real browser driven by Playwright, browser-use, or an equivalent browser
  automation tool.
- A real LLM provider configuration for chat tests.

Do not replace PostgreSQL, Redis, storage, the backend, the worker, or the
browser with in-memory mocks. The purpose of these tests is to catch failures at
the seams between services, browser behavior, streaming, persistence, and
runtime orchestration.

### Environment Preparation

Before each test session:

1. Start the Docker environment from a clean baseline.
2. Apply database migrations.
3. Verify API health and frontend availability.
4. Verify Redis and storage connectivity.
5. Confirm worker registration and event processing are active.
6. Confirm the configured LLM provider is reachable.
7. Create or reset a dedicated integration-test tenant, user, workspace, and
   application data set.

Recommended local targets:

| Service | Default Target |
| --- | --- |
| Frontend | `http://localhost:3000` |
| API | `http://localhost:8000` |
| MCP | `http://localhost:9000` |
| PostgreSQL | `localhost:5432` |
| Redis | `localhost:6379` |
| Object storage | `localhost:9000` / `localhost:9001` |

Use a dedicated test account and dedicated test data. Do not run destructive
integration tests against shared production-like data.

## Test Flow

Each full integration run should execute the following flow in order.

### 1. Preflight

Validate that the environment is usable before opening the main browser flow:

- Docker containers are running.
- API health endpoint succeeds.
- Frontend loads without console errors.
- Database migrations are current.
- Worker process can consume Redis stream events.
- Object storage bucket is available.
- Test user credentials are valid.

Persist preflight results as structured artifacts even when the test fails.

### 2. Login

Use the browser to test the real login flow:

- Open the frontend login page.
- Submit valid integration-test credentials.
- Verify successful navigation to the authenticated application area.
- Verify the session survives a browser refresh.
- Verify authenticated API requests are made with the expected token.
- Test invalid credentials and verify the error state is visible and safe.

### 3. Feature CRUD Coverage

For every major user-facing resource, test create, read, update, and delete
behavior through the browser where possible. API-only setup is allowed only for
test fixture preparation.

| Feature Area | Required Coverage |
| --- | --- |
| Workspace | Create workspace, update metadata, open detail view, delete or archive |
| App / Agent | Create app, configure executor/model, update settings, delete |
| LLM Model Config | Create or select provider config, verify availability, update display fields |
| Knowledge | Upload document, wait for parsing, inspect chunks or status, delete |
| Memory | Create memory item, update content/tags, verify retrieval, delete |
| Skill | Create skill entry, update metadata/content, verify listing, delete |
| Tool | Create or register tool, update config, validate listing, delete |
| Context Tree | Verify context paths, search/list/read context, update and remove entries |
| Event History | Verify run events are visible and ordered |

Each CRUD scenario should assert:

- The UI reflects the operation.
- The API response is successful.
- The persisted state is visible after reload.
- Deletion removes or marks the resource according to product behavior.
- Error states are visible and do not corrupt existing data.

### 4. Core Chat Effect Test

The chat test is the highest-priority integration scenario. It should validate
that a user can complete a useful conversation through the real frontend, real
backend, real worker, and real LLM provider.

Required chat scenarios:

1. Basic chat
   - Send a simple prompt.
   - Verify streaming starts quickly.
   - Verify the final answer is rendered.
   - Verify the run reaches a terminal success state.

2. Multi-turn continuity
   - Ask an initial question.
   - Ask a follow-up that depends on prior context.
   - Verify the answer uses the previous turn correctly.

3. Context-grounded chat
   - Upload or create a known test document.
   - Ask a question that requires using that document.
   - Verify the answer cites or reflects the uploaded content accurately.

4. Tool-assisted chat
   - Trigger a task that requires a registered tool.
   - Verify tool-call events appear.
   - Verify tool results are incorporated into the final answer.

5. Failure and recovery
   - Trigger a controlled invalid input or unavailable resource.
   - Verify the UI shows a useful error.
   - Verify the next valid chat request still succeeds.

Chat quality should be evaluated using both deterministic checks and human
review notes:

- Relevance to the user prompt.
- Correct use of workspace context.
- Correct tool selection when tools are needed.
- No obvious hallucinated project facts.
- Clear final answer.
- Stable multi-turn behavior.
- Reasonable latency and token usage.

## Required Persisted Test Artifacts

Every integration test run must write an artifact directory. Artifacts are part
of the test result, not optional debug output.

Recommended directory format:

```text
tests/artifacts/integration/YYYYMMDD-HHMMSS_<scenario>/
```

Required files:

| Artifact | Purpose |
| --- | --- |
| `summary.json` | Overall status, scenario name, git commit, start/end time, pass/fail |
| `environment.json` | Docker image tags, service URLs, enabled providers, browser version |
| `steps.jsonl` | One record per test step with status, duration, and error details |
| `conversation.jsonl` | Full chat transcript with user messages, assistant messages, tool calls, and tool results |
| `token_usage.json` | Per-run and total input, output, and total token counts |
| `timings.json` | Login time, CRUD operation timings, chat first-token latency, total chat duration |
| `run_events.jsonl` | Persisted event stream records for the tested chat runs |
| `browser_trace.zip` | Browser trace for replaying UI behavior |
| `screenshots/` | Key screenshots, including failures |
| `docker_logs/` | API, worker, frontend, Redis, and database logs relevant to the run |

The minimum required chat metrics are:

- Full conversation record.
- Total token usage.
- Per-message token usage when available.
- Total elapsed chat duration.
- First-token latency.
- Run ID.
- Workspace ID.
- Model/provider identifier.
- Tool calls and tool results.
- Final run status.

Do not store secrets in artifacts. Redact API keys, JWTs, database passwords,
storage credentials, and user passwords before writing logs.

## Pass / Fail Criteria

A full integration run passes only when:

- The real Docker environment starts successfully.
- Browser login succeeds.
- Required CRUD flows pass.
- The core chat scenarios complete successfully.
- Chat transcripts and metrics are persisted.
- No unhandled frontend console errors occur during required flows.
- No backend or worker crash occurs.
- Every failed assertion has a persisted screenshot, trace, and structured error
  record.

If artifact persistence fails, the test run must be marked as failed even if the
UI behavior appears correct.

## Recommended Execution Pattern

Use one top-level integration runner that performs:

1. Environment bootstrap.
2. Browser session setup.
3. Login.
4. Fixture creation.
5. CRUD scenario execution.
6. Chat scenario execution.
7. Artifact collection.
8. Cleanup or data retention according to the test policy.

Cleanup should remove temporary test resources only after artifacts are safely
written. For failed runs, preserve enough data to debug the failure.

