"""Query classes — all database access goes through these.

Direct ``session.execute()`` / ``session.add()`` is BANNED outside
this package and ``db/session.py``. The CI check in
``ci/check_tenant_isolation.sh`` enforces this.
"""
