SELECT current_user, session_user, current_database();
SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='auth_users';
