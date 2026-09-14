"""HTTP fetching for Google Flights.

``fast_flights``' default fetcher does not carry a consent cookie, so requests
originating in the EU/EEA get redirected to ``consent.google.com`` and the
result page never loads. We supply our own fetcher that pre-seeds Google's
``SOCS`` consent cookie, which is what a browser sends once the user has
dismissed the consent dialog.
"""

from __future__ import annotations

from fast_flights.integrations.base import FetchIntegration
from fast_flights.querying import Query
from primp import Client

URL = "https://www.google.com/travel/flights"

CONSENT_COOKIE = {"SOCS": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_L2aBg"}
"""Google's consent cookie, as set after accepting the EU consent dialog."""


class ConsentingFetcher(FetchIntegration):
    """Fetch the flights page with Google's consent cookie already set."""

    def __init__(self, *, proxy: str | None = None, timeout: float = 30.0) -> None:
        self.proxy = proxy
        self.timeout = timeout

    def fetch_html(self, q: Query | str, /) -> str:
        client = Client(
            impersonate="chrome_145",
            impersonate_os="macos",
            referer=True,
            proxy=self.proxy,
            timeout=self.timeout,
            cookie_store=True,
        )
        client.set_cookies("https://www.google.com", CONSENT_COOKIE)

        params = q.params() if isinstance(q, Query) else {"q": q}
        response = client.get(URL, params=params)

        if "consent.google.com" in response.url:
            raise RuntimeError(
                "Google redirected to its consent page instead of returning "
                "results. Set FLIGHTS_MCP_PROXY to a proxy outside the EU/EEA, "
                "or retry shortly."
            )

        return response.text
