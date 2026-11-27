SELECT has_schema_privilege('marketing_service','public','CREATE') AS schema_create,
       rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname='marketing_service';
SELECT table_name, privilege_type FROM information_schema.role_table_grants
WHERE grantee='marketing_auth' AND table_schema='public' ORDER BY 1,2;
SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1;
