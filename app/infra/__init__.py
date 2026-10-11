"""Infrastructure adapters: Postgres, Redis/ARQ, Telegram HTTP client, clock.

Implements domain ports. Receives configuration as plain constructor arguments (wired in app/di.py) rather than
reading settings itself.
"""
