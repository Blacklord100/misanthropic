"""Misanthropic — the Anthropic Messages API, conjured out of your local Claude Code login.

Anthropic charges you per token. Misanthropic charges no one: it phones no server,
trusts no key, and bills nothing. Point any Anthropic SDK or HTTP client at this
server's base URL and it answers exactly like the hosted Messages API — except
every request is fulfilled by shelling out to the `claude` binary you already have
logged in. Your subscription IS the auth.
"""

__version__ = "0.8.5"
