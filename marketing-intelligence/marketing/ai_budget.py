"""Atomic, durable budget reservation with release and settlement."""
from datetime import date, datetime, timezone


def _day():
    return datetime.now(timezone.utc).date()


def _amount(value):
    if type(value) is not int or not 0 < value <= 10 ** 12:
        raise ValueError('Invalid budget amount')
    return value


def reserve(conn, workspace, microusd):
    """Atomically reserve budget; never overspends under concurrency."""
    microusd = _amount(microusd)
    row = conn.execute(
        "UPDATE ai_daily_budget SET reserved_microusd=reserved_microusd+%s "
        "WHERE workspace_id=%s AND day=(now() AT TIME ZONE 'UTC')::date "
        "AND reserved_microusd<=limit_microusd-%s RETURNING reserved_microusd",
        (microusd, workspace, microusd),
    ).fetchone()
    if row is None:
        raise PermissionError('Budget unavailable or exceeded')
    return row[0]


def release(conn, workspace, microusd):
    """Return an unused reservation after a failed or ambiguous call."""
    microusd = _amount(microusd)
    row = conn.execute(
        "UPDATE ai_daily_budget SET reserved_microusd=reserved_microusd-%s "
        "WHERE workspace_id=%s AND day=(now() AT TIME ZONE 'UTC')::date "
        "AND reserved_microusd>=%s RETURNING reserved_microusd",
        (microusd, workspace, microusd),
    ).fetchone()
    if row is None:
        raise PermissionError('No reservation to release')
    return row[0]


def settle(conn, workspace, microusd):
    """Convert a reservation into recorded spend (reservation is consumed)."""
    return release(conn, workspace, microusd)
