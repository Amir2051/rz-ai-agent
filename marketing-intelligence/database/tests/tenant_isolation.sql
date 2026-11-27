DO $test$ BEGIN
 BEGIN
 INSERT INTO auth_users(id,email,password_hash) VALUES ('10000000-0000-4000-8000-000000000001','tenant-a@example.invalid','synthetic-not-a-password'),('10000000-0000-4000-8000-000000000002','tenant-b@example.invalid','synthetic-not-a-password');
 INSERT INTO organizations(id,name) VALUES ('20000000-0000-4000-8000-000000000001','Synthetic A'),('20000000-0000-4000-8000-000000000002','Synthetic B');
 INSERT INTO organization_members VALUES ('20000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000001','owner'),('20000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000002','viewer');
 INSERT INTO workspaces(organization_id,name) VALUES ('20000000-0000-4000-8000-000000000001','A'),('20000000-0000-4000-8000-000000000002','B');
 SET LOCAL ROLE marketing_reader;
 PERFORM set_config('app.user_id','10000000-0000-4000-8000-000000000001',true);
 IF (SELECT count(*) FROM workspaces)<>1 OR EXISTS(SELECT 1 FROM workspaces WHERE name='B') THEN RAISE EXCEPTION 'A isolation failed'; END IF;
 IF (SELECT count(*) FROM organizations)<>1 OR (SELECT count(*) FROM organization_members)<>1 THEN RAISE EXCEPTION 'membership failed'; END IF;
 PERFORM set_config('app.user_id','10000000-0000-4000-8000-000000000002',true);
 IF (SELECT count(*) FROM workspaces)<>1 OR EXISTS(SELECT 1 FROM workspaces WHERE name='A') THEN RAISE EXCEPTION 'B isolation failed'; END IF;
 PERFORM set_config('app.user_id','',true);
 IF EXISTS(SELECT 1 FROM workspaces) OR EXISTS(SELECT 1 FROM organizations) THEN RAISE EXCEPTION 'anonymous isolation failed'; END IF;
 RESET ROLE;
 RAISE SQLSTATE 'ZX001' USING MESSAGE='rollback synthetic fixtures';
 EXCEPTION WHEN SQLSTATE 'ZX001' THEN NULL;
 END;
 END $test$;
