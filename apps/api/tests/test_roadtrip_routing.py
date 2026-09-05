"""The routing client. No network: every response is a stub, because the
thing worth testing is how PrintOps reads an answer, not whether a public
demo server was up when CI ran."""

import httpx
import pytest

from app.roadtrip.routing import (
    MAX_GEOMETRY_POINTS,
    DrivingRoute,
    RoutingError,
    _thin,
    fetch_driving_route,
)

BROOKFIELD = (39.7864, -93.0735)
JEFFERSON_CITY = (38.5767, -92.1735)


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch):
    """Swaps httpx.AsyncClient for one wired to a stub transport."""

    def install(handler):
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = _transport(handler)
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    return install


def _ok(distance_metres=197260.3, coordinates=None):
    coordinates = coordinates or [[-93.0735, 39.7864], [-92.6, 39.2], [-92.1735, 38.5767]]
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": distance_metres,
                "geometry": {"type": "LineString", "coordinates": coordinates},
            }
        ],
    }


async def test_a_route_comes_back_in_miles_and_latitude_first(patched_client):
    """OSRM answers in metres and longitude-first. Both conversions happen
    once, here, rather than at every caller that would have to remember."""
    patched_client(lambda request: httpx.Response(200, json=_ok()))

    route = await fetch_driving_route(
        base_url="https://routing.test", origin=BROOKFIELD, destination=JEFFERSON_CITY
    )
    assert isinstance(route, DrivingRoute)
    assert route.miles == 122.6
    assert route.geometry[0] == [39.7864, -93.0735]
    assert route.geometry[-1] == [38.5767, -92.1735]


async def test_the_drive_is_longer_than_the_crow_flies(patched_client):
    """The whole point: 96 miles as the crow flies, 123 by road."""
    patched_client(lambda request: httpx.Response(200, json=_ok()))
    route = await fetch_driving_route(
        base_url="https://routing.test", origin=BROOKFIELD, destination=JEFFERSON_CITY
    )
    assert route.miles > 96.0


async def test_a_trailing_slash_on_the_base_url_is_harmless(patched_client):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json=_ok())

    patched_client(handler)
    await fetch_driving_route(
        base_url="https://routing.test/", origin=BROOKFIELD, destination=JEFFERSON_CITY
    )
    assert "//route" not in seen["path"]


async def test_osrm_reports_no_route_with_a_200(patched_client):
    """The branch that matters most: OSRM puts its own failures in the
    body. Two points with no road between them — an island, another
    continent, the Moon — answer 200 with code NoRoute."""
    patched_client(
        lambda request: httpx.Response(200, json={"code": "NoRoute", "message": "no route"})
    )
    with pytest.raises(RoutingError, match="NoRoute"):
        await fetch_driving_route(
            base_url="https://routing.test", origin=BROOKFIELD, destination=(21.4, -157.9)
        )


async def test_an_http_error_is_a_routing_error(patched_client):
    patched_client(lambda request: httpx.Response(503, text="busy"))
    with pytest.raises(RoutingError, match="503"):
        await fetch_driving_route(
            base_url="https://routing.test", origin=BROOKFIELD, destination=JEFFERSON_CITY
        )


async def test_an_unreachable_service_is_a_routing_error(patched_client):
    def handler(request):
        raise httpx.ConnectError("refused")

    patched_client(handler)
    with pytest.raises(RoutingError, match="Could not reach"):
        await fetch_driving_route(
            base_url="https://routing.test", origin=BROOKFIELD, destination=JEFFERSON_CITY
        )


async def test_a_route_with_no_shape_is_refused(patched_client):
    patched_client(
        lambda request: httpx.Response(
            200,
            json={"code": "Ok", "routes": [{"distance": 100.0, "geometry": {"coordinates": []}}]},
        )
    )
    with pytest.raises(RoutingError, match="no shape"):
        await fetch_driving_route(
            base_url="https://routing.test", origin=BROOKFIELD, destination=JEFFERSON_CITY
        )


async def test_non_json_is_refused(patched_client):
    patched_client(lambda request: httpx.Response(200, text="<html>proxy login</html>"))
    with pytest.raises(RoutingError, match="wasn't JSON"):
        await fetch_driving_route(
            base_url="https://routing.test", origin=BROOKFIELD, destination=JEFFERSON_CITY
        )


async def test_a_long_route_is_thinned(patched_client):
    many = [[-93.0 + index * 0.001, 39.0 + index * 0.001] for index in range(5000)]
    patched_client(lambda request: httpx.Response(200, json=_ok(coordinates=many)))
    route = await fetch_driving_route(
        base_url="https://routing.test", origin=BROOKFIELD, destination=JEFFERSON_CITY
    )
    assert len(route.geometry) == MAX_GEOMETRY_POINTS


def test_thinning_keeps_both_ends():
    points = [[float(index), float(index)] for index in range(1000)]
    thinned = _thin(points, limit=10)
    assert thinned[0] == points[0]
    assert thinned[-1] == points[-1]
    assert len(thinned) == 10


def test_a_short_route_is_left_alone():
    points = [[0.0, 0.0], [1.0, 1.0]]
    assert _thin(points, limit=10) == points
