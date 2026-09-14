"""MCP server exposing Google Flights search via ``fast-flights``."""

from __future__ import annotations

import os
from datetime import UTC
from datetime import date as Date
from datetime import datetime as Datetime
from typing import Annotated, Literal

import anyio
from fast_flights import (
    FlightQuery,
    FlightsNotFound,
    Passengers,
    Query,
    create_query,
    get_flights,
)
from fast_flights.types import Currency, Language, SeatType, TripType
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from .fetching import ConsentingFetcher
from .models import Itinerary, SearchResult, itinerary_from

DEFAULT_MAX_RESULTS = 20

INSTRUCTIONS = """\
Search Google Flights for real, currently offered itineraries.

Use `search_flights` for one-way and round-trip searches. Airports are
three-letter IATA codes (e.g. 'OSL', 'JFK'); resolve city names to codes
before calling. Dates are 'YYYY-MM-DD' and must be in the future.

Prices are totals for all passengers. Results come from scraping Google
Flights, so they reflect what Google shows right now and may shift between
calls; always present them as indicative and link the returned
`google_flights_url` so the user can book.
"""

mcp = FastMCP(name="flights-mcp", instructions=INSTRUCTIONS)


class FlightLeg(BaseModel):
    """One leg of a journey, plus filters that apply to that leg."""

    date: Annotated[str, Field(description="Departure date in 'YYYY-MM-DD' format.")]
    from_airport: Annotated[
        str, Field(description="Origin IATA airport code, e.g. 'OSL'.")
    ]
    to_airport: Annotated[
        str, Field(description="Destination IATA airport code, e.g. 'JFK'.")
    ]
    max_stops: Annotated[
        int | None,
        Field(
            default=None, ge=0, description="Maximum stops. Use 0 for non-stop only."
        ),
    ] = None
    airlines: Annotated[
        list[str] | None,
        Field(
            default=None,
            description=(
                "Restrict to these airline IATA codes (e.g. ['SK', 'LH']) or "
                "alliances ('ONEWORLD', 'SKYTEAM', 'STAR_ALLIANCE'). Google "
                "applies the first leg's airline filter to the whole search."
            ),
        ),
    ] = None
    earliest_departure_hour: Annotated[
        int | None,
        Field(default=None, ge=0, le=23, description="Earliest local departure hour."),
    ] = None
    latest_departure_hour: Annotated[
        int | None,
        Field(default=None, ge=0, le=23, description="Latest local departure hour."),
    ] = None
    earliest_arrival_hour: Annotated[
        int | None,
        Field(default=None, ge=0, le=23, description="Earliest local arrival hour."),
    ] = None
    latest_arrival_hour: Annotated[
        int | None,
        Field(default=None, ge=0, le=23, description="Latest local arrival hour."),
    ] = None
    max_duration_minutes: Annotated[
        int | None,
        Field(default=None, gt=0, description="Maximum total travel time, in minutes."),
    ] = None
    connecting_airports: Annotated[
        list[str] | None,
        Field(
            default=None, description="Only connect through these IATA airport codes."
        ),
    ] = None
    min_layover_minutes: Annotated[
        int | None,
        Field(default=None, ge=0, description="Minimum layover, in minutes."),
    ] = None
    max_layover_minutes: Annotated[
        int | None,
        Field(default=None, ge=0, description="Maximum layover, in minutes."),
    ] = None
    less_emissions_only: Annotated[
        bool,
        Field(
            default=False, description="Only itineraries with lower-than-typical CO2."
        ),
    ] = False

    def to_flight_query(self) -> FlightQuery:
        return FlightQuery(
            date=_validate_date(self.date),
            from_airport=_validate_airport(self.from_airport),
            to_airport=_validate_airport(self.to_airport),
            max_stops=self.max_stops,
            airlines=[code.upper() for code in self.airlines]
            if self.airlines
            else None,
            earliest_departure_hour=self.earliest_departure_hour,
            latest_departure_hour=self.latest_departure_hour,
            earliest_arrival_hour=self.earliest_arrival_hour,
            latest_arrival_hour=self.latest_arrival_hour,
            max_duration_minutes=self.max_duration_minutes,
            connecting_airports=(
                [_validate_airport(code) for code in self.connecting_airports]
                if self.connecting_airports
                else None
            ),
            min_layover_minutes=self.min_layover_minutes,
            max_layover_minutes=self.max_layover_minutes,
            less_emissions_only=self.less_emissions_only,
        )


def _validate_airport(code: str) -> str:
    normalized = code.strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise ToolError(
            f"{code!r} is not a valid IATA airport code; expected three letters, e.g. 'OSL'."
        )
    return normalized


def _validate_date(value: str) -> str:
    try:
        parsed = Date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolError(
            f"{value!r} is not a valid date; expected 'YYYY-MM-DD'."
        ) from exc
    if parsed < Datetime.now(tz=UTC).date():
        raise ToolError(
            f"{value!r} is in the past; Google Flights only sells future travel."
        )
    return parsed.isoformat()


def _build_query(
    *,
    legs: list[FlightLeg],
    trip: TripType,
    seat: SeatType,
    adults: int,
    children: int,
    infants_in_seat: int,
    infants_on_lap: int,
    currency: str,
    language: str,
    max_price: int | None,
    carry_on_bags: int,
    checked_bags: int,
    hide_separate_and_self_transfer: bool,
    exclude_basic_economy: bool,
) -> Query:
    try:
        passengers = Passengers(
            adults=adults,
            children=children,
            infants_in_seat=infants_in_seat,
            infants_on_lap=infants_on_lap,
        )
    except AssertionError as exc:
        raise ToolError(str(exc)) from exc

    if adults + children + infants_in_seat + infants_on_lap == 0:
        raise ToolError("At least one passenger is required.")

    return create_query(
        flights=[leg.to_flight_query() for leg in legs],
        seat=seat,
        trip=trip,
        passengers=passengers,
        language=language,
        currency=currency,
        max_price=max_price,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        hide_separate_and_self_transfer=hide_separate_and_self_transfer,
        exclude_basic_economy=exclude_basic_economy,
    )


async def _run_search(
    query: Query,
    *,
    max_results: int,
    sort_by: Literal["price", "duration", "departure"],
) -> SearchResult:
    url = query.url()
    proxy = os.environ.get("FLIGHTS_MCP_PROXY") or None

    try:
        results = await anyio.to_thread.run_sync(
            lambda: get_flights(query, integration=ConsentingFetcher(proxy=proxy))
        )
    except FlightsNotFound as exc:
        raise ToolError(
            f"Google Flights returned no results for this search. See {url}"
        ) from exc
    except Exception as exc:  # network failure, layout change, rate limiting…
        raise ToolError(
            f"Could not fetch flights from Google Flights ({type(exc).__name__}: {exc}). "
            "This is usually a transient network issue or rate limiting; retrying "
            f"shortly often works. Search URL: {url}"
        ) from exc

    metadata = getattr(results, "metadata", None)
    itineraries = [itinerary_from(flight, metadata) for flight in results]

    currency = query.currency or None
    for itinerary in itineraries:
        itinerary.currency = currency

    itineraries.sort(key=_sort_key(sort_by))

    truncated = len(itineraries) > max_results
    itineraries = itineraries[:max_results]
    prices = [i.price for i in itineraries if i.price is not None]

    return SearchResult(
        itineraries=itineraries,
        count=len(itineraries),
        cheapest_price=min(prices) if prices else None,
        currency=currency,
        google_flights_url=url,
        truncated=truncated,
    )


def _sort_key(sort_by: Literal["price", "duration", "departure"]):
    """Build a sort key that always pushes missing values to the end."""
    if sort_by == "duration":
        return lambda i: (
            i.total_duration_minutes is None,
            i.total_duration_minutes or 0,
        )
    if sort_by == "departure":
        return lambda i: (i.departure is None, i.departure or Date.max)
    return lambda i: (i.price is None, i.price or 0)


@mcp.tool(
    annotations={"readOnlyHint": True, "openWorldHint": True},
    tags={"flights", "search"},
)
async def search_flights(
    from_airport: Annotated[
        str, Field(description="Origin IATA airport code, e.g. 'OSL'.")
    ],
    to_airport: Annotated[
        str, Field(description="Destination IATA airport code, e.g. 'JFK'.")
    ],
    departure_date: Annotated[
        str, Field(description="Outbound date in 'YYYY-MM-DD' format.")
    ],
    return_date: Annotated[
        str | None,
        Field(
            description=(
                "Return date in 'YYYY-MM-DD' format. Omit for a one-way search; "
                "providing it makes the search a round-trip."
            )
        ),
    ] = None,
    adults: Annotated[int, Field(ge=0, le=9, description="Adult passengers.")] = 1,
    children: Annotated[int, Field(ge=0, le=9, description="Children aged 2-11.")] = 0,
    infants_in_seat: Annotated[
        int, Field(ge=0, le=9, description="Infants occupying their own seat.")
    ] = 0,
    infants_on_lap: Annotated[
        int,
        Field(ge=0, le=9, description="Infants on a lap. Requires one adult each."),
    ] = 0,
    seat: Annotated[SeatType, Field(description="Cabin class.")] = "economy",
    max_stops: Annotated[
        int | None,
        Field(ge=0, description="Maximum stops per leg. Use 0 for non-stop only."),
    ] = None,
    airlines: Annotated[
        list[str] | None,
        Field(
            description=(
                "Restrict to these airline IATA codes (e.g. ['SK', 'LH']) or "
                "alliances ('ONEWORLD', 'SKYTEAM', 'STAR_ALLIANCE')."
            )
        ),
    ] = None,
    max_price: Annotated[
        int | None,
        Field(gt=0, description="Maximum total price, in the requested currency."),
    ] = None,
    carry_on_bags: Annotated[
        int, Field(ge=0, description="Carry-on bags to include in the price estimate.")
    ] = 0,
    checked_bags: Annotated[
        int, Field(ge=0, description="Checked bags to include in the price estimate.")
    ] = 0,
    exclude_basic_economy: Annotated[
        bool, Field(description="Exclude basic economy fares.")
    ] = False,
    hide_separate_and_self_transfer: Annotated[
        bool,
        Field(description="Hide separate-ticket and self-transfer itineraries."),
    ] = False,
    currency: Annotated[
        Currency | Literal[""],
        Field(description="Currency for prices. Empty lets Google decide."),
    ] = "",
    language: Annotated[
        Language | Literal[""],
        Field(description="Language for names. Empty lets Google decide."),
    ] = "",
    sort_by: Annotated[
        Literal["price", "duration", "departure"],
        Field(description="How to order the returned itineraries."),
    ] = "price",
    max_results: Annotated[
        int, Field(ge=1, le=100, description="Maximum itineraries to return.")
    ] = DEFAULT_MAX_RESULTS,
) -> SearchResult:
    """Search Google Flights for one-way or round-trip flights.

    Omit `return_date` for a one-way search. Prices are totals for all
    passengers in the requested currency. Results are live scrapes of Google
    Flights and may change between calls, so treat them as indicative and share
    the returned `google_flights_url` for booking.
    """
    legs = [
        FlightLeg(
            date=departure_date,
            from_airport=from_airport,
            to_airport=to_airport,
            max_stops=max_stops,
            airlines=airlines,
        )
    ]
    if return_date is not None:
        legs.append(
            FlightLeg(
                date=return_date,
                from_airport=to_airport,
                to_airport=from_airport,
                max_stops=max_stops,
                airlines=airlines,
            )
        )
        if _validate_date(return_date) < _validate_date(departure_date):
            raise ToolError("`return_date` must not be before `departure_date`.")

    query = _build_query(
        legs=legs,
        trip="round-trip" if return_date is not None else "one-way",
        seat=seat,
        adults=adults,
        children=children,
        infants_in_seat=infants_in_seat,
        infants_on_lap=infants_on_lap,
        currency=currency,
        language=language,
        max_price=max_price,
        carry_on_bags=carry_on_bags,
        checked_bags=checked_bags,
        hide_separate_and_self_transfer=hide_separate_and_self_transfer,
        exclude_basic_economy=exclude_basic_economy,
    )
    return await _run_search(query, max_results=max_results, sort_by=sort_by)


__all__ = ["FlightLeg", "Itinerary", "SearchResult", "mcp"]
