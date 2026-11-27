-- Idempotent rollout from the documented production state (001+002) to the
-- fully tested schema (003-012). Roles and policies are guarded because the
-- production database already contains marketing_auth from an earlier partial run.
BEGIN;

DO $r$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='marketing_auth') THEN
  CREATE ROLE marketing_auth NOLOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS; END IF; END $r$;
GRANT USAGE ON SCHEMA public TO marketing_auth;
GRANT SELECT (id,email,password_hash,disabled_at) ON auth_users TO marketing_auth;
GRANT SELECT,INSERT,DELETE ON auth_sessions TO marketing_auth;
GRANT marketing_reader TO marketing_auth;
GRANT marketing_auth TO marketing_owner;
CREATE TABLE IF NOT EXISTS auth_login_limits (bucket text PRIMARY KEY, attempts integer NOT NULL CHECK(attempts>=0), window_start timestamptz NOT NULL);
GRANT SELECT,INSERT,UPDATE ON auth_login_limits TO marketing_auth;

DO $r$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='marketing_drafter') THEN
  CREATE ROLE marketing_drafter NOLOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS; END IF; END $r$;
GRANT marketing_reader TO marketing_drafter;
GRANT marketing_drafter TO marketing_auth;
GRANT USAGE ON SCHEMA public TO marketing_drafter;
CREATE TABLE IF NOT EXISTS marketing_drafts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspaces(id),
 actor_id uuid NOT NULL REFERENCES auth_users(id), kind text NOT NULL CHECK(kind IN ('campaign','content','lead','automation')),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=32000),
 created_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE marketing_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketing_drafts FORCE ROW LEVEL SECURITY;
GRANT SELECT ON marketing_drafts TO marketing_reader;
GRANT INSERT(workspace_id,actor_id,kind,payload) ON marketing_drafts TO marketing_drafter;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='marketing_drafts' AND policyname='drafts_read') THEN
  CREATE POLICY drafts_read ON marketing_drafts FOR SELECT TO marketing_reader USING (EXISTS(SELECT 1 FROM workspaces w WHERE w.id=workspace_id)); END IF; END $p$;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='marketing_drafts' AND policyname='drafts_insert') THEN
  CREATE POLICY drafts_insert ON marketing_drafts FOR INSERT TO marketing_drafter WITH CHECK (actor_id=nullif(current_setting('app.user_id',true),'')::uuid AND EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.user_id=actor_id AND m.role IN ('owner','admin','analyst'))); END IF; END $p$;
CREATE INDEX IF NOT EXISTS marketing_drafts_workspace_created_idx ON marketing_drafts(workspace_id,created_at DESC);

CREATE TABLE IF NOT EXISTS ai_credentials(workspace_id uuid NOT NULL REFERENCES workspaces(id),provider text NOT NULL CHECK(provider IN ('openai','anthropic','openrouter','gemini','ollama','custom')),ciphertext bytea NOT NULL CHECK(octet_length(ciphertext) BETWEEN 29 AND 8300),key_version integer NOT NULL CHECK(key_version>0),PRIMARY KEY(workspace_id,provider));
ALTER TABLE ai_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_credentials FORCE ROW LEVEL SECURITY;
DO $r$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='marketing_ai_credentials') THEN
  CREATE ROLE marketing_ai_credentials NOLOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS; END IF; END $r$;
GRANT marketing_reader TO marketing_ai_credentials;
GRANT USAGE ON SCHEMA public TO marketing_ai_credentials;
GRANT SELECT,INSERT,UPDATE,DELETE ON ai_credentials TO marketing_ai_credentials;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='ai_credentials' AND policyname='ai_credentials_admin') THEN
  CREATE POLICY ai_credentials_admin ON ai_credentials TO marketing_ai_credentials USING(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin'))) WITH CHECK(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin'))); END IF; END $p$;

CREATE TABLE IF NOT EXISTS ai_daily_budget(workspace_id uuid NOT NULL REFERENCES workspaces(id),day date NOT NULL,limit_microusd bigint NOT NULL CHECK(limit_microusd>=0),reserved_microusd bigint NOT NULL DEFAULT 0 CHECK(reserved_microusd>=0 AND reserved_microusd<=limit_microusd),PRIMARY KEY(workspace_id,day));
ALTER TABLE ai_daily_budget ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_daily_budget FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON ai_daily_budget TO marketing_ai_credentials;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='ai_daily_budget' AND policyname='ai_budget_admin') THEN
  CREATE POLICY ai_budget_admin ON ai_daily_budget TO marketing_ai_credentials USING(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin'))) WITH CHECK(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin'))); END IF; END $p$;

CREATE TABLE IF NOT EXISTS ai_settings(workspace_id uuid PRIMARY KEY REFERENCES workspaces(id),provider text NOT NULL CHECK(provider IN ('openai','anthropic','openrouter','gemini','ollama')),model text NOT NULL CHECK(length(model) BETWEEN 1 AND 200),max_output integer NOT NULL CHECK(max_output BETWEEN 1 AND 2048),updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE ai_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_settings FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON ai_settings TO marketing_ai_credentials;
GRANT marketing_ai_credentials TO marketing_auth;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='ai_settings' AND policyname='ai_settings_admin') THEN
  CREATE POLICY ai_settings_admin ON ai_settings TO marketing_ai_credentials USING(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin'))) WITH CHECK(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin'))); END IF; END $p$;

CREATE TABLE IF NOT EXISTS mcp_activity(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL REFERENCES workspaces(id),actor_id uuid NOT NULL REFERENCES auth_users(id),client_id text NOT NULL CHECK(length(client_id) BETWEEN 1 AND 200),tool text NOT NULL CHECK(length(tool) BETWEEN 1 AND 100),outcome text NOT NULL CHECK(outcome IN ('success','denied','failed')),created_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE mcp_activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_activity FORCE ROW LEVEL SECURITY;
DO $r$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='marketing_mcp_audit') THEN
  CREATE ROLE marketing_mcp_audit NOLOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS; END IF; END $r$;
GRANT marketing_reader TO marketing_mcp_audit;
GRANT marketing_mcp_audit TO marketing_auth;
GRANT USAGE ON SCHEMA public TO marketing_mcp_audit;
GRANT SELECT ON mcp_activity TO marketing_reader;
GRANT INSERT(workspace_id,actor_id,client_id,tool,outcome) ON mcp_activity TO marketing_mcp_audit;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='mcp_activity' AND policyname='activity_read') THEN
  CREATE POLICY activity_read ON mcp_activity FOR SELECT TO marketing_reader USING(EXISTS(SELECT 1 FROM workspaces w WHERE w.id=workspace_id)); END IF; END $p$;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='mcp_activity' AND policyname='activity_append') THEN
  CREATE POLICY activity_append ON mcp_activity FOR INSERT TO marketing_mcp_audit WITH CHECK(actor_id=nullif(current_setting('app.user_id',true),'')::uuid AND EXISTS(SELECT 1 FROM workspaces w WHERE w.id=workspace_id)); END IF; END $p$;
CREATE INDEX IF NOT EXISTS mcp_activity_workspace_time ON mcp_activity(workspace_id,created_at DESC);

CREATE TABLE IF NOT EXISTS mcp_grants(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),token_hash text UNIQUE NOT NULL CHECK(length(token_hash)=64),user_id uuid NOT NULL REFERENCES auth_users(id),workspace_id uuid NOT NULL REFERENCES workspaces(id),client_id text NOT NULL,expires_at timestamptz NOT NULL,revoked_at timestamptz);
DO $r$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='marketing_mcp_verify') THEN
  CREATE ROLE marketing_mcp_verify NOLOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS; END IF; END $r$;
GRANT USAGE ON SCHEMA public TO marketing_mcp_verify;
GRANT SELECT ON mcp_grants TO marketing_mcp_verify;
GRANT SELECT(id,disabled_at) ON auth_users TO marketing_mcp_verify;
GRANT marketing_mcp_verify TO marketing_auth;

CREATE TABLE IF NOT EXISTS auth_external_identities(provider text NOT NULL CHECK(provider='google'),subject text NOT NULL,user_id uuid NOT NULL REFERENCES auth_users(id),created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(provider,subject),UNIQUE(provider,user_id));
CREATE TABLE IF NOT EXISTS auth_mfa(user_id uuid PRIMARY KEY REFERENCES auth_users(id),encrypted_secret bytea NOT NULL CHECK(octet_length(encrypted_secret)>=29),key_version integer NOT NULL CHECK(key_version>0),confirmed_at timestamptz,last_step bigint NOT NULL DEFAULT -1);
CREATE TABLE IF NOT EXISTS auth_oidc_attempts(state_hash text PRIMARY KEY,nonce_hash text NOT NULL,encrypted_verifier bytea NOT NULL,expires_at timestamptz NOT NULL,consumed_at timestamptz);

DO $r$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='marketing_registration') THEN
  CREATE ROLE marketing_registration NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS; END IF; END $r$;
GRANT USAGE ON SCHEMA public TO marketing_registration;
GRANT SELECT(id,email,disabled_at),INSERT(id,email,password_hash) ON auth_users TO marketing_registration;
GRANT SELECT,INSERT ON auth_external_identities TO marketing_registration;
GRANT INSERT(id,name) ON organizations TO marketing_registration;
GRANT INSERT(organization_id,user_id,role) ON organization_members TO marketing_registration;
GRANT INSERT(id,organization_id,name) ON workspaces TO marketing_registration;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='organizations' AND policyname='registration_org_insert') THEN
  CREATE POLICY registration_org_insert ON organizations FOR INSERT TO marketing_registration WITH CHECK(id=nullif(current_setting('app.registration_org',true),'')::uuid); END IF; END $p$;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='organization_members' AND policyname='registration_member_insert') THEN
  CREATE POLICY registration_member_insert ON organization_members FOR INSERT TO marketing_registration WITH CHECK(organization_id=nullif(current_setting('app.registration_org',true),'')::uuid AND user_id=nullif(current_setting('app.registration_user',true),'')::uuid AND role='owner'); END IF; END $p$;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='workspaces' AND policyname='registration_workspace_insert') THEN
  CREATE POLICY registration_workspace_insert ON workspaces FOR INSERT TO marketing_registration WITH CHECK(organization_id=nullif(current_setting('app.registration_org',true),'')::uuid); END IF; END $p$;

-- Activation layer (012), idempotent.
GRANT UPDATE(last_seen_at) ON auth_sessions TO marketing_auth;
GRANT marketing_registration TO marketing_auth;
GRANT SELECT, INSERT, UPDATE, DELETE ON auth_oidc_attempts TO marketing_auth;
ALTER TABLE auth_mfa ENABLE ROW LEVEL SECURITY;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='auth_mfa' AND policyname='mfa_runtime_owner') THEN
  CREATE POLICY mfa_runtime_owner ON auth_mfa FOR ALL TO marketing_auth USING (user_id=nullif(current_setting('app.user_id',true),'')::uuid) WITH CHECK (user_id=nullif(current_setting('app.user_id',true),'')::uuid); END IF; END $p$;
GRANT SELECT, INSERT, UPDATE ON auth_mfa TO marketing_auth;
CREATE TABLE IF NOT EXISTS auth_recovery_codes(
  user_id uuid NOT NULL REFERENCES auth_users(id), code_hash text NOT NULL CHECK(length(code_hash)=64),
  used_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (user_id,code_hash));
ALTER TABLE auth_recovery_codes ENABLE ROW LEVEL SECURITY;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='auth_recovery_codes' AND policyname='recovery_runtime_owner') THEN
  CREATE POLICY recovery_runtime_owner ON auth_recovery_codes FOR ALL TO marketing_auth USING (user_id=nullif(current_setting('app.user_id',true),'')::uuid) WITH CHECK (user_id=nullif(current_setting('app.user_id',true),'')::uuid); END IF; END $p$;
GRANT SELECT, INSERT, UPDATE ON auth_recovery_codes TO marketing_auth;
CREATE TABLE IF NOT EXISTS auth_login_challenges(
  challenge_hash text PRIMARY KEY CHECK(length(challenge_hash)=64), user_id uuid NOT NULL REFERENCES auth_users(id),
  expires_at timestamptz NOT NULL, consumed_at timestamptz, owner_enrollment boolean NOT NULL DEFAULT false);
GRANT SELECT, INSERT, UPDATE, DELETE ON auth_login_challenges TO marketing_auth;
ALTER TABLE mcp_grants ADD COLUMN IF NOT EXISTS audience text NOT NULL DEFAULT '';
ALTER TABLE mcp_grants ADD COLUMN IF NOT EXISTS scopes text[] NOT NULL DEFAULT ARRAY['marketing:read']::text[];
CREATE OR REPLACE FUNCTION verify_mcp_grant(token_hash text) RETURNS TABLE(
  user_id text, workspace_id text, client_id text, expires_at bigint, audience text, scopes text[]
) LANGUAGE sql SECURITY DEFINER SET search_path = public AS $fn$
  SELECT g.user_id::text, g.workspace_id::text, g.client_id, extract(epoch from g.expires_at)::bigint, g.audience, g.scopes
  FROM mcp_grants g JOIN auth_users u ON u.id=g.user_id
  WHERE g.token_hash=$1 AND g.revoked_at IS NULL AND g.expires_at>now() AND u.disabled_at IS NULL
$fn$;
REVOKE ALL ON FUNCTION verify_mcp_grant(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verify_mcp_grant(text) TO marketing_mcp_verify;
REVOKE SELECT ON mcp_grants FROM marketing_mcp_verify;

DO $r$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='marketing_oauth') THEN
  CREATE ROLE marketing_oauth NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS; END IF; END $r$;
GRANT USAGE ON SCHEMA public TO marketing_oauth;
GRANT marketing_oauth TO marketing_auth;
CREATE TABLE IF NOT EXISTS oauth_clients(client_id text PRIMARY KEY,client_secret_hash text,name text NOT NULL,redirect_uris text[] NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS oauth_codes(code_hash text PRIMARY KEY CHECK(length(code_hash)=64),client_id text NOT NULL REFERENCES oauth_clients(client_id),user_id uuid NOT NULL REFERENCES auth_users(id),workspace_id uuid NOT NULL REFERENCES workspaces(id),redirect_uri text NOT NULL,code_challenge text NOT NULL CHECK(length(code_challenge) BETWEEN 43 AND 128),resource text NOT NULL,scopes text[] NOT NULL,expires_at timestamptz NOT NULL,consumed_at timestamptz,family_id uuid NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_tokens(token_hash text PRIMARY KEY CHECK(length(token_hash)=64),refresh_hash text UNIQUE CHECK(length(refresh_hash)=64),client_id text NOT NULL REFERENCES oauth_clients(client_id),user_id uuid NOT NULL REFERENCES auth_users(id),workspace_id uuid NOT NULL REFERENCES workspaces(id),audience text NOT NULL,scopes text[] NOT NULL,family_id uuid NOT NULL,expires_at timestamptz NOT NULL,refresh_expires_at timestamptz NOT NULL,revoked_at timestamptz,created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS oauth_tokens_client_idx ON oauth_tokens(client_id);
CREATE INDEX IF NOT EXISTS oauth_tokens_refresh_family_idx ON oauth_tokens(family_id);
GRANT SELECT, INSERT, UPDATE, DELETE ON oauth_clients, oauth_codes, oauth_tokens TO marketing_oauth;
GRANT SELECT ON oauth_tokens TO marketing_mcp_verify;

-- Automation (014), idempotent.
DO $r$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='marketing_automation') THEN
  CREATE ROLE marketing_automation NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS; END IF; END $r$;
GRANT USAGE ON SCHEMA public TO marketing_automation;
GRANT marketing_automation TO marketing_auth;
CREATE TABLE IF NOT EXISTS marketing_jobs(
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspaces(id),
  actor_id uuid NOT NULL REFERENCES auth_users(id), kind text NOT NULL CHECK(kind IN ('report','content_draft','plan_review')),
  payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16000),
  status text NOT NULL CHECK(status IN ('pending','running','succeeded','failed','cancelled')),
  run_at timestamptz NOT NULL, lease_until timestamptz, attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
  max_attempts integer NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10), last_error text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE marketing_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketing_jobs FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON marketing_jobs TO marketing_automation;
DO $p$ BEGIN IF NOT EXISTS (SELECT FROM pg_policies WHERE schemaname='public' AND tablename='marketing_jobs' AND policyname='jobs_member') THEN
  CREATE POLICY jobs_member ON marketing_jobs FOR ALL TO marketing_automation
    USING (EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id
                  WHERE w.id=workspace_id AND m.user_id=nullif(current_setting('app.user_id',true),'')::uuid))
    WITH CHECK (actor_id=nullif(current_setting('app.user_id',true),'')::uuid AND
                EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id
                       WHERE w.id=workspace_id AND m.user_id=actor_id)); END IF; END $p$;
CREATE INDEX IF NOT EXISTS marketing_jobs_due ON marketing_jobs(status,run_at);
CREATE OR REPLACE FUNCTION automation_recover() RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $f$
DECLARE n integer; BEGIN
  UPDATE marketing_jobs SET status=CASE WHEN attempts<max_attempts THEN 'pending' ELSE 'failed' END,
    lease_until=NULL, last_error=COALESCE(last_error,'lease expired'), updated_at=now()
  WHERE status='running' AND lease_until<now();
  GET DIAGNOSTICS n = ROW_COUNT; RETURN n; END $f$;
CREATE OR REPLACE FUNCTION automation_finish(p_id uuid,p_status text,p_error text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $f$
BEGIN UPDATE marketing_jobs SET status=p_status,last_error=p_error,lease_until=NULL,updated_at=now() WHERE id=p_id; END $f$;
REVOKE ALL ON FUNCTION automation_recover(), automation_finish(uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION automation_recover(), automation_finish(uuid,text,text) TO marketing_automation;
COMMIT;
