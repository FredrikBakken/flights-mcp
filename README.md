# flights-mcp

:airplane: An [MCP](https://modelcontextprotocol.io) server that lets LLMs search
Google Flights, built on [FastMCP](https://gofastmcp.com) and
[fast-flights](https://github.com/AWeirdDev/flights).

## Tools

### `search_flights`

Searches one-way and round-trip flights. Omit `return_date` for a one-way search.

Key parameters:

| Parameter | Description |
| --- | --- |
| `from_airport`, `to_airport` | Three-letter IATA codes, e.g. `OSL`, `JFK` |
| `departure_date`, `return_date` | `YYYY-MM-DD`; `return_date` is optional |
| `adults`, `children`, `infants_in_seat`, `infants_on_lap` | Passengers (max 9 total) |
| `seat` | `economy`, `premium-economy`, `business`, `first` |
| `max_stops` | `0` for non-stop only |
| `airlines` | Airline codes (`["SK", "LH"]`) or alliances (`STAR_ALLIANCE`) |
| `max_price`, `carry_on_bags`, `checked_bags` | Price and bag-fee filters |
| `exclude_basic_economy`, `hide_separate_and_self_transfer` | Fare-quality filters |
| `currency`, `language` | Empty lets Google choose |
| `sort_by`, `max_results` | `price` (default), `duration`, or `departure` |

Each result includes price, airlines, stops, timezone-correct total duration,
per-segment details, CO2 estimates, and a `google_flights_url` reproducing the
search so a user can book.

## Usage

Run the server over stdio:

```bash
uv run flights-mcp
```

Register it with an MCP client, for example in Zed's `settings.json`:

```json
{
  "context_servers": {
    "flights": {
      "command": {
        "path": "uv",
        "args": ["run", "--directory", "/path/to/flights-mcp", "flights-mcp"]
      }
    }
  }
}
```

### Configuration

| Environment variable | Description |
| --- | --- |
| `FLIGHTS_MCP_PROXY` | Optional proxy URL used for outbound requests to Google. |

## Development

```bash
uv sync          # install dependencies
uv run pytest    # run the test suite
prek run --all-files
```

## Notes and caveats

- **This scrapes Google Flights.** There is no official API, so results reflect
  what Google currently serves and may change or be rate-limited. Treat prices
  as indicative and confirm via `google_flights_url`.
- **Consent redirect.** Requests from the EU/EEA get redirected to
  `consent.google.com`. The server sends Google's `SOCS` consent cookie to avoid
  this; if you still hit it, set `FLIGHTS_MCP_PROXY`.
- **Multi-city is not supported.** Google returns an empty result payload for
  multi-city queries through this scraping approach, so no such tool is exposed.
- **Airport codes only.** City names are not resolved; the calling model should
  map them to IATA codes.
