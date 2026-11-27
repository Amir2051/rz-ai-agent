"""Durable automation scheduling and leased execution.

Only draft/report/plan-review jobs can be scheduled. No job may publish,
send messages or spend money; the handler receives a restricted payload and
its output is persisted as a reviewable draft.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from psycopg.types.json import Jsonb

KINDS = frozenset({'report', 'content_draft', 'plan_review'})


def _bind(conn, user, write=True):
    conn.execute('SET LOCAL ROLE marketing_automation')
    conn.execute("SELECT set_config('app.user_id',%s,true)", (str(UUID(str(user))),))


def schedule(conn, user, workspace, kind, payload, run_at):
    if kind not in KINDS or not isinstance(payload, dict):
        raise ValueError('Invalid job')
    if any(payload.get(k) is True for k in ('external_execution', 'published', 'sent')):
        raise ValueError('External effects are not permitted')
    if not isinstance(run_at, datetime) or run_at.tzinfo is None:
        raise ValueError('Timezone-aware run_at required')
    _bind(conn, user)
    row = conn.execute(
        'INSERT INTO marketing_jobs(workspace_id,actor_id,kind,payload,run_at) '
        'VALUES (%s,%s,%s,%s,%s) RETURNING id::text',
        (str(UUID(str(workspace))), str(UUID(str(user))), kind, Jsonb(payload), run_at),
    ).fetchone()
    return {'id': row[0], 'status': 'pending', 'external_execution': False}


def recover(conn):
    conn.execute('SET LOCAL ROLE marketing_automation')
    return conn.execute('SELECT automation_recover()').fetchone()[0]


def claim(conn, limit=10):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('Invalid limit')
    conn.execute('SET LOCAL ROLE marketing_automation')
    rows = conn.execute(
        "SELECT id::text,workspace_id::text,actor_id::text,kind,payload,attempts,max_attempts "
        "FROM marketing_jobs WHERE status='pending' AND run_at<=now() "
        "ORDER BY run_at,id FOR UPDATE SKIP LOCKED LIMIT %s", (limit,),
    ).fetchall()
    jobs = []
    for r in rows:
        updated = conn.execute(
            "UPDATE marketing_jobs SET status='running', lease_until=now()+interval '5 minutes', "
            "attempts=attempts+1, updated_at=now() WHERE id=%s AND status='pending' RETURNING id",
            (r[0],),
        ).fetchone()
        if updated:
            jobs.append({'id': r[0], 'workspace': r[1], 'actor': r[2], 'kind': r[3],
                         'payload': r[4], 'attempt': r[5], 'max_attempts': r[6]})
    return jobs


def finish(conn, job_id, status, error=None):
    if status not in ('succeeded', 'failed', 'cancelled'):
        raise ValueError('Invalid status')
    conn.execute('SET LOCAL ROLE marketing_automation')
    conn.execute('SELECT automation_finish(%s,%s,%s)',
                 (str(UUID(str(job_id))), status, str(error)[:500] if error else None))


def run_due(db, handler, limit=10):
    """Claim due jobs and execute a restricted handler. Never raises on a single job."""
    results = []
    with db() as conn:
        recover(conn)
        jobs = claim(conn, limit)
    for job in jobs:
        try:
            output = handler(job)
            if not isinstance(output, dict) or output.get('external_execution') is not False:
                raise ValueError('Handler output must be a reviewable draft')
            with db() as conn:
                finish(conn, job['id'], 'succeeded')
            results.append({'id': job['id'], 'status': 'succeeded'})
        except Exception as exc:
            with db() as conn:
                finish(conn, job['id'], 'failed', exc)
            results.append({'id': job['id'], 'status': 'failed'})
    return results
