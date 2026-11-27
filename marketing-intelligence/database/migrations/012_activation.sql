BEGIN;

-- Session maintenance: allow the authenticated role to refresh last_seen_at.
GRANT UPDATE(last_seen_at) ON auth_sessions TO marketing_auth;

-- Identity activation: runtime grants for the previously deferred tables.
GRANT marketing_registration TO marketing_auth;
GRANT SELECT, INSERT, UPDATE, DELETE ON auth_oidc_attempts TO marketing_auth;

-- MFA activation, tenant-scoped for the runtime role (owner keeps implicit access).
ALTER TABLE auth_mfa ENABLE ROW LEVEL SECURITY;
CREATE POLICY mfa_runtime_owner ON auth_mfa FOR ALL TO marketing_auth
  USING (user_id = nullif(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (user_id = nullif(current_setting('app.user_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE ON auth_mfa TO marketing_auth;

CREATE TABLE auth_recovery_codes(
  user_id uuid NOT NULL REFERENCES auth_users(id),
  code_hash text NOT NULL CHECK (length(code_hash) = 64),
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, code_hash)
);
ALTER TABLE auth_recovery_codes ENABLE ROW LEVEL SECURITY;
CREATE POLICY recovery_runtime_owner ON auth_recovery_codes FOR ALL TO marketing_auth
  USING (user_id = nullif(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (user_id = nullif(current_setting('app.user_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE ON auth_recovery_codes TO marketing_auth;

CREATE TABLE auth_login_challenges(
  challenge_hash text PRIMARY KEY CHECK (length(challenge_hash) = 64),
  user_id uuid NOT NULL REFERENCES auth_users(id),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  owner_enrollment boolean NOT NULL DEFAULT false
);
GRANT SELECT, INSERT, UPDATE, DELETE ON auth_login_challenges TO marketing_auth;

-- MCP grants: enforce tenant RLS through a definer verifier instead of broad table reads.
ALTER TABLE mcp_grants ADD COLUMN audience text NOT NULL DEFAULT '';
ALTER TABLE mcp_grants ADD COLUMN scopes text[] NOT NULL DEFAULT ARRAY['marketing:read']::text[];
CREATE OR REPLACE FUNCTION verify_mcp_grant(token_hash text) RETURNS TABLE(
  user_id text, workspace_id text, client_id text, expires_at bigint,
  audience text, scopes text[]
) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $fn$
  SELECT g.user_id::text, g.workspace_id::text, g.client_id,
         extract(epoch from g.expires_at)::bigint, g.audience, g.scopes
  FROM mcp_grants g JOIN auth_users u ON u.id = g.user_id
  WHERE g.token_hash = $1 AND g.revoked_at IS NULL AND g.expires_at > now()
    AND u.disabled_at IS NULL
$fn$;
REVOKE ALL ON FUNCTION verify_mcp_grant(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verify_mcp_grant(text) TO marketing_mcp_verify;
REVOKE SELECT ON mcp_grants FROM marketing_mcp_verify;

-- OAuth 2.1 authorization server state (system tables, role-restricted).
CREATE ROLE marketing_oauth NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO marketing_oauth;
GRANT marketing_oauth TO marketing_auth;

CREATE TABLE oauth_clients(
  client_id text PRIMARY KEY,
  client_secret_hash text,
  name text NOT NULL,
  redirect_uris text[] NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE oauth_codes(
  code_hash text PRIMARY KEY CHECK (length(code_hash) = 64),
  client_id text NOT NULL REFERENCES oauth_clients(client_id),
  user_id uuid NOT NULL REFERENCES auth_users(id),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  redirect_uri text NOT NULL,
  code_challenge text NOT NULL CHECK (length(code_challenge) BETWEEN 43 AND 128),
  resource text NOT NULL,
  scopes text[] NOT NULL,
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  family_id uuid NOT NULL
);
CREATE TABLE oauth_tokens(
  token_hash text PRIMARY KEY CHECK (length(token_hash) = 64),
  refresh_hash text UNIQUE CHECK (length(refresh_hash) = 64),
  client_id text NOT NULL REFERENCES oauth_clients(client_id),
  user_id uuid NOT NULL REFERENCES auth_users(id),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  audience text NOT NULL,
  scopes text[] NOT NULL,
  family_id uuid NOT NULL,
  expires_at timestamptz NOT NULL,
  refresh_expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX oauth_tokens_client_idx ON oauth_tokens(client_id);
CREATE INDEX oauth_tokens_refresh_family_idx ON oauth_tokens(family_id);
GRANT SELECT, INSERT, UPDATE, DELETE ON oauth_clients, oauth_codes, oauth_tokens TO marketing_oauth;
GRANT SELECT ON oauth_tokens TO marketing_mcp_verify;

COMMIT;
