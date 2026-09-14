"""Tests for the fault-tolerant payload parser.

The fixtures mirror the real Google payload shape closely enough to exercise
the failure that motivated this module: an itinerary whose price block is an
empty list.
"""

import json
from typing import Any

import pytest
from fast_flights.exceptions import FlightsNotFound

from flights_mcp.parsing import parse, parse_js

METADATA: list[Any] = [
    None,
    [[], [["SK", "Scandinavian Airlines"], ["WF", "Wideroe"]]],
]


def segment(*, code_from="TRD", code_to="OSL", dep=(10, 30), arr=(11, 20), duration=50):
    """Build a segment with the positional layout Google uses."""
    raw: list[Any] = [None] * 23
    raw[3], raw[4] = code_from, f"{code_from} Airport"
    raw[5], raw[6] = f"{code_to} Airport", code_to
    raw[8], raw[10] = list(dep), list(arr)
    raw[11] = duration
    raw[17] = "Boeing 737"
    raw[20] = [2026, 10, 1]
    raw[21] = [2026, 10, 1]
    return raw


def itinerary(price_block, *, airlines=("SK",), segments=None):
    flight: list[Any] = [None] * 23
    flight[0] = "Nonstop"
    flight[1] = list(airlines)
    flight[2] = segments if segments is not None else [segment()]
    extras: list[Any] = [None] * 9
    extras[7], extras[8] = 45_000, 57_000
    flight[22] = extras
    return [flight, price_block]


def payload_js(entries):
    payload: list[Any] = [None] * 8
    payload[3] = [entries]
    payload[7] = METADATA
    return f"data:{json.dumps(payload)},sideChannel"


PRICED: list[Any] = [[None, 699], "token"]
UNPRICED: list[Any] = [[], "token"]
"""The shape that used to raise IndexError: an empty price block."""


class TestResilientParsing:
    def test_parses_a_normal_itinerary(self):
        results = parse_js(payload_js([itinerary(PRICED)]))
        assert len(results) == 1
        assert results[0].price == 699
        assert results[0].flights[0].from_airport.code == "TRD"

    def test_unpriced_itinerary_does_not_discard_the_others(self):
        """The regression: one empty price block used to sink the whole search."""
        results = parse_js(
            payload_js([itinerary(PRICED), itinerary(UNPRICED), itinerary(PRICED)])
        )
        assert len(results) == 3
        assert [f.price for f in results] == [699, None, 699]

    def test_unpriced_itinerary_keeps_its_flight_details(self):
        results = parse_js(payload_js([itinerary(UNPRICED)]))
        assert results[0].price is None
        assert results[0].flights[0].to_airport.code == "OSL"

    def test_malformed_itinerary_is_skipped_not_fatal(self):
        results = parse_js(payload_js([itinerary(PRICED), ["nonsense"], "junk"]))
        assert len(results) == 1
        assert results[0].price == 699

    def test_resolves_metadata(self):
        results = parse_js(payload_js([itinerary(PRICED)]))
        assert {a.code: a.name for a in results.metadata.airlines}["WF"] == "Wideroe"

    def test_empty_result_set(self):
        payload: list[Any] = [None] * 8
        payload[3] = [None]
        payload[7] = METADATA
        assert len(parse_js(f"data:{json.dumps(payload)},sideChannel")) == 0

    def test_error_status_raises_flights_not_found(self):
        with pytest.raises(FlightsNotFound):
            parse_js("data:errorHasStatus: true,")

    def test_missing_payload_script_is_actionable(self):
        with pytest.raises(RuntimeError, match="without a results payload"):
            parse("<html><body>blocked</body></html>")

    def test_multi_segment_itinerary(self):
        results = parse_js(
            payload_js(
                [
                    itinerary(
                        UNPRICED,
                        airlines=("WF",),
                        segments=[
                            segment(code_from="TRD", code_to="BNN", duration=50),
                            segment(code_from="BNN", code_to="OSL", duration=105),
                        ],
                    )
                ]
            )
        )
        assert len(results[0].flights) == 2
        assert results[0].price is None
