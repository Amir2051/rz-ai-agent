BEGIN;
CREATE ROLE marketing_registration NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO marketing_registration;
GRANT SELECT(id,email,disabled_at),INSERT(id,email,password_hash) ON auth_users TO marketing_registration;
GRANT SELECT,INSERT ON auth_external_identities TO marketing_registration;
GRANT INSERT(id,name) ON organizations TO marketing_registration;
GRANT INSERT(organization_id,user_id,role) ON organization_members TO marketing_registration;
GRANT INSERT(id,organization_id,name) ON workspaces TO marketing_registration;
CREATE POLICY registration_org_insert ON organizations FOR INSERT TO marketing_registration WITH CHECK(id=nullif(current_setting('app.registration_org',true),'')::uuid);
CREATE POLICY registration_member_insert ON organization_members FOR INSERT TO marketing_registration WITH CHECK(organization_id=nullif(current_setting('app.registration_org',true),'')::uuid AND user_id=nullif(current_setting('app.registration_user',true),'')::uuid AND role='owner');
CREATE POLICY registration_workspace_insert ON workspaces FOR INSERT TO marketing_registration WITH CHECK(organization_id=nullif(current_setting('app.registration_org',true),'')::uuid);
-- No runtime membership granted: callback integration and role review pending.
COMMIT;
