"""Breakthrough — an Anthropic-API-compatible server backed by your local Claude Code CLI.

Point any Anthropic SDK or HTTP client at this server's base URL and it behaves
like the hosted Messages API — except every request is fulfilled by shelling out
to the `claude` binary you already have logged in. No API key, no per-token billing.
"""

__version__ = "0.4.0"
