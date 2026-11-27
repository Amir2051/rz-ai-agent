\echo TABLES
SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1;
\echo ROLES
SELECT rolname FROM pg_roles WHERE rolname LIKE 'marketing%' ORDER BY 1;
\echo RLS
SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='r' AND relrowsecurity ORDER BY 1;
