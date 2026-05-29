"""Unit tests for dashboard service helpers."""

from __future__ import annotations

from engine.dashboard.service import DashboardHandler, _parse_allowed_origins


class _Headers(dict):
    def get(self, key: str, default: str = "") -> str:
        return super().get(key, default)


def _handler_for_origin(origin: str):
    handler = DashboardHandler.__new__(DashboardHandler)
    handler.headers = _Headers({"Origin": origin})
    handler.sent_headers = []

    def send_header(key: str, value: str) -> None:
        handler.sent_headers.append((key, value))

    handler.send_header = send_header
    return handler


def test_parse_allowed_origins_keeps_only_safe_origins() -> None:
    parsed = _parse_allowed_origins(
        "https://dashboard.example.com, http://localhost:5173, "
        "https://bad.example.com/path, https://evil.example.com\r\nX-Test: yes, ftp://example.com"
    )

    assert parsed == {
        "https://dashboard.example.com": "https://dashboard.example.com",
        "http://localhost:5173": "http://localhost:5173",
    }


def test_cors_header_uses_stored_allowlisted_value() -> None:
    DashboardHandler._auth = {
        "allowed_origins": {
            "https://dashboard.example.com": "https://dashboard.example.com",
        }
    }
    handler = _handler_for_origin("https://dashboard.example.com")

    handler._send_cors_headers()

    assert ("Access-Control-Allow-Origin", "https://dashboard.example.com") in handler.sent_headers
    assert ("Vary", "Origin") in handler.sent_headers


def test_cors_header_ignores_unmatched_origin_with_newline() -> None:
    DashboardHandler._auth = {
        "allowed_origins": {
            "https://dashboard.example.com": "https://dashboard.example.com",
        }
    }
    handler = _handler_for_origin("https://dashboard.example.com\r\nX-Test: yes")

    handler._send_cors_headers()

    assert handler.sent_headers == []
