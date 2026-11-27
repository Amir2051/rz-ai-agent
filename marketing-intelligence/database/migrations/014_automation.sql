BEGIN;
CREATE ROLE marketing_automation NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO marketing_automation;
GRANT marketing_automation TO marketing_auth;
CREATE TABLE marketing_jobs(
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  actor_id uuid NOT NULL REFERENCES auth_users(id),
  kind text NOT NULL CHECK(kind IN ('report','content_draft','plan_review')),
  payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16000),
  status text NOT NULL CHECK(status IN ('pending','running','succeeded','failed','cancelled')),
  run_at timestamptz NOT NULL,
  lease_until timestamptz,
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
  max_attempts integer NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE marketing_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketing_jobs FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE ON marketing_jobs TO marketing_automation;
CREATE POLICY jobs_member ON marketing_jobs FOR ALL TO marketing_automation
  USING (EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id
                WHERE w.id=workspace_id AND m.user_id=nullif(current_setting('app.user_id',true),'')::uuid))
  WITH CHECK (actor_id=nullif(current_setting('app.user_id',true),'')::uuid AND
              EXISTS(SELECT 1 FROM workspaces w JOIN organization_members m ON m.organization_id=w.organization_id
                     WHERE w.id=workspace_id AND m.user_id=actor_id));
CREATE INDEX marketing_jobs_due ON marketing_jobs(status,run_at);
CREATE OR REPLACE FUNCTION automation_recover() RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $f$
DECLARE n integer;
BEGIN
  UPDATE marketing_jobs SET status=CASE WHEN attempts<max_attempts THEN 'pending' ELSE 'failed' END,
    lease_until=NULL, last_error=COALESCE(last_error,'lease expired'), updated_at=now()
  WHERE status='running' AND lease_until<now();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $f$;
CREATE OR REPLACE FUNCTION automation_finish(p_id uuid,p_status text,p_error text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $f$
BEGIN
  UPDATE marketing_jobs SET status=p_status, last_error=p_error, lease_until=NULL, updated_at=now() WHERE id=p_id;
END $f$;
REVOKE ALL ON FUNCTION automation_recover(), automation_finish(uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION automation_recover(), automation_finish(uuid,text,text) TO marketing_automation;
COMMIT;
