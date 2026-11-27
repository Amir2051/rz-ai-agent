BEGIN;
CREATE TABLE mcp_grants(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),token_hash text UNIQUE NOT NULL CHECK(length(token_hash)=64),user_id uuid NOT NULL REFERENCES auth_users(id),workspace_id uuid NOT NULL REFERENCES workspaces(id),client_id text NOT NULL,expires_at timestamptz NOT NULL,revoked_at timestamptz);
CREATE ROLE marketing_mcp_verify NOLOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO marketing_mcp_verify;
GRANT SELECT ON mcp_grants TO marketing_mcp_verify;
GRANT SELECT(id,disabled_at) ON auth_users TO marketing_mcp_verify;
GRANT marketing_mcp_verify TO marketing_auth;
COMMIT;
