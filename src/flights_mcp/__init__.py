"""flights-mcp: an MCP server for searching Google Flights."""

from .server import mcp


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


__all__ = ["main", "mcp"]
