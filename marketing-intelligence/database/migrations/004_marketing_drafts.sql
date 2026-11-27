BEGIN;
CREATE ROLE marketing_drafter NOLOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS;
GRANT marketing_reader TO marketing_drafter;
GRANT marketing_drafter TO marketing_auth;
GRANT USAGE ON SCHEMA public TO marketing_drafter;
CREATE TABLE marketing_drafts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspaces(id),
 actor_id uuid NOT NULL REFERENCES auth_users(id), kind text NOT NULL CHECK(kind IN ('campaign','content','lead','automation')),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=32000),
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE marketing_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketing_drafts FORCE ROW LEVEL SECURITY;
GRANT SELECT ON marketing_drafts TO marketing_reader;
GRANT INSERT(workspace_id,actor_id,kind,payload) ON marketing_drafts TO marketing_drafter;
CREATE POLICY drafts_read ON marketing_drafts FOR SELECT TO marketing_reader USING (EXISTS(SELECT 1 FROM workspaces w WHERE w.id=workspace_id));
CREATE POLICY drafts_insert ON marketing_drafts FOR INSERT TO marketing_drafter WITH CHECK (actor_id=nullif(current_setting('app.user_id',true),'')::uuid AND EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.user_id=actor_id AND m.role IN ('owner','admin','analyst')));
CREATE INDEX marketing_drafts_workspace_created_idx ON marketing_drafts(workspace_id,created_at DESC);
COMMIT;
