"""Entry point: `python -m mcp_server` (what .mcp.json launches)."""
from __future__ import annotations

import sys

from mcp_server.server import serve


def main() -> int:
    try:
        serve()
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        # The MCP client went away — a normal shutdown, not a failure.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
