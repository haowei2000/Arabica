# Structure Real Task Browser Benchmark - 2026-05-20

## Environment

- Branch: `codex/deepseek-tool-browser-tests`
- API under test: source checkout on `http://127.0.0.1:8011` with embedded event worker
- MCP under test: source checkout on `http://127.0.0.1:9019/mcp`
- Browser path: Playwright Chromium, requests executed from the browser page context
- Model: DeepSeek V4 Pro, `deepseek-v4-pro`
- Model options: `reasoning_effort=high`, `extra_body.thinking.type=enabled`
- Workspace: `fb834b57-53ce-4138-8b28-ccfe69f897e1`
- Imported skill: `bench-real-task-20260520002529`
- Uploaded source file: `/chat/uploads/fbeb5ad6-1db5-466a-b650-5077c97554ba/benchmark-incident-20260520002529.md`
- Browser screenshot: `/tmp/structure-benchmark-20260520002529.png`

The benchmark used the real DeepSeek endpoint. Backend logs confirmed successful
`POST https://api.deepseek.com/chat/completions` calls for these runs.

## Setup Timings

| Step | Time |
| --- | ---: |
| Benchmark window from tool-list start to task completion | 110.957 s |
| Skill folder upload | 0.042 s |
| Skill context sync wait | 0.017 s |
| Workspace creation and context copy | 0.897 s |
| Workspace skill path verification | 0.102 s |
| Markdown file upload and parse | 0.531 s |

The workspace copy contained 8,253 context rows. The benchmark-window timing is
not a tool-list latency measurement; it spans from the initial tool-list step to
the end of the three primary task runs.

## Task Results

| Task | Run ID | Tool calls | Time | Input tokens | Output tokens | Total tokens | Events | Thinking events |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Skill marker read | `e7d0037d-3e17-4c77-9cff-661412451013` | `read_context` x1 | 24.402 s | 2,236 | 122 | 2,358 | 60 | 50 |
| File summary artifact | `c146a619-b29f-4c02-b746-8750774b3c50` | `read_context` x2, `create_artifact` x2 | 50.502 s | 15,769 | 1,477 | 17,246 | 339 | 317 |
| Long follow-up review | `b0537080-b937-4a67-87cf-bc105e7eb4ea` | `read_context` x2, `list_artifacts` x1 | 34.346 s | 6,125 | 936 | 7,061 | 428 | 414 |
| Direct artifact replay | `b41298c2-ecda-4fd8-803b-7a773bd6425e` | `read_artifact` x1, `read_context` x1 | 30.316 s | 7,395 | 818 | 8,213 | 470 | 458 |
| **Total** | - | **10 tool calls** | **139.566 s** | **31,525** | **3,353** | **34,878** | **1,297** | **1,239** |

## Notes

- DeepSeek thinking mode produced `agent.thinking` events in every task, and
  tool replay continued successfully after tool results.
- The file summary task created two artifacts with the same name. This is a
  real model behavior worth handling with idempotency or duplicate-call guards.
- The long follow-up task listed artifacts with the current run ID, so it did
  not see the artifacts created by the previous run. The direct artifact replay
  task then explicitly used the artifact ID and verified `read_artifact`.
- The highest token task was file summary plus artifact generation: 17,246 total
  tokens. The likely driver is repeated raw context/tool-result replay.
- This benchmark supports the proposed optimization direction: use indexed or
  compact summaries for repeated context instead of replaying full key summaries
  in long conversations.
