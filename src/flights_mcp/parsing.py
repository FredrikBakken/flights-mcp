"""A fault-tolerant reimplementation of ``fast_flights``' payload parser.

Google's payload is not uniform: some itineraries carry an empty price block
(``k[1][0] == []``) instead of the usual ``[None, <price>]``. Upstream's
``parse_js`` indexes straight into that with ``k[1][0][1]``, so a *single*
malformed entry raises ``IndexError`` and discards the entire result set —
including the dozens of perfectly good itineraries either side of it.

This surfaced as an apparently random "some dates work, some don't" failure.
It is fully deterministic: the affected dates simply happen to contain an
itinerary Google declined to price (observed on Widerøe TRD-BNN-OSL, where
the fare is only sold as part of a connecting ticket).

We parse the same payload defensively: a bad field degrades that one value to
``None``, and a bad itinerary is skipped rather than taking the search with it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fast_flights.exceptions import FlightsNotFound
from fast_flights.model import (
    Airline,
    Airport,
    Alliance,
    CarbonEmission,
    Flights,
    JsMetadata,
    SimpleDatetime,
    SingleFlight,
)
from fast_flights.parser import ResultList, _parse_time
from selectolax.lexbor import LexborHTMLParser

log = logging.getLogger(__name__)


def _dig(data: Any, *path: int) -> Any:
    """Follow a positional path into the payload, returning None if it breaks."""
    for key in path:
        try:
            data = data[key]
        except IndexError, KeyError, TypeError:
            return None
    return data


def _metadata(payload: Any) -> JsMetadata:
    alliances = [
        Alliance(code=code, name=name) for code, name in (_dig(payload, 7, 1, 0) or [])
    ]
    airlines = [
        Airline(code=code, name=name) for code, name in (_dig(payload, 7, 1, 1) or [])
    ]
    return JsMetadata(alliances=alliances, airlines=airlines)


def _single_flight(raw: Any) -> SingleFlight:
    code_from, code_to = _dig(raw, 3), _dig(raw, 6)
    if not isinstance(code_from, str) or not isinstance(code_to, str):
        # Without both endpoints the segment is unusable, and it means we are
        # reading something that is not a segment at all.
        raise TypeError("flight segment is missing its airport codes")

    departure = SimpleDatetime(
        date=tuple(_dig(raw, 20) or ()), time=_parse_time(_dig(raw, 8))
    )
    arrival = SimpleDatetime(
        date=tuple(_dig(raw, 21) or ()), time=_parse_time(_dig(raw, 10))
    )
    return SingleFlight(
        from_airport=Airport(code=code_from, name=_dig(raw, 4)),
        to_airport=Airport(code=code_to, name=_dig(raw, 5)),
        departure=departure,
        arrival=arrival,
        duration=_dig(raw, 11),
        plane_type=_dig(raw, 17),
    )


def _itinerary(entry: Any) -> Flights:
    if not isinstance(entry, list):
        raise TypeError(f"itinerary entry is {type(entry).__name__}, not a list")

    flight = entry[0]
    segments = [_single_flight(raw) for raw in (_dig(flight, 2) or [])]
    if not segments:
        # An itinerary with no segments carries no usable information, and is a
        # reliable signal that we misread the entry rather than that Google
        # returned an empty one.
        raise ValueError("itinerary has no flight segments")

    return Flights(
        type=_dig(flight, 0),
        # None when Google lists an itinerary without a bookable fare.
        price=_dig(entry, 1, 0, 1),
        airlines=_dig(flight, 1) or [],
        flights=segments,
        carbon=CarbonEmission(
            emission=_dig(flight, 22, 7),
            typical_on_route=_dig(flight, 22, 8),
        ),
    )


def parse_js(js: str) -> ResultList:
    """Parse the ``ds:1`` script payload, skipping unparseable itineraries."""
    data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        raise FlightsNotFound("no flights found; received error")

    payload = json.loads(data)

    results = ResultList()
    results.metadata = _metadata(payload)

    for entry in _dig(payload, 3, 0) or []:
        try:
            results.append(_itinerary(entry))
        except Exception:
            # One malformed itinerary must not sink the whole search.
            log.debug("skipping unparseable itinerary", exc_info=True)

    return results


def parse(html: str) -> ResultList:
    """Parse a Google Flights results page."""
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise RuntimeError(
            "Google Flights returned a page without a results payload. This "
            "usually means the request was blocked or redirected rather than "
            "served; retrying shortly often works."
        )
    return parse_js(script.text())
