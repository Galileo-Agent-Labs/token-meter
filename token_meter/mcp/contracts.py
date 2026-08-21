"""Validation, cursors, and response bounds shared by MCP query tools."""

import base64
import hashlib
import json


MAX_CURSOR_CHARS = 2048
MAX_RESPONSE_BYTES = 65_536


class MCPQueryError(ValueError):
    """A stable, content-free query error safe for MCP transport."""

    def __init__(self, code, message):
        super().__init__(str(message))
        self.code = str(code)


def _canonical_bytes(value):
    try:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError):
        raise MCPQueryError("invalid_argument", "query values must be JSON compatible")
    return text.encode("utf-8")


def _fingerprint(value):
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def normalize_limit(value, default, maximum):
    try:
        result = int(default if value is None else value)
    except (TypeError, ValueError):
        raise MCPQueryError("invalid_argument", "limit must be an integer")
    if isinstance(value, bool) or result < 1 or result > int(maximum):
        raise MCPQueryError(
            "invalid_argument", "limit must be between 1 and {}".format(maximum),
        )
    return result


def normalize_string_list(value, field, allowed, maximum, default=()):
    if value is None:
        values = list(default)
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise MCPQueryError(
            "invalid_argument", "{} must be an array".format(field),
        )
    if len(values) > int(maximum):
        raise MCPQueryError(
            "invalid_argument",
            "{} accepts at most {} values".format(field, maximum),
        )
    normalized = []
    for item in values:
        item = str(item or "").strip()
        if item not in allowed:
            raise MCPQueryError(
                "invalid_argument",
                "{} contains an unsupported value".format(field),
            )
        if item in normalized:
            raise MCPQueryError(
                "invalid_argument", "{} contains a duplicate value".format(field),
            )
        normalized.append(item)
    return tuple(normalized)


def make_cursor(position, query, revision):
    try:
        position = int(position)
    except (TypeError, ValueError):
        raise MCPQueryError("invalid_argument", "cursor position must be an integer")
    if position < 0:
        raise MCPQueryError("invalid_argument", "cursor position must not be negative")
    payload = {
        "p": position,
        "q": _fingerprint(query),
        "r": _fingerprint(revision),
    }
    return base64.urlsafe_b64encode(_canonical_bytes(payload)).decode("ascii").rstrip("=")


def read_cursor(cursor, query, revision):
    value = str(cursor or "")
    if not value or len(value) > MAX_CURSOR_CHARS:
        raise MCPQueryError("invalid_argument", "cursor is invalid")
    try:
        padding = "=" * ((4 - len(value) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
        position = payload["p"]
        query_hash = payload["q"]
        revision_hash = payload["r"]
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise MCPQueryError("invalid_argument", "cursor is invalid")
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        raise MCPQueryError("invalid_argument", "cursor is invalid")
    if query_hash != _fingerprint(query):
        raise MCPQueryError("invalid_argument", "cursor does not match this query")
    if revision_hash != _fingerprint(revision):
        raise MCPQueryError("stale_cursor", "source evidence changed; restart the query")
    return position


def bounded_page(items, *, offset, limit, query, revision,
                 max_bytes=MAX_RESPONSE_BYTES):
    rows = list(items or ())
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        raise MCPQueryError("invalid_argument", "page offset must be an integer")
    if offset < 0 or offset > len(rows):
        raise MCPQueryError("invalid_argument", "page offset is outside the result")
    limit = normalize_limit(limit, limit, max(1, int(limit)))
    stop = min(len(rows), offset + limit)
    selected = []
    for index in range(offset, stop):
        candidate = selected + [rows[index]]
        more = index + 1 < len(rows)
        result = {
            "items": candidate,
            "next_cursor": make_cursor(index + 1, query, revision) if more else None,
            "truncated": more,
            "offset": offset,
            "returned": len(candidate),
            "total": len(rows),
        }
        if len(_canonical_bytes(result)) > int(max_bytes):
            break
        selected = candidate
    if not selected and offset < stop:
        raise MCPQueryError(
            "response_too_large", "one result row exceeds the response bound",
        )
    next_position = offset + len(selected)
    more = next_position < len(rows)
    return {
        "items": selected,
        "next_cursor": make_cursor(next_position, query, revision) if more else None,
        "truncated": more,
        "offset": offset,
        "returned": len(selected),
        "total": len(rows),
    }
