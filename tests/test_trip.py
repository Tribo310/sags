import asyncio

from app import mcp_server


class FakeOSRM:
    """Home + stores A, B; OSRM visits B first: waypoint_index says where each input point sits in the trip."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url, params):
        class R:
            def json(self):
                return {"waypoints": [{"waypoint_index": 0}, {"waypoint_index": 2}, {"waypoint_index": 1}],
                        "trips": [{"distance": 12000, "duration": 1800, "geometry": {"coordinates": [[0, 0]]},
                                   "legs": [{"distance": 3000, "duration": 300}, {"distance": 4000, "duration": 600},
                                            {"distance": 5000, "duration": 900}]}]}
        return R()


def test_stops_follow_osrm_order_with_their_legs(monkeypatch):
    async def branches(lon, lat, stores):
        return {"branches": [{"store": s, "name": s, "location": {"coordinates": [i, i]}} for i, s in enumerate(stores)]}
    monkeypatch.setattr(mcp_server, "nearest_branches", branches)
    monkeypatch.setattr(mcp_server.httpx, "AsyncClient", lambda **_: FakeOSRM())
    trip = asyncio.run(mcp_server.plan_trip(9, 9, ["a", "b"]))
    assert [(s["store"], s["km"], s["minutes"]) for s in trip["stops"]] == [("b", 3.0, 5), ("a", 4.0, 10)]
    assert (trip["km"], trip["minutes"], trip["home"]) == (12.0, 30, [9, 9])
