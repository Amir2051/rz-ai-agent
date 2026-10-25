# RZ AI

A small FastAPI service for a local personal AI assistant, with chat requests routed through OpenRouter.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `RZ_API_KEY` and `OPENROUTER_API_KEY`. Keep `.env` private; never commit it.

## Run locally

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8788
```

- Health: `http://127.0.0.1:8788/health`
- Interactive API docs: `http://127.0.0.1:8788/docs`
- Chat: `POST http://127.0.0.1:8788/v1/chat` with JSON `{"message":"Hello"}` and an `X-Api-Key` header matching `RZ_API_KEY`.

The hosted default is `nvidia/nemotron-3.5-lightning:free`, with `openrouter/free` as a one-time fallback for ordinary chat when the chosen endpoint is blocked or unavailable. OpenRouter routing privacy is controlled by `OPENROUTER_ZDR` (default `false` for the current testing configuration) and provider failover by `OPENROUTER_ALLOW_FALLBACKS` (default `true`). If ZDR is disabled, prompts may be processed under OpenRouter and the selected provider’s standard data policies; check those policies before using sensitive data.

The service binds to loopback in the example command so it is not exposed to the network by default.

Hermes is an optional provider/fallback configured with `HERMES_API_KEY` and
`HERMES_MODEL` (default `upstage/solar-mini4:free`). Use a valid server-side Nous
inference API key; do not copy a short-lived CLI OAuth/agent token into Render.
When configured, provider order is OpenRouter, Hermes, then Ollama, skipping any
provider that is not configured.

An optional Ollama fallback can be enabled with `OLLAMA_BASE_URL` and
`OLLAMA_MODEL` (default `qwen3:1.7b`). If the endpoint requires authentication, set
`OLLAMA_API_KEY`; it is sent only as a server-side Bearer header. A hosted Render
service cannot reach `127.0.0.1` on a user's Kali computer; hosted use requires a
secure, authenticated private connection. Do not expose port 11434 publicly.


## Optional task-aware model routing

The hosted default remains `OPENROUTER_MODEL`. You can optionally set
`OPENROUTER_CODING_MODEL` and/or `OPENROUTER_ANALYSIS_MODEL` to model IDs that
you have verified are available under your OpenRouter account’s allowed-provider and privacy settings. When configured,
simple keyword-based routing selects those models for coding/debugging or
analysis/research requests. An explicit `model` in a chat request takes
precedence. If either optional variable is empty, requests in that category
continue to use the default model.

Cartesia voice synthesis uses `CARTESIA_API_KEY`, `CARTESIA_VOICE_ID`, `CARTESIA_TTS_MODEL`, and `CARTESIA_VERSION`; keep the API key in an ignored local `.env` and the hosted service secret store, never in source control. The live transcription connection remains separate from MIA’s chat model. OpenRouter requests read `provider.zdr` and `provider.allow_fallbacks` from the corresponding environment variables. Set `OPENROUTER_ZDR=true` only if the selected model has an available endpoint that meets your ZDR requirement. Keep model IDs compatible with the provider allowlist configured in your OpenRouter account. If a valid Hermes key is configured, Hermes is attempted after an OpenRouter failure; configured Ollama is the next fallback.


## Runtime capabilities and session cleanup

Authenticated clients can query `GET /v1/capabilities` to inspect the active provider,
default model, whether optional task-specific routes are configured, privacy request
flags, and session limits. This endpoint never returns API keys, prompts, or chat content.

Idle in-memory conversations are removed after `SESSION_TTL_SECONDS` (default: one hour)
when sessions are accessed or listed. This is memory cleanup, not durable storage:
conversation history still does not survive a process restart.


## Model Context Protocol (MCP) tools

RZ Agent exposes an authenticated MCP JSON-RPC endpoint at `POST /mcp` using the
Streamable HTTP request shape. It reuses the existing RZ Agent authentication: send
`X-API-Key: <RZ_API_KEY>` in local API-key mode, or use the signed-in account session
when running in account-auth mode. Do not put API keys in tool arguments or commit them
to client configuration files.

Available tools:

- `get_runtime_status`: safe provider/model routing and privacy-flag summary; no secrets or chat content.
- `list_conversations`: active conversation IDs for the authenticated user only.
- `chat`: send a prompt through the existing configured model and provider policy.
- `reset_conversation`: delete one of the authenticated user's active in-memory conversations.

MCP does not add filesystem access, shell execution, or external app integrations. Web research is exposed separately through the authenticated `/v1/research` endpoint; it uses Brave Search when `BRAVE_SEARCH_API_KEY` is configured and otherwise attempts a best-effort DuckDuckGo HTML fallback, which may be rate-limited. The existing OpenRouter Zero Data Retention request and disabled provider fallbacks remain unchanged.

## Capability workflows

The browser UI includes authenticated workflows for document analysis, code review, memory save/list/delete, and task-plan creation/listing/step updates. Code review analyzes submitted code and does not execute it. Document uploads are limited to 3 MB and supported text, source, CSV, DOCX, and PDF formats; extracted content is bounded before it is sent to the configured model. Task plans track steps and statuses but do not autonomously execute them.

Memory and task data use PostgreSQL when `DATABASE_URL` is configured. Without it, the service falls back to local SQLite at `RZ_LOCAL_DATA_PATH` (or `~/.rz-ai-agent/capabilities.sqlite3`). Local SQLite is suitable for development but may not survive restarts or replacements on an ephemeral hosting service; use a persistent PostgreSQL database for durable hosted data. The `/v1/tools` and `/v1/capabilities` endpoints report configuration status without exposing credentials.

Image generation uses OpenRouter's unified Image API (`POST https://openrouter.ai/api/v1/images`) with `OPENROUTER_API_KEY`. The default model is `bytedance-seed/seedream-4.5` (image generations are paid; OpenRouter's model catalog and endpoint pricing can change). Optionally override `IMAGE_GENERATION_ENDPOINT`, `IMAGE_GENERATION_API_KEY`, and `IMAGE_GENERATION_MODEL`; custom endpoints must use HTTPS and return the OpenRouter-style `data[0].b64_json` or an HTTPS image URL. The endpoint reports provider errors honestly and returns the model and usage cost when supplied.

MCP initialize example (replace the key locally; never paste a real key into shared logs):

```bash
curl -sS http://127.0.0.1:8788/mcp \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_RZ_API_KEY' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"mcp-client","version":"1.0"}}}'
```

## Read-only GitHub MCP tools

The authenticated `/mcp` endpoint also exposes four GitHub read-only tools, restricted to repositories owned by `Amir2051`:

- `github_get_repository` — repository metadata.
- `github_list_issues` — issue summaries, excluding pull requests.
- `github_list_pull_requests` — pull-request summaries.
- `github_get_file_contents` — UTF-8 text file content (maximum 30,000 characters).

Public repositories can be read without a GitHub token. To read private repositories, configure `GITHUB_TOKEN` as a server-side environment secret with read-only repository/content permissions. Never pass the token in MCP arguments or commit it. The integration does not expose GitHub write operations, arbitrary owners, or shell access. Issue/PR list calls are limited to 20 results per request.

Example MCP tool call after authentication:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"github_get_repository","arguments":{"repo":"rz-ai-agent"}}}
```

## Capability endpoints (authenticated)

- `GET /v1/tools` reports available capabilities and explicit limitations.
- `POST /v1/research` accepts `{ "query": "...", "limit": 5 }` and returns bounded web search results. Configure `BRAVE_SEARCH_API_KEY` for reliable search; an unauthenticated DuckDuckGo HTML attempt is best-effort and returns an explicit error if blocked. Explicit prompts such as “search the web” or “research online” include retrieved snippets in chat synthesis. Search results are untrusted and should be verified at source.
- `POST /v1/investment/research` accepts `{ "query": "...", "limit": 4 }` and runs three bounded searches covering primary company/fund sources, recent developments, and independent risk analysis. It returns source-linked groups, a research timestamp, and limitations. Search snippets are leads, not live quotes or verified financial statements. Configure `BRAVE_SEARCH_API_KEY` for more reliable hosted search. Investment requests are routed through MIA's bounded agent tools; they do not execute trades or guarantee a buy/sell signal.
- `POST /v1/investment/quotes` accepts `{ "symbols": ["AAPL", "SPY"] }` and returns quote metadata, source URLs, market timestamps, quote age, and a stale flag. The initial connector uses the Yahoo Finance chart endpoint as a prototype third-party source, not a guaranteed real-time or licensed market-data feed. Users must verify timestamps and data before making decisions; there are no trading operations.
- `POST /v1/images/generations` accepts `{ "prompt": "..." }` and uses the shared `OPENROUTER_API_KEY` with `POST https://openrouter.ai/api/v1/images` by default. Default model: `bytedance-seed/seedream-4.5`; every generated image may incur a charge, so check current model pricing before use. You can override `IMAGE_GENERATION_ENDPOINT`, `IMAGE_GENERATION_API_KEY`, and `IMAGE_GENERATION_MODEL`. The UI displays the actual returned image and reports model/cost metadata when present; provider errors are not faked.
- `POST /v1/documents/analyze` accepts `{ "filename": "notes.pdf", "content_base64": "...", "question": "Summarize this" }`. Upload limit is 3 MB; extracted text is capped at 40,000 characters. Supported formats include TXT, Markdown, source code, JSON, CSV, DOCX, and PDF. PDF extraction uses `pypdf`.
- `POST /v1/code/review` accepts `{ "language": "python", "code": "...", "question": "Review this" }`. This analyzes supplied code but never executes it.
- `GET/POST/DELETE /v1/memory` list, save, and delete explicitly saved user memories. In account-auth mode, records are scoped to the authenticated account and use the configured PostgreSQL database. Local API-key mode stores memory in SQLite at `RZ_LOCAL_DATA_PATH` or `~/.rz-ai-agent/capabilities.sqlite3`.
- `GET/POST /v1/tasks` and `PATCH /v1/tasks/{task_id}/steps/{step_index}` persist bounded task plans and track step status. These are task tracking primitives, not an unrestricted background agent or shell executor.

All endpoints require the existing API-key or account authentication. No arbitrary shell execution or write-capable GitHub tools are added. OpenRouter routing preferences are configured with `OPENROUTER_ZDR` and `OPENROUTER_ALLOW_FALLBACKS`. The existing conversation history remains in memory; explicit saved memory and task records are stored separately.


## MCP access for external clients

The deployed service supports authenticated MCP at `/mcp`. For external MCP clients, create a scoped bearer token from the signed-in account endpoints documented in `AUTHENTICATION.md`. Apply `database/migrations/002_api_tokens.sql` after the existing auth migration. Tokens are revocable and stored hashed. Available MCP tools include runtime status, conversations/chat, and read-only GitHub repository/issue/PR/file operations; the approved REST capability endpoints cover research, memory, tasks, document analysis, code review, and image generation. There is no arbitrary shell execution or unrestricted filesystem tool.

## Slack CLI workflow (local development)

MIA's MCP & Plugins page includes a Slack CLI quickstart. From this repository root:

```bash
./scripts/slack-cli-workflow.sh check
./scripts/slack-cli-workflow.sh install  # only if missing; asks before running installer
./scripts/slack-cli-workflow.sh login
./scripts/slack-cli-workflow.sh create
cd <your-new-slack-project>
/path/to/rz-ai-agent/scripts/slack-cli-workflow.sh run
```

The helper checks for the `slack` executable, downloads the official installer to a temporary file before asking for confirmation, and delegates interactive login/project creation/running to Slack CLI. It does not collect or persist Slack credentials. Never commit `.env` files, tokens, signing secrets, or client secrets. Review app scopes and manifest before installing the app in a workspace. `slack run` runs locally and is not a production deployment.

Official quickstart: https://docs.slack.dev/quickstart


## Terminal client

The same assistant can be used from a terminal as well as the browser UI. This client uses the account's scoped bearer token; it does not store the token in its session file.

1. Sign in at `https://rz-ai.safenestt.com`.
2. Open **RZ MCP + Plugins** and create an API token with the `mcp:call` scope. Copy it once; do not paste it into chat or commit it.
3. On Kali/Linux, from a local checkout of this repository:

```bash
chmod +x scripts/rz-ai
export RZ_API_TOKEN='paste-your-scoped-token-here'
./scripts/rz-ai
```

For a one-shot prompt use `./scripts/rz-ai -m "Explain this error"`. To use a different base URL, set `RZ_AI_URL`; the default is `https://rz-ai.safenestt.com`. The CLI stores only a random conversation identifier in `~/.config/rz-ai/session` with restrictive file permissions. The API token remains in the shell environment and is never written by the client.

**Important:** the terminal client sends prompts to the hosted assistant. It does not itself grant the hosted service access to your Kali shell. Local-machine command execution requires a separate, authenticated local bridge and should be enabled only with explicit command approval.

## RZ-AI PWA and skill profiles

The existing MIA assistant is installable as a Progressive Web App (PWA): `/manifest.webmanifest` provides install metadata, `/app-icon.svg` provides the icon, and `/sw.js` caches only the app shell. The service worker deliberately bypasses `/v1/*`, MCP, API docs, and authenticated/private data; offline mode can display the cached shell but does not make AI or account functions work offline. The UI remains the existing assistant at `/`—this is not an operating system or a replacement chat app.

Built-in skill profiles live in `app/skills.py`. The chat endpoint adds concise, task-relevant guidance for strategic execution, software engineering, research and evidence, RZ principles, learning, ethical tool use, and creative communication. `GET /v1/skills` exposes only the non-sensitive profile names and descriptions, and `/skills` displays them in the same app. Skills guide reasoning and tool selection; they do not grant permissions, expand the server allowlist, or claim integrations are connected. RZ Collection notes remain in `docs/rz-collection-knowledge.md` and are treated as RZ's philosophy rather than universal fact.


## Investment research prototype

Authenticated investment endpoints are available at `/v1/investment/research`, `/v1/investment/opportunity-scan`, and `/v1/investment/quotes`. The opportunity scanner compares 2–5 specified candidates using separate source-linked research packs and an explicit evidence checklist; it deliberately does not invent numeric rankings or fair values from search snippets. Quote lookup currently uses an unofficial third-party Yahoo Finance chart endpoint and is not a licensed feed. Yahoo's published help says data is informational and must not be redistributed, so do not offer the quote output as a commercial data feed; select a provider and license suitable for the intended use before monetizing market-data features. All endpoints require authentication and none executes trades.
