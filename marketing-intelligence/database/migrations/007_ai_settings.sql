BEGIN;
CREATE TABLE ai_settings(workspace_id uuid PRIMARY KEY REFERENCES workspaces(id),provider text NOT NULL CHECK(provider IN ('openai','anthropic','openrouter','gemini','ollama')),model text NOT NULL CHECK(length(model) BETWEEN 1 AND 200),max_output integer NOT NULL CHECK(max_output BETWEEN 1 AND 2048),updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE ai_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_settings FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON ai_settings TO marketing_ai_credentials;
GRANT marketing_ai_credentials TO marketing_auth;
CREATE POLICY ai_settings_admin ON ai_settings TO marketing_ai_credentials USING(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin'))) WITH CHECK(EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id WHERE w.id=workspace_id AND m.role IN ('owner','admin')));
COMMIT;
