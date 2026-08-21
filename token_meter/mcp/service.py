"""Dependency-injected, read-only session and trace query service."""

import datetime
import os
import time

from token_meter.mcp.contracts import (
    MCPQueryError,
    bounded_page,
    normalize_limit,
    normalize_string_list,
    read_cursor,
)
from token_meter.mcp.projections import (
    ALL_TRACE_SECTIONS,
    native_structure_projection,
    session_projection,
    standardized_trace_projection,
)


SESSION_SCOPES = {"current_project", "all"}
SESSION_STATES = {"current", "completed", "historical"}
TRACE_VIEWS = {"standardized", "native_structure"}
TRACE_EVENT_TYPES = {
    "start", "user", "model", "reasoning", "tool_call", "tool_result",
    "usage", "context", "compaction", "coordination", "complete", "error",
}
TRACE_PAGE_BYTES = 60_000


def _optional_string(value, field, maximum=240):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MCPQueryError(
            "invalid_argument", "{} must be a string".format(field),
        )
    value = value.strip()
    if len(value) > maximum:
        raise MCPQueryError(
            "invalid_argument", "{} is too long".format(field),
        )
    return value


def _required_string(value, field, maximum=240):
    result = _optional_string(value, field, maximum)
    if not result:
        raise MCPQueryError(
            "invalid_argument", "{} is required".format(field),
        )
    return result


def _timestamp(value, field):
    value = _optional_string(value, field, 64)
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        raise MCPQueryError(
            "invalid_argument", "{} must be an ISO-8601 timestamp".format(field),
        )


def _project_matches(project_key, candidate, requested):
    candidate = project_key(candidate)
    requested = project_key(requested)
    if not candidate or not requested:
        return False
    candidate = candidate.rstrip("/\\")
    requested = requested.rstrip("/\\")
    return (
        candidate == requested
        or requested.startswith(candidate + os.sep)
        or requested.startswith(candidate + "/")
        or requested.startswith(candidate + "\\")
    )


def normalize_trace_arguments(session_id, view, sections, execution,
                              event_types, limit):
    session_id = _required_string(session_id, "session_id")
    view = _optional_string(view, "view", 40) or "standardized"
    if view not in TRACE_VIEWS:
        raise MCPQueryError("invalid_argument", "view is unsupported")
    sections = normalize_string_list(
        sections,
        "sections",
        set(ALL_TRACE_SECTIONS),
        len(ALL_TRACE_SECTIONS),
        default=ALL_TRACE_SECTIONS,
    )
    event_types = normalize_string_list(
        event_types,
        "event_types",
        TRACE_EVENT_TYPES,
        len(TRACE_EVENT_TYPES),
    )
    if execution is not None:
        try:
            execution = int(execution)
        except (TypeError, ValueError):
            raise MCPQueryError(
                "invalid_argument", "execution must be a positive integer",
            )
        if isinstance(execution, bool) or execution < 1:
            raise MCPQueryError(
                "invalid_argument", "execution must be a positive integer",
            )
    return {
        "session_id": session_id,
        "view": view,
        "sections": sections,
        "execution": execution,
        "event_types": event_types,
        "limit": normalize_limit(limit, 50, 200),
    }


class MCPQueryService:
    """Query local evidence through injected application-owned callbacks."""

    def __init__(self, *, sources, find_session, summary, state, revision,
                 project_key, runtime_descriptors, now=None):
        self._sources = sources
        self._find_session = find_session
        self._summary = summary
        self._state = state
        self._revision = revision
        self._project_key = project_key
        self._runtime_descriptors = runtime_descriptors
        self._now = now or time.time

    def sessions(self, scope="current_project", runtime=None, client=None,
                 model=None, state=None, start=None, end=None, cursor=None,
                 limit=None, caller=None):
        scope = _optional_string(scope, "scope", 40) or "current_project"
        if scope not in SESSION_SCOPES:
            raise MCPQueryError("invalid_argument", "scope is unsupported")
        filters = {
            "runtime": _optional_string(runtime, "runtime", 120),
            "client": _optional_string(client, "client", 120),
            "model": _optional_string(model, "model", 160),
            "state": _optional_string(state, "state", 40),
            "start": _timestamp(start, "start"),
            "end": _timestamp(end, "end"),
        }
        if filters["state"] and filters["state"] not in SESSION_STATES:
            raise MCPQueryError("invalid_argument", "state is unsupported")
        if (
            filters["start"] is not None
            and filters["end"] is not None
            and filters["start"] > filters["end"]
        ):
            raise MCPQueryError(
                "invalid_argument", "start must not be later than end",
            )
        limit = normalize_limit(limit, 20, 100)
        caller = caller or {}
        caller_project = str(caller.get("project") or caller.get("cwd") or "")
        if scope == "current_project" and not self._project_key(caller_project):
            raise MCPQueryError(
                "invalid_argument", "current project context is unavailable",
            )
        source_rows = list(self._sources() or ())
        projected = []
        matched_sources = []
        for source in source_rows:
            if scope == "current_project" and not _project_matches(
                self._project_key, source.get("project"), caller_project,
            ):
                continue
            if filters["runtime"] and source.get("provider") != filters["runtime"]:
                continue
            if filters["client"] and (
                source.get("client") or source.get("provider")
            ) != filters["client"]:
                continue
            summary = self._summary(source) or {}
            row = session_projection(source, summary, self._now())
            if filters["model"] and row.get("model") != filters["model"]:
                continue
            if filters["state"] and row.get("state") != filters["state"]:
                continue
            activity = row.get("last_activity_at")
            if filters["start"] is not None and (
                activity is None or activity < filters["start"]
            ):
                continue
            if filters["end"] is not None and (
                activity is None or activity > filters["end"]
            ):
                continue
            projected.append(row)
            matched_sources.append(source)
        ordering = sorted(
            range(len(projected)),
            key=lambda index: (
                -(projected[index].get("last_activity_at") or 0),
                projected[index].get("id") or "",
            ),
        )
        projected = [projected[index] for index in ordering]
        matched_sources = [matched_sources[index] for index in ordering]
        query = {
            "scope": scope,
            "filters": filters,
            "limit": limit,
        }
        revision = tuple(
            (row.get("id"), self._revision(source))
            for row, source in zip(projected, matched_sources)
        )
        offset = read_cursor(cursor, query, revision) if cursor else 0
        page = bounded_page(
            projected,
            offset=offset,
            limit=limit,
            query=query,
            revision=revision,
            max_bytes=TRACE_PAGE_BYTES,
        )
        return {
            "ok": True,
            "as_of": self._now(),
            "data_scope": "session_inventory",
            "scope": scope,
            "sessions": page.pop("items"),
            "page": page,
        }

    def trace(self, session_id, view="standardized", sections=None,
              execution=None, event_types=None, cursor=None, limit=None):
        arguments = normalize_trace_arguments(
            session_id, view, sections, execution, event_types, limit,
        )
        return self._trace_result(arguments, cursor)

    def _trace_result(self, arguments, cursor):
        sources = list(self._sources() or ())
        source = self._find_session(arguments["session_id"], sources)
        if source is None:
            raise MCPQueryError(
                "session_not_found", "the requested session was not found",
            )
        state = self._state(source)
        if not state:
            raise MCPQueryError(
                "evidence_unavailable", "detailed session evidence is unavailable",
            )
        query = {
            "session_id": arguments["session_id"],
            "view": arguments["view"],
            "sections": arguments["sections"],
            "execution": arguments["execution"],
            "event_types": arguments["event_types"],
            "limit": arguments["limit"],
        }
        revision = self._revision(source)
        offset = read_cursor(cursor, query, revision) if cursor else 0
        if arguments["view"] == "native_structure":
            records = native_structure_projection(
                source, state, arguments["execution"], arguments["event_types"],
            )
            page = bounded_page(
                records,
                offset=offset,
                limit=arguments["limit"],
                query=query,
                revision=revision,
                max_bytes=TRACE_PAGE_BYTES,
            )
            return {
                "ok": True,
                "schema_version": "1.0",
                "as_of": self._now(),
                "data_scope": "selected_session_native_structure",
                "view": arguments["view"],
                "records": page.pop("items"),
                "page": page,
            }
        projection = standardized_trace_projection(
            source,
            state,
            arguments["sections"],
            arguments["execution"],
            arguments["event_types"],
        )
        flat = []
        for section in ("executions", "events", "tools"):
            for item in projection.get(section) or []:
                flat.append({"section": section, "value": item})
            if section in projection:
                projection[section] = []
        page = bounded_page(
            flat,
            offset=offset,
            limit=arguments["limit"],
            query=query,
            revision=revision,
            max_bytes=TRACE_PAGE_BYTES,
        )
        for item in page.pop("items"):
            projection[item["section"]].append(item["value"])
        projection.update({
            "ok": True,
            "as_of": self._now(),
            "data_scope": "selected_session_standardized_trace",
            "view": arguments["view"],
            "page": page,
        })
        return projection
