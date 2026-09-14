"""Pydantic models describing the data returned by the MCP tools.

``fast_flights`` returns plain dataclasses with fairly terse, positional-ish
data (e.g. dates as ``(year, month, day)`` tuples). These models reshape that
into something an LLM can read without guessing: ISO dates, resolved airline
names, and human-friendly durations.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime as Datetime
from datetime import time as Time
from itertools import pairwise

from fast_flights.model import Flights, JsMetadata, SimpleDatetime, SingleFlight
from pydantic import BaseModel, Field


def _format_duration(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


class Airport(BaseModel):
    """An airport as reported by Google Flights."""

    code: str = Field(description="IATA airport code, e.g. 'OSL'.")
    name: str = Field(description="Human readable airport name.")


class Leg(BaseModel):
    """A single operated flight segment within an itinerary."""

    from_airport: Airport
    to_airport: Airport
    departure: Datetime = Field(description="Local departure time at the origin.")
    arrival: Datetime = Field(description="Local arrival time at the destination.")
    duration_minutes: int | None = Field(
        default=None, description="Segment duration in minutes."
    )
    duration: str | None = Field(
        default=None, description="Segment duration, e.g. '2h 15m'."
    )
    plane_type: str | None = Field(
        default=None, description="Aircraft type, when Google reports it."
    )


class CarbonEmission(BaseModel):
    """Carbon emission estimates, in grams of CO2 per passenger."""

    emission_grams: int | None = Field(
        default=None, description="Estimated emissions for this itinerary, in grams."
    )
    typical_on_route_grams: int | None = Field(
        default=None, description="Typical emissions for this route, in grams."
    )
    difference_percent: int | None = Field(
        default=None,
        description=(
            "Percent difference versus the typical flight on this route. "
            "Negative means this itinerary emits less than average."
        ),
    )


class Itinerary(BaseModel):
    """One bookable option returned by Google Flights."""

    price: int | None = Field(
        default=None,
        description=(
            "Total price for all passengers, in the requested currency. "
            "None when Google did not report a price."
        ),
    )
    currency: str | None = Field(
        default=None,
        description=(
            "Currency of `price`. None when the search let Google pick the "
            "currency based on locale, in which case it is usually USD."
        ),
    )
    airlines: list[str] = Field(
        default_factory=list, description="Marketing airline names."
    )
    stops: int = Field(description="Number of stops (segments - 1).")
    total_duration_minutes: int | None = Field(
        default=None,
        description=(
            "Total travel time in minutes, layovers included. Computed from "
            "segment durations plus layovers, so it is timezone-correct."
        ),
    )
    total_duration: str | None = Field(
        default=None, description="Total travel time, e.g. '11h 40m'."
    )
    departure: Datetime | None = Field(
        default=None, description="Local departure time of the first segment."
    )
    arrival: Datetime | None = Field(
        default=None, description="Local arrival time of the final segment."
    )
    legs: list[Leg] = Field(default_factory=list)
    carbon: CarbonEmission | None = None


class SearchResult(BaseModel):
    """The full response of a flight search."""

    itineraries: list[Itinerary] = Field(default_factory=list)
    count: int = Field(description="Number of itineraries in this response.")
    cheapest_price: int | None = Field(
        default=None, description="Lowest price among the returned itineraries."
    )
    currency: str | None = None
    google_flights_url: str = Field(
        description="Google Flights URL reproducing this exact search."
    )
    truncated: bool = Field(
        default=False,
        description="True when more itineraries were available than were returned.",
    )


def _to_datetime(value: SimpleDatetime) -> Datetime:
    year, month, day = value.date
    hour, minute = value.time
    return Datetime.combine(Date(year, month, day), Time(hour, minute))


def _airline_names(codes: list[str], metadata: JsMetadata | None) -> list[str]:
    """Resolve airline codes to names, falling back to the raw code.

    Google sometimes already hands back names rather than codes, so anything
    that does not look like a code is passed through untouched.
    """
    lookup = {a.code: a.name for a in metadata.airlines} if metadata else {}
    return [lookup.get(code, code) for code in codes]


def _leg_from(single: SingleFlight) -> Leg:
    return Leg(
        from_airport=Airport(
            code=single.from_airport.code, name=single.from_airport.name
        ),
        to_airport=Airport(code=single.to_airport.code, name=single.to_airport.name),
        departure=_to_datetime(single.departure),
        arrival=_to_datetime(single.arrival),
        duration_minutes=single.duration,
        duration=_format_duration(single.duration),
        plane_type=single.plane_type or None,
    )


def _carbon_from(flights: Flights) -> CarbonEmission | None:
    carbon = flights.carbon
    if carbon is None:
        return None

    emission = carbon.emission
    typical = carbon.typical_on_route
    difference = (
        round((emission - typical) / typical * 100)
        if emission is not None and typical
        else None
    )
    return CarbonEmission(
        emission_grams=emission,
        typical_on_route_grams=typical,
        difference_percent=difference,
    )


def _total_duration_minutes(legs: list[Leg]) -> int | None:
    """Total travel time across all legs, including layovers.

    Departure/arrival times are *local* to their airports, so subtracting the
    final arrival from the first departure gives a wrong answer whenever the
    itinerary crosses timezones. Segment durations are already absolute, and a
    layover is measured at a single airport (hence a single timezone), so
    summing the two is correct.
    """
    if not legs:
        return None
    if any(leg.duration_minutes is None for leg in legs):
        return None

    total = sum(leg.duration_minutes or 0 for leg in legs)
    for previous, following in pairwise(legs):
        layover = (following.departure - previous.arrival).total_seconds() / 60
        total += round(layover)
    return total


def itinerary_from(flights: Flights, metadata: JsMetadata | None) -> Itinerary:
    """Convert a ``fast_flights`` result into an :class:`Itinerary`."""
    legs = [_leg_from(single) for single in flights.flights]
    total_minutes = _total_duration_minutes(legs)

    return Itinerary(
        price=flights.price,
        airlines=_airline_names(flights.airlines, metadata),
        stops=max(len(legs) - 1, 0),
        total_duration_minutes=total_minutes,
        total_duration=_format_duration(total_minutes),
        departure=legs[0].departure if legs else None,
        arrival=legs[-1].arrival if legs else None,
        legs=legs,
        carbon=_carbon_from(flights),
    )
