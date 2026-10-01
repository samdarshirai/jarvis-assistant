import os

import pytest


@pytest.fixture
def pool():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    from jarvis.db import init_schema, make_pool

    p = make_pool(url)
    init_schema(p)
    with p.connection() as c:
        c.execute("TRUNCATE audit_log, oauth_tokens")
    yield p
    p.close()
