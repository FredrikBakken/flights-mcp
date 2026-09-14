# flights-mcp

[![CI](https://github.com/FredrikBakken/flights-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/FredrikBakken/flights-mcp/actions/workflows/ci.yml)

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

## Install

The name `flights-mcp` on PyPI belongs to an unrelated project, so this server
is **not published to PyPI**. Install it straight from GitHub instead:

```bash
uvx --from git+https://github.com/FredrikBakken/flights-mcp flights-mcp
```

To pin a specific release, append the tag:

```bash
uvx --from git+https://github.com/FredrikBakken/flights-mcp@v0.1.0 flights-mcp
```

Built wheels and sdists are also attached to each
[GitHub release](https://github.com/FredrikBakken/flights-mcp/releases),
alongside a `SHA256SUMS` file, if you would rather install from an artifact:

```bash
uv tool install ./flights_mcp-0.1.0-py3-none-any.whl
```

## Usage

The server speaks MCP over stdio, so it is normally launched by an MCP client
rather than by hand. Register it in Zed's `settings.json`:

```json
{
  "context_servers": {
    "flights": {
      "command": {
        "path": "uvx",
        "args": [
          "--from",
          "git+https://github.com/FredrikBakken/flights-mcp",
          "flights-mcp"
        ]
      }
    }
  }
}
```

Or, when working on a local checkout:

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
uv sync                # install dependencies
uv run pytest          # run the test suite
prek run --all-files   # lint, format, type check
mise run update        # upgrade tools and dependencies, refresh hooks
```

### Releasing

CI runs the hooks, tests, and a build on every push and pull request.

To cut a release, bump the version and push a matching `v*` tag:

```bash
uv version --bump patch   # or minor / major
git commit -am "Release v0.1.1"
git tag v0.1.1
git push origin main --tags
```

The release workflow verifies that the tag matches the project version, runs the
tests, then builds and attaches the wheel, sdist, and `SHA256SUMS` to a GitHub
release.

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
- **Not on PyPI.** The `flights-mcp` name there is an unrelated Duffel-API
  project. Install from git or a GitHub release instead.
