import pytest

from tests.conftest import db_name_is_test


@pytest.mark.parametrize("url,ok", [
    ("postgresql://jarvis:jarvis@localhost:5432/jarvis_test", True),
    ("postgresql://jarvis:jarvis@localhost:5432/jarvis_test?sslmode=disable", True),
    ("postgresql://jarvis:jarvis@localhost:5432/jarvis", False),
    ("postgresql://jarvis:jarvis@localhost:5432/jarvis?options=_test", False),
    ("postgresql://jarvis:jarvis@localhost:5432/", False),
    ("postgresql://jarvis:jarvis@localhost:5432", False),
    ("postgresql://jarvis:jarvis@localhost:5432/_test", False),
])
def test_guard(url, ok):
    assert db_name_is_test(url) is ok
