RZ Agent authentication setup

RZ Agent account authentication is separate from SafeNestT. Use a dedicated Neon project/database and do not reuse SafeNestT database credentials.

## What is implemented

- Email/password registration and login, with Argon2 password hashes.
- Random session tokens stored only as SHA-256 hashes in PostgreSQL.
- HttpOnly, SameSite=Lax cookies; production cookies use Secure.
- Database-backed session expiration and logout revocation.
- Protected chat and conversation-list/reset endpoints.
- Scoped, revocable API tokens for MCP clients; only token hashes are stored, and a new token is shown once.
- User-prefixed conversation IDs to prevent one account from using another account's session IDs.
- Local mode remains compatible with the existing RZ_API_KEY flow when DATABASE_URL is unset.

## Configure the dedicated Neon database

1. Create or select a database dedicated to RZ Agent. Never use the SafeNestT database.
2. In that database's SQL editor, run database/migrations/001_auth.sql, then database/migrations/002_api_tokens.sql.
3. Copy its PostgreSQL connection string into Render's DATABASE_URL secret. Keep TLS/SSL enabled.
4. Set APP_ORIGIN to the exact public RZ Agent origin, for example https://your-rz-agent-service.onrender.com (replace with the actual service URL).
5. Set APP_ENV=production and configure OPENROUTER_API_KEY and OPENROUTER_MODEL in Render's environment settings.
6. Deploy only after reviewing the diff and verifying the database migration has succeeded.

Do not commit connection strings, provider keys, or account passwords.

## Endpoints

- GET /v1/auth/mode: reports account when DATABASE_URL is configured, otherwise api_key.
- POST /v1/auth/register: JSON with email and a password of at least 12 characters.
- POST /v1/auth/login: same JSON shape.
- GET /v1/auth/me: validates the session cookie.
- POST /v1/auth/logout: revokes the current session.
- POST /v1/auth/tokens: create a scoped API token (session-authenticated); JSON accepts `name`, `scopes` (`mcp:read`, `mcp:call`), and optional `expires_in_days` (1–365, or `null` for no expiry). The plaintext token is returned once.
- GET /v1/auth/tokens: list token metadata without secrets.
- DELETE /v1/auth/tokens/{token_id}: revoke one of your tokens.
- POST /mcp: MCP JSON-RPC endpoint supporting `initialize`, `ping`, `tools/list`, and `tools/call`.
- GET /v1/tools and GET /v1/capabilities: discover available tool schemas and service capabilities.
- POST /v1/tools/{tool_name}: call a tool with a JSON object body. Send `Authorization: Bearer rzat_...` for external clients.
- GET /health: health check.

## Current limitations before public launch

- Both SQL migrations must be applied to the dedicated RZ Agent database before account tokens can be created or verified.
- There is no email verification, password-reset flow, MFA, account recovery, or admin account-management screen yet.
- Rate limits currently live in process memory. Add a shared rate-limit store or edge-level controls before relying on them at scale.
- Conversation history remains in process memory, so it is not durable across service restarts and is not yet stored in PostgreSQL.
- Run an authenticated browser test against the dedicated database before treating the deployment as production-ready.


## MCP tools and security

The initial MCP surface intentionally exposes only three account-scoped tools:

- `chat`: call the configured RZ AI provider.
- `list_conversations`: list in-memory conversation IDs owned by the authenticated account.
- `reset_conversation`: clear one owned in-memory conversation.

It does **not** provide web browsing, arbitrary shell execution, local file access, or external-app actions. UI prompt shortcuts are guided chat prompts, not independent backend tools.

Create a token while signed in, copy it once, and configure the MCP client to send `Authorization: Bearer <token>`. Use only the scopes needed. Revoke tokens through `DELETE /v1/auth/tokens/{token_id}`. Browser sessions remain cookie-authenticated; bearer tokens do not bypass account authentication. Local development without `DATABASE_URL` continues to accept the existing `RZ_API_KEY` as a bearer token for MCP testing.
