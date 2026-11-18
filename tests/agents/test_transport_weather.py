from datetime import timedelta

import respx

from app.agents.transport import transport_node
from app.agents.weather_node import weather_node
from app.schemas.place import GeoPoint, Source
from app.schemas.trip import TripRequest

from ..conftest import load_fixture
from ..fakes import START, install_repo, mock_http

G = load_fixture("nominatim_guwahati.json")[0]
S = load_fixture("nominatim_shillong.json")[0]


def gp(d, tier=2):
    return GeoPoint(name=d["name"], lat=float(d["lat"]), lon=float(d["lon"]), tier=tier, source=Source(name="t"),
                    confidence="verified")


REQ = TripRequest(origin="Guwahati", destination="Shillong", start_date=START, end_date=START + timedelta(days=2),
                  travellers=2, budget_inr=8000)


async def test_transport_options_from_cost_table(monkeypatch):
    install_repo(monkeypatch)
    with respx.mock(assert_all_called=False) as r:
        mock_http(r)
        out = await transport_node({"request": REQ, "origin_geo": gp(G), "dest_geo": gp(S)})
    opts = out["intercity_options"]
    assert {o.mode for o in opts} == {"state_bus", "shared_taxi", "private_cab"}
    assert opts == sorted(opts, key=lambda o: o.total_inr)
    bus = next(o for o in opts if o.mode == "state_bus")
    cab = next(o for o in opts if o.mode == "private_cab")
    assert bus.minutes > cab.minutes  # duration factor from the table, not a code constant
    assert all(o.source.name and o.leg_source.name.startswith("OSRM") for o in opts)


async def test_local_trip_has_no_intercity(monkeypatch):
    install_repo(monkeypatch)
    with respx.mock(assert_all_called=False) as r:
        mock_http(r)
        out = await transport_node({"request": REQ, "origin_geo": gp(S), "dest_geo": gp(S)})
    assert out["intercity_options"] == []


async def test_weather_node_ok_and_failure(mem_cache):
    with respx.mock(assert_all_called=False) as r:
        mock_http(r)
        out = await weather_node({"request": REQ, "dest_geo": gp(S)})
    assert [w.date for w in out["weather"]] == [START + timedelta(days=i) for i in range(3)]
    mem_cache.data.clear()
    with respx.mock(assert_all_called=False) as r:
        r.get(url__regex=r".*").respond(500)
        out = await weather_node({"request": REQ, "dest_geo": gp(S)})
    assert out["weather"] == [] and out["warnings"]
