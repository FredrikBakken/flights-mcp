---
name: flights-mcp-dev
description: Guidance for developing, testing, and debugging the flights-mcp server, which scrapes Google Flights via fast-flights and serves it over MCP with FastMCP. Use when adding or changing MCP tools, touching the scraping/parsing layer, updating the fast-flights dependency, or diagnosing empty, wrong, or failing flight results.
---

# Working on flights-mcp

An MCP server exposing Google Flights search. Built on [FastMCP](https://gofastmcp.com)
and [fast-flights](https://github.com/AWeirdDev/flights).

## Layout

| Path | Role |
| --- | --- |
| `src/flights_mcp/server.py` | FastMCP instance, tool definitions, input validation |
| `src/flights_mcp/models.py` | Pydantic response models, conversion from `fast_flights` dataclasses |
| `src/flights_mcp/fetching.py` | Custom HTTP fetcher that handles Google's consent redirect |
| `tests/test_server.py` | Tests; no network access |

## Commands

```bash
uv run pytest          # tests
mise run update        # upgrade tools + deps, refresh hooks, run them
prek run --all-files   # lint, format, type-check (ruff, ty, rumdl, uv-lock)
uv run flights-mcp     # run the server over stdio
```

Run `prek run --all-files` before finishing. Hooks are file-scoped, so **newly
created files must be `git add`-ed or the hooks silently skip them** (they
report "no files to check" rather than failing).

## Critical context

These are non-obvious behaviours discovered the hard way. Respect them.

### There is no API — this is a scraper

`fast-flights` parses a JSON blob out of a `<script class="ds:1">` tag in
Google's HTML. Google can change that shape at any time. Consequences:

- Parse failures surface as low-level errors (`IndexError`, `AttributeError`,
  `'NoneType' object has no attribute 'text'`), not clean exceptions. `_run_search`
  wraps everything in a `ToolError` that includes the search URL.
- **Never present results as authoritative.** Every response carries
  `google_flights_url` so a user can verify and book.
- When results look wrong, inspect the raw payload before changing code:
  fetch the HTML, pull the `ds:1` script, `json.loads` it, and check the shape.

### The EU consent redirect

Google redirects EU/EEA requests to `consent.google.com` and never serves
results. The stock `fast-flights` fetcher does not handle this and fails with an
opaque `AttributeError`.

`ConsentingFetcher` in `fetching.py` pre-seeds the `SOCS` consent cookie, which
fixes it. Note that the `CONSENT` cookie does **not** work; only `SOCS` does.
**Always pass this fetcher** via `get_flights(query, integration=...)` — do not
call `get_flights(query, proxy=...)` directly. `FLIGHTS_MCP_PROXY` is the
fallback for environments where the cookie is insufficient.

### Multi-city searches do not work

Google returns an empty payload (`payload[3]` is `None`) for `trip="multi-city"`
through this scraping approach, regardless of the query. A multi-city tool was
implemented, verified non-functional against live data, and deliberately removed.
**Do not re-add one** without first confirming against the raw payload that
Google actually returns results.

### Times are local; never subtract them across legs

`SimpleDatetime` values are local to their airport. Computing total duration as
`last_arrival - first_departure` is wrong whenever the itinerary crosses
timezones — it reported an 11-hour OSL→JFK trip as "4h 50m".

`_total_duration_minutes` instead sums absolute segment durations plus layovers
(a layover sits at one airport, hence one timezone, so that subtraction is safe).
There is a regression test; keep it passing.

## Adding or changing a tool

- Validate inputs with the `_validate_*` helpers and raise `ToolError` with a
  message that tells the model how to fix the call.
- **Do not put `min_length`/`max_length` constraints on tool parameters** when a
  helper already validates them. Pydantic constraints leak into the MCP input
  schema and produce an opaque protocol-level "Invalid request parameters"
  error instead of your actionable message.
- `get_flights` blocks, so call it through `anyio.to_thread.run_sync`.
- Return the Pydantic models from `models.py` rather than raw `fast_flights`
  dataclasses; they resolve airline codes to names and emit ISO datetimes.
- Annotate tools with `readOnlyHint` / `openWorldHint`.

## Testing

Tests must not hit the network. Build `fast_flights.model` dataclasses directly
and pass them to `itinerary_from`, or monkeypatch `flights_mcp.fetching.Client`.

For live verification, use a real search through an in-process client and read
`result.structured_content` (note: `result.data` is a generated `Root` object
without `model_dump`):

```python
from fastmcp import Client
from flights_mcp.server import mcp

async with Client(mcp) as client:
    result = await client.call_tool("search_flights", {...})
    print(result.structured_content)
```

Sanity-check live output rather than assuming success: a plausible-looking
response can still contain wrong durations or prices.

## Upgrading fast-flights

Its parser is tightly coupled to Google's payload. After any bump, run a live
search and confirm prices, airline names, durations, and segment details are
still populated — the unit tests use fixtures and will not catch an upstream
parser break.
