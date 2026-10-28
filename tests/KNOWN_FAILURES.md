# Known failing tests (baseline recorded 2026-10-28)

These 21 tests fail on `main` before the situation-snapshot work and are unchanged by it. Most (18) fail because the
test settings object (`SimpleNamespace`) has no `tavily_api_key`, which `tenant.build_registry` reads since commit
cbac515 (operator-set Tavily key). `test_users.py::test_init_schema_on_an_upgraded_database_runs_no_add_column` expects
no statements but `init_schema` now also deletes legacy `deepgram_key`/`tavily_key` rows.
`test_isolation.py::test_unlisted_and_disabled_users_refused_on_every_route` also lists routes missing from its table
(`/shopping`, `/calendar/events/{id}`, ...). Fix the fixtures, then delete this file.

Run: `TEST_DATABASE_URL=postgresql://jarvis:jarvis@localhost:5432/jarvis_test pytest -q` -> `21 failed` expected.

- tests/test_appapi.py::test_app_endpoints_use_callers_google_clients
- tests/test_appapi.py::test_chat_uses_user_thread_and_lock
- tests/test_appapi.py::test_disabled_user_gets_401_on_next_request
- tests/test_appapi.py::test_note_of_other_user_404
- tests/test_appapi.py::test_two_users_chat_concurrently_not_serialised
- tests/test_dashboard.py::test_dashboard_scoped
- tests/test_dashboard.py::test_server_weather_city_is_the_owners_only
- tests/test_isolation.py::test_unlisted_and_disabled_users_refused_on_every_route
- tests/test_keys.py::test_bad_timezone_rejected
- tests/test_keys.py::test_display_name_persists_and_is_isolated
- tests/test_keys.py::test_display_name_validation_is_atomic
- tests/test_keys.py::test_http_url_and_localhost_rejected_422
- tests/test_keys.py::test_key_is_stripped_of_whitespace_and_newline
- tests/test_keys.py::test_llm_save_waits_for_the_users_advisory_lock
- tests/test_keys.py::test_me_never_returns_secret_values
- tests/test_keys.py::test_model_ids_capped_and_shaped
- tests/test_keys.py::test_non_ascii_key_is_rejected_key_not_500
- tests/test_keys.py::test_port_zero_and_numeric_ip_hosts_rejected
- tests/test_keys.py::test_put_llm_failure_saves_nothing_and_names_model
- tests/test_keys.py::test_put_llm_saves_only_after_passing_test
- tests/test_users.py::test_init_schema_on_an_upgraded_database_runs_no_add_column
