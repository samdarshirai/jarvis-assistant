import os
from urllib.parse import urlparse

import pytest


def db_name_is_test(url: str) -> bool:
    name = urlparse(url).path.lstrip("/")
    return name.endswith("_test") and name != "_test"


@pytest.fixture
def pool():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    if not db_name_is_test(url):
        pytest.skip("TEST_DATABASE_URL must point at a database whose name ends in _test "
                    "(refusing to TRUNCATE a real DB)")
    import psycopg
    from psycopg import sql

    from jarvis.db import init_schema, make_pool

    name = urlparse(url).path.lstrip("/")
    with psycopg.connect(urlparse(url)._replace(path="/postgres").geturl(), autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    p = make_pool(url)
    init_schema(p)
    with p.connection() as c:
        c.execute("TRUNCATE audit_log, oauth_tokens, devices")
    yield p
    p.close()
