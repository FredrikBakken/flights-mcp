from datetime import UTC, datetime, timedelta

import pytest
from fast_flights.model import (
    Airline,
    Airport,
    CarbonEmission,
    Flights,
    JsMetadata,
    SimpleDatetime,
    SingleFlight,
)
from fastmcp import Client
from fastmcp.exceptions import ToolError

from flights_mcp.fetching import CONSENT_COOKIE, ConsentingFetcher
from flights_mcp.models import itinerary_from
from flights_mcp.server import FlightLeg, _validate_airport, _validate_date, mcp


@pytest.fixture
def anyio_backend():
    return "asyncio"


def future(days: int = 30) -> str:
    return (datetime.now(tz=UTC).date() + timedelta(days=days)).isoformat()


def make_flights() -> Flights:
    return Flights(
        type="Nonstop",
        price=420,
        airlines=["SK"],
        flights=[
            SingleFlight(
                from_airport=Airport(code="OSL", name="Oslo Gardermoen"),
                to_airport=Airport(code="LHR", name="London Heathrow"),
                departure=SimpleDatetime(date=(2030, 5, 1), time=(8, 30)),
                arrival=SimpleDatetime(date=(2030, 5, 1), time=(10, 20)),
                duration=110,
                plane_type="Airbus A320",
            )
        ],
        carbon=CarbonEmission(typical_on_route=200_000, emission=150_000),
    )


class TestValidation:
    def test_airport_is_normalized(self):
        assert _validate_airport(" osl ") == "OSL"

    @pytest.mark.parametrize("code", ["OSLO", "O1L", "", "os"])
    def test_bad_airport_rejected(self, code):
        with pytest.raises(ToolError):
            _validate_airport(code)

    def test_date_is_normalized(self):
        assert _validate_date(f" {future()} ") == future()

    def test_past_date_rejected(self):
        with pytest.raises(ToolError, match="in the past"):
            _validate_date("2020-01-01")

    def test_malformed_date_rejected(self):
        with pytest.raises(ToolError, match="not a valid date"):
            _validate_date("next tuesday")


class TestConversion:
    def test_itinerary_fields(self):
        metadata = JsMetadata(airlines=[Airline(code="SK", name="SAS")], alliances=[])
        itinerary = itinerary_from(make_flights(), metadata)

        assert itinerary.price == 420
        assert itinerary.airlines == ["SAS"]
        assert itinerary.stops == 0
        assert itinerary.total_duration == "1h 50m"
        assert itinerary.departure is not None
        assert itinerary.arrival is not None
        assert itinerary.departure.isoformat() == "2030-05-01T08:30:00"
        assert itinerary.arrival.isoformat() == "2030-05-01T10:20:00"
        assert itinerary.legs[0].duration == "1h 50m"
        assert itinerary.legs[0].from_airport.code == "OSL"

    def test_total_duration_uses_segments_and_layovers(self):
        """Local arrival/departure times cannot be subtracted across timezones.

        OSL 12:00 -> LHR 13:00 local is 2h of flying (LHR is an hour behind),
        then a 1h layover, then a 3h segment: 6h total, not the 4h a naive
        last-arrival-minus-first-departure calculation would give.
        """
        flights = Flights(
            type="1 stop",
            price=500,
            airlines=["SK"],
            flights=[
                SingleFlight(
                    from_airport=Airport(code="OSL", name="Oslo"),
                    to_airport=Airport(code="LHR", name="London Heathrow"),
                    departure=SimpleDatetime(date=(2030, 5, 1), time=(12, 0)),
                    arrival=SimpleDatetime(date=(2030, 5, 1), time=(13, 0)),
                    duration=120,
                    plane_type="Airbus A320",
                ),
                SingleFlight(
                    from_airport=Airport(code="LHR", name="London Heathrow"),
                    to_airport=Airport(code="JFK", name="New York JFK"),
                    departure=SimpleDatetime(date=(2030, 5, 1), time=(14, 0)),
                    arrival=SimpleDatetime(date=(2030, 5, 1), time=(16, 0)),
                    duration=180,
                    plane_type="Boeing 777",
                ),
            ],
            carbon=CarbonEmission(typical_on_route=1, emission=1),
        )
        itinerary = itinerary_from(flights, None)

        assert itinerary.stops == 1
        assert itinerary.total_duration_minutes == 360
        assert itinerary.total_duration == "6h"

    def test_carbon_difference(self):
        itinerary = itinerary_from(make_flights(), None)
        assert itinerary.carbon is not None
        assert itinerary.carbon.difference_percent == -25

    def test_unknown_airline_code_passes_through(self):
        itinerary = itinerary_from(
            make_flights(), JsMetadata(airlines=[], alliances=[])
        )
        assert itinerary.airlines == ["SK"]


class TestFetching:
    def test_consent_cookie_is_set(self):
        assert "SOCS" in CONSENT_COOKIE

    def test_consent_redirect_is_reported(self, monkeypatch):
        class FakeResponse:
            url = "https://consent.google.com/m?continue=..."
            text = "<html></html>"

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def set_cookies(self, *args, **kwargs):
                pass

            def get(self, *args, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("flights_mcp.fetching.Client", FakeClient)
        with pytest.raises(RuntimeError, match="consent page"):
            ConsentingFetcher().fetch_html("OSL to LHR")


class TestLegQuery:
    def test_builds_flight_query(self):
        leg = FlightLeg(
            date=future(),
            from_airport="osl",
            to_airport="jfk",
            airlines=["sk"],
            max_stops=0,
            connecting_airports=["cph"],
        )
        query = leg.to_flight_query()

        assert (query.from_airport, query.to_airport) == ("OSL", "JFK")
        assert query.airlines == ["SK"]
        assert query.connecting_airports == ["CPH"]
        assert query.max_stops == 0


class TestTools:
    @pytest.mark.anyio
    async def test_tools_are_registered(self, anyio_backend):
        async with Client(mcp) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert names == {"search_flights"}

    @pytest.mark.anyio
    async def test_return_before_departure_is_rejected(self, anyio_backend):
        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="must not be before"):
                await client.call_tool(
                    "search_flights",
                    {
                        "from_airport": "OSL",
                        "to_airport": "JFK",
                        "departure_date": future(30),
                        "return_date": future(10),
                    },
                )

    @pytest.mark.anyio
    async def test_invalid_airport_is_rejected(self, anyio_backend):
        async with Client(mcp) as client:
            with pytest.raises(ToolError):
                await client.call_tool(
                    "search_flights",
                    {
                        "from_airport": "Oslo",
                        "to_airport": "JFK",
                        "departure_date": future(),
                    },
                )
