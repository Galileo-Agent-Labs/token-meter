#!/usr/bin/env python3
"""Read-only stdio MCP server for Token Meter. Standard library only."""

import json
import os
import sys

import meter


SERVER_NAME = "tokenmeter"
SERVER_TITLE = "Token Meter"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
}
SERVER_INSTRUCTIONS = (
    "Token Meter is a local, read-only source of cost and efficiency evidence for Codex and Claude. "
    "Use check for a decision about the caller's current run, usage for aggregate historical change, "
    "and capabilities for optional skill-pack hygiene. Prefer calling check at meaningful phase "
    "boundaries or when the user asks about cost, context, tool output, or whether to continue. Never imply "
    "continuous monitoring: tools run only when called. Results omit prompts, messages, reasoning text, tool "
    "arguments, tool results, credentials, config values, and named historical runs. The server cannot mutate "
    "Token Meter or agent configuration. Present the verdict first, no more than three evidence points, one "
    "action, the caveat, and the supplied dashboard URL."
)

READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

COMMON_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["ok", "answer", "evidence", "recommended_action", "caveat", "dashboard_url", "as_of", "data_scope"],
    "properties": {
        "ok": {"type": "boolean"},
        "answer": {"type": "string"},
        "evidence": {"type": "array", "maxItems": 3, "items": {"type": "object"}},
        "recommended_action": {"type": "string"},
        "caveat": {"type": "string"},
        "dashboard_url": {"type": "string"},
        "as_of": {"type": "string"},
        "data_scope": {"type": "string"},
        "approximate_fields": {"type": "array", "items": {"type": "string"}},
        "truncated": {"type": "boolean"},
    },
    "additionalProperties": True,
}

TOOLS = [
    {
        "name": "check",
        "title": "Check current run",
        "description": (
            "Answer a decision about the caller's matched current run: whether to continue, cost, context, "
            "tool-result efficiency, next-phase readiness, or one retained execution. Returns a verdict, at "
            "most three evidence points, one action, a caveat, and a dashboard link."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "focus": {"type": "string", "enum": ["continue", "cost", "context", "tools", "next_phase"], "default": "continue"},
                "execution": {"type": "integer", "minimum": 1, "description": "Optional retained execution number to inspect."},
                "session_id": {"type": "string", "minLength": 1, "maxLength": 240, "description": "Optional Token Meter session id when the user explicitly selected a run."},
            },
            "additionalProperties": False,
        },
        "outputSchema": COMMON_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "usage",
        "title": "Review aggregate usage",
        "description": (
            "Review anonymous aggregate spend, model mix, tool-result volume, or day-over-day change. "
            "Historical run titles, project names, session ids, and paths are never returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "enum": ["today", "7d", "14d"], "default": "7d"},
                "focus": {"type": "string", "enum": ["spend", "models", "tools", "changes"], "default": "changes"},
            },
            "additionalProperties": False,
        },
        "outputSchema": COMMON_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "capabilities",
        "title": "Review optional capabilities",
        "description": (
            "Review named user-installed skill packs with bounded usage evidence. "
            "Never returns environment variables, credentials, config values, tool arguments, or tool results, "
            "and cannot change configuration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["current", "all"], "default": "current"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 5},
            },
            "additionalProperties": False,
        },
        "outputSchema": COMMON_OUTPUT_SCHEMA,
        "annotations": READ_ONLY_ANNOTATIONS,
    },
]


def caller_context():
    return {
        "runtime": os.environ.get("TOKEN_METER_CALLER", ""),
        "project": os.environ.get("TOKEN_METER_PROJECT") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(),
    }


def jsonrpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def tool_error(message):
    payload = {"ok": False, "error": str(message)}
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": True,
    }


def call_tool(name, arguments):
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        return tool_error("Tool arguments must be an object.")
    try:
        if name == "check":
            allowed = {"focus", "execution", "session_id"}
            if set(arguments) - allowed:
                raise ValueError("check received an unsupported argument")
            data = meter.agent_check(caller=caller_context(), **arguments)
        elif name == "usage":
            allowed = {"window", "focus"}
            if set(arguments) - allowed:
                raise ValueError("usage received an unsupported argument")
            data = meter.agent_usage(**arguments)
        elif name == "capabilities":
            allowed = {"scope", "limit"}
            if set(arguments) - allowed:
                raise ValueError("capabilities received an unsupported argument")
            data = meter.agent_capabilities(caller=caller_context(), **arguments)
        else:
            return tool_error(f"Unknown tool: {name}")
    except ValueError as exc:
        return tool_error(exc)
    except Exception:
        return tool_error("Token Meter could not build this insight. Check the local dashboard and try again.")
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": False,
    }


def dispatch(request, initialized=False):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return jsonrpc_error(request.get("id") if isinstance(request, dict) else None, -32600, "Invalid Request"), initialized
    request_id = request.get("id")
    method = request["method"]
    params = request.get("params") or {}
    if method.startswith("notifications/"):
        return None, initialized
    if method == "initialize":
        if not isinstance(params, dict):
            return jsonrpc_error(request_id, -32602, "Invalid params"), initialized
        requested_version = str(params.get("protocolVersion") or "")
        protocol_version = requested_version if requested_version in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        return jsonrpc_result(request_id, {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        }), True
    if method == "ping":
        return jsonrpc_result(request_id, {}), initialized
    if not initialized:
        return jsonrpc_error(request_id, -32002, "Server is not initialized"), initialized
    if method == "tools/list":
        return jsonrpc_result(request_id, {"tools": TOOLS}), initialized
    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return jsonrpc_error(request_id, -32602, "Invalid params"), initialized
        return jsonrpc_result(request_id, call_tool(params["name"], params.get("arguments"))), initialized
    return jsonrpc_error(request_id, -32601, "Method not found"), initialized


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    initialized = False
    for raw_line in stdin:
        if len(raw_line) > 1_048_576:
            response = jsonrpc_error(None, -32700, "Parse error")
        else:
            try:
                request = json.loads(raw_line)
            except (TypeError, json.JSONDecodeError):
                response = jsonrpc_error(None, -32700, "Parse error")
            else:
                response, initialized = dispatch(request, initialized=initialized)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            stdout.flush()


if __name__ == "__main__":
    try:
        serve()
    except BrokenPipeError:
        pass
    except Exception as exc:
        print(f"tokenmeter MCP stopped: {exc}", file=sys.stderr)
        raise
