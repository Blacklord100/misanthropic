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


def test_requests_in_flight_tracks_governor():
    assert server.requests_in_flight() == 0
    server._governor.acquire()
    try:
        assert server.requests_in_flight() == 1
    finally:
        server._governor.release()
    assert server.requests_in_flight() == 0


def test_governor_resize_wakes_waiters():
    import threading as _t
    import time as _time
    g = server.Governor(1)
    assert g.acquire(timeout=0.1)
    assert not g.acquire(timeout=0.05)        # full
    got = []
    t = _t.Thread(target=lambda: got.append(g.acquire(timeout=2)))
    t.start()
    _time.sleep(0.05)
    g.set_limit(2)                            # raising the limit frees the waiter
    t.join(timeout=2)
    assert got == [True]
    assert g.in_flight == 2
    g.release(); g.release()
    assert g.in_flight == 0
    assert g.set_limit(999) == 64             # clamped
