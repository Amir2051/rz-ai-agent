BEGIN;
CREATE TABLE auth_external_identities(provider text NOT NULL CHECK(provider='google'),subject text NOT NULL,user_id uuid NOT NULL REFERENCES auth_users(id),created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(provider,subject),UNIQUE(provider,user_id));
CREATE TABLE auth_mfa(user_id uuid PRIMARY KEY REFERENCES auth_users(id),encrypted_secret bytea NOT NULL CHECK(octet_length(encrypted_secret)>=29),key_version integer NOT NULL CHECK(key_version>0),confirmed_at timestamptz,last_step bigint NOT NULL DEFAULT -1);
CREATE TABLE auth_oidc_attempts(state_hash text PRIMARY KEY,nonce_hash text NOT NULL,encrypted_verifier bytea NOT NULL,expires_at timestamptz NOT NULL,consumed_at timestamptz);
-- No application grants until enrollment, callback and registration policy are reviewed.
COMMIT;
