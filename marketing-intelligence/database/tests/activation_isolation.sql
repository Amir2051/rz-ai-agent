BEGIN;
-- Self-contained fixtures for activation-layer grants and tenant RLS.
INSERT INTO auth_users(id,email,password_hash) VALUES
  ('11111111-1111-4111-8111-111111111111','a-activation@example.invalid','synthetic'),
  ('22222222-2222-4222-8222-222222222222','b-activation@example.invalid','synthetic');
INSERT INTO organizations(id,name) VALUES ('33333333-3333-4333-8333-333333333333','Activation Org');
INSERT INTO organization_members(organization_id,user_id,role) VALUES
  ('33333333-3333-4333-8333-333333333333','11111111-1111-4111-8111-111111111111','owner'),
  ('33333333-3333-4333-8333-333333333333','22222222-2222-4222-8222-222222222222','viewer');
INSERT INTO workspaces(id,organization_id,name) VALUES
  ('44444444-4444-4444-8444-444444444444','33333333-3333-4333-8333-333333333333','Activation Workspace');
INSERT INTO oauth_clients(client_id,client_secret_hash,name,redirect_uris) VALUES
  ('synthetic-client',repeat('a',64),'synthetic',ARRAY['http://127.0.0.1:8090/callback']);

SET ROLE marketing_auth;
SELECT set_config('app.user_id','11111111-1111-4111-8111-111111111111',true);

-- OIDC state can be created and consumed once by the runtime role.
INSERT INTO auth_oidc_attempts(state_hash,nonce_hash,encrypted_verifier,expires_at)
  VALUES (repeat('a',64),repeat('b',64),decode(repeat('00',40),'hex'),now()+interval '10 minutes');
DO $t$ BEGIN
  IF (SELECT count(*) FROM auth_oidc_attempts WHERE state_hash=repeat('a',64)) <> 1 THEN
    RAISE EXCEPTION 'oidc insert denied';
  END IF;
  IF (SELECT count(*) FROM verify_mcp_grant(repeat('0',64))) <> 0 THEN
    RAISE EXCEPTION 'grant verifier leaked';
  END IF;
END $t$;

-- MFA and recovery records are tenant-scoped for the runtime role.
INSERT INTO auth_mfa(user_id,encrypted_secret,key_version,confirmed_at,last_step)
  VALUES ('11111111-1111-4111-8111-111111111111',decode(repeat('00',32),'hex'),1,now(),0);
INSERT INTO auth_recovery_codes(user_id,code_hash)
  VALUES ('11111111-1111-4111-8111-111111111111',repeat('c',64));
DO $t$ BEGIN
  IF (SELECT count(*) FROM auth_mfa) <> 1 THEN RAISE EXCEPTION 'mfa isolation failed'; END IF;
  IF (SELECT count(*) FROM auth_recovery_codes) <> 1 THEN RAISE EXCEPTION 'recovery isolation failed'; END IF;
END $t$;
SELECT set_config('app.user_id','22222222-2222-4222-8222-222222222222',true);
DO $t$ BEGIN
  IF (SELECT count(*) FROM auth_mfa) <> 0 THEN RAISE EXCEPTION 'cross-tenant mfa read'; END IF;
  IF (SELECT count(*) FROM auth_recovery_codes) <> 0 THEN RAISE EXCEPTION 'cross-tenant recovery read'; END IF;
END $t$;

-- Audience-bound OAuth tokens are readable only through the restricted verifier.
SELECT set_config('app.user_id','11111111-1111-4111-8111-111111111111',true);
INSERT INTO oauth_tokens(token_hash,refresh_hash,client_id,user_id,workspace_id,audience,scopes,family_id,expires_at,refresh_expires_at)
  VALUES (repeat('d',64),repeat('e',64),'synthetic-client','11111111-1111-4111-8111-111111111111',
          '44444444-4444-4444-8444-444444444444','https://marketing.safenestt.com/mcp',
          ARRAY['marketing:read'],gen_random_uuid(),now()+interval '15 minutes',now()+interval '30 days');
DO $t$ BEGIN
  IF (SELECT count(*) FROM oauth_tokens WHERE token_hash=repeat('d',64)) <> 1 THEN
    RAISE EXCEPTION 'oauth token insert denied';
  END IF;
  IF (SELECT audience FROM oauth_tokens WHERE token_hash=repeat('d',64))
       <> 'https://marketing.safenestt.com/mcp' THEN
    RAISE EXCEPTION 'audience binding lost';
  END IF;
END $t$;
RESET ROLE;
ROLLBACK;
