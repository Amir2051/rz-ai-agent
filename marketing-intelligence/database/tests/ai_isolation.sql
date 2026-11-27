BEGIN;
GRANT marketing_ai_credentials TO mi_fixture_service;
SET LOCAL ROLE marketing_ai_credentials;
SELECT set_config('app.user_id','10000000-0000-4000-8000-000000000001',true);
INSERT INTO ai_credentials VALUES ('30000000-0000-4000-8000-000000000001','openai',decode(repeat('ab',40),'hex'),1);
DO $$ BEGIN
 IF (SELECT count(*) FROM ai_credentials)<>1 THEN RAISE EXCEPTION 'owner read failed'; END IF;
 PERFORM set_config('app.user_id','10000000-0000-4000-8000-000000000002',true);
 IF (SELECT count(*) FROM ai_credentials)<>0 THEN RAISE EXCEPTION 'cross tenant read'; END IF;
 BEGIN INSERT INTO ai_credentials VALUES ('30000000-0000-4000-8000-000000000002','openai',decode(repeat('ab',40),'hex'),1); RAISE EXCEPTION 'viewer write allowed'; EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
ROLLBACK;
