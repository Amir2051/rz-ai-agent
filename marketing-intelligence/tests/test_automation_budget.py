import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from marketing import automation, ai_budget, rz_runtime


class Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows=None, many=None):
        self.rows = rows or {}
        self.many = many or {}
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        for key, value in self.rows.items():
            if key in sql:
                return Result(value)
        return Result(None, self.many.get(next((k for k in self.many if k in sql), ''), []))


W = '10000000-0000-4000-8000-000000000001'
U = '20000000-0000-4000-8000-000000000002'
J = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


class AutomationBudget(unittest.TestCase):
    def test_schedule_rejects_external_effects(self):
        conn = FakeConn()
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        with self.assertRaises(ValueError):
            automation.schedule(conn, U, W, 'report', {'external_execution': True}, run_at)
        with self.assertRaises(ValueError):
            automation.schedule(conn, U, W, 'publish', {}, run_at)
        with self.assertRaises(ValueError):
            automation.schedule(conn, U, W, 'report', {}, datetime.now())

    def test_schedule_persists_pending_job(self):
        conn = FakeConn({'INSERT INTO marketing_jobs': (J,)})
        result = automation.schedule(conn, U, W, 'report', {'scope': 'synthetic'},
                                     datetime.now(timezone.utc) + timedelta(hours=1))
        self.assertEqual(result, {'id': J, 'status': 'pending', 'external_execution': False})

    def test_claim_and_finish(self):
        job = (J, W, U, 'report', {'scope': 'synthetic'}, 1, 3)
        conn = FakeConn(
            {'UPDATE marketing_jobs SET status': (J,)},
            {'FROM marketing_jobs WHERE status': [job]},
        )
        jobs = automation.claim(conn, 5)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['kind'], 'report')
        automation.finish(conn, J, 'succeeded')
        self.assertTrue(any('automation_finish' in sql for sql, _ in conn.calls))

    def test_handler_must_return_reviewable_draft(self):
        def bad_handler(job):
            return {'external_execution': True}
        job = (J, W, U, 'report', {'scope': 'synthetic'}, 1, 3)
        conn = FakeConn(
            {'SELECT automation_recover': (0,), 'UPDATE marketing_jobs SET status': (J,)},
            {'FROM marketing_jobs WHERE status': [job]},
        )
        results = automation.run_due(lambda: _ctx(conn), bad_handler)
        self.assertEqual(results, [{'id': J, 'status': 'failed'}])

    def test_reserve_release_settle(self):
        conn = FakeConn({'UPDATE ai_daily_budget SET reserved_microusd=reserved_microusd+': (40,),
                         'UPDATE ai_daily_budget SET reserved_microusd=reserved_microusd-': (20,)})
        self.assertEqual(ai_budget.reserve(conn, W, 40), 40)
        self.assertEqual(ai_budget.release(conn, W, 20), 20)
        self.assertEqual(ai_budget.settle(conn, W, 20), 20)

    def test_director_requires_ledger_for_live_inference(self):
        policy = None
        with self.assertRaises(PermissionError):
            rz_runtime.run_director(W, {W}, policy, 'synthetic', 1, 'synthetic', None, enabled=True)

    def test_director_never_executes_external_actions(self):
        class Client:
            def post(self, *args, **kwargs):
                raise AssertionError('network disabled')
        from marketing.ai_security import ModelPolicy
        from decimal import Decimal
        policy = ModelPolicy(provider='openrouter', model='synthetic', max_output=32,
                             max_input_bytes=10000, input_per_million=Decimal('0'),
                             output_per_million=Decimal('0'), privacy_verified=True)
        with self.assertRaises(PermissionError):
            rz_runtime.run_director(W, {W}, policy, 'synthetic', 1, 'synthetic', Client(), enabled=False)


class _ctx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *args):
        return False


if __name__ == '__main__':
    unittest.main()
