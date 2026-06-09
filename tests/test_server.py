import pytest

from misanthropic import server


@pytest.mark.parametrize("body,expected", [
    ({"tools": [{"type": "web_search_20260209", "name": "web_search"}]}, True),
    ({"tools": [{"type": "web_search_20250305", "name": "web_search"}]}, True),
    ({"tools": [{"type": "custom", "name": "foo"}]}, False),
    ({"tools": []}, False),
    ({}, False),
    ({"tools": "not-a-list"}, False),
    ({"tools": [{"type": "web_search_x"}, {"type": "other"}]}, True),
])
def test_request_wants_web(body, expected):
    assert server._request_wants_web(body) is expected


@pytest.mark.parametrize("linked,web,expected", [
    (False, False, "stateless"),
    (False, True, "web"),
    (True, False, "session"),
    (True, True, "session+web"),
])
def test_request_mode(linked, web, expected):
    # _request_mode doesn't touch self; call it unbound with a dummy.
    assert server.Handler._request_mode(None, linked, web) == expected
