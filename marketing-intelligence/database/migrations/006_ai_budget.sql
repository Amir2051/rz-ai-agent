BEGIN;
CREATE TABLE ai_daily_budget(workspace_id uuid NOT NULL REFERENCES workspaces(id),day date NOT NULL,limit_microusd bigint NOT NULL CHECK(limit_microusd>=0),reserved_microusd bigint NOT NULL DEFAULT 0 CHECK(reserved_microusd>=0 AND reserved_microusd<=limit_microusd),PRIMARY KEY(workspace_id,day));
ALTER TABLE ai_daily_budget ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_daily_budget FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON ai_daily_budget TO marketing_ai_credentials;
CREATE POLICY ai_budget_admin ON ai_daily_budget TO marketing_ai_credentials USING(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin'))) WITH CHECK(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin')));
COMMIT;
