"""Capture requests are sparse and must not replay generation after an ambiguous disconnect."""
from __future__ import annotations

import time

import httpx


class CaptureRequestError(httpx.HTTPError):
    """A phase-specific failure without request bodies, tokens, or server error pages."""


def request(method: str, url: str, *, phase: str, timeout=httpx.Timeout(200, connect=5), **kwargs) -> httpx.Response:
    """Use a fresh connection per request; retry only a read-only download's transport failure.

    A prepare response can sit idle while the scene model runs. Opening a new connection for
    finish avoids reusing that socket across the server's keepalive deadline, including when
    an SSH tunnel delays the peer's close notification. This is preventative: a disconnect
    alone does not identify which peer closed the old connection.
    """
    attempts = 2 if method.upper() == "GET" else 1
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            # Explicitly disable transport retries: a lost POST response is not permission
            # to submit the same paid generation again. Do not share the UI's pooled client.
            with httpx.Client(timeout=timeout,
                              transport=httpx.HTTPTransport(retries=0)) as client:
                response = client.request(method, url, **kwargs)
                response.raise_for_status()
            print(f"[capture http] phase={phase} status={response.status_code} "
                  f"elapsed_ms={(time.monotonic() - started) * 1000:.0f}")
            return response
        except httpx.HTTPError as exc:
            status = f" status={exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else ""
            retry = isinstance(exc, httpx.TransportError) and attempt + 1 < attempts
            print(f"[capture http] phase={phase} error={type(exc).__name__}{status} "
                  f"elapsed_ms={(time.monotonic() - started) * 1000:.0f} retry={retry}")
            if not retry:
                raise CaptureRequestError(f"{phase}: {type(exc).__name__}{status}") from exc
    raise AssertionError("unreachable")
