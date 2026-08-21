"""Shared privacy assertions for runtime-backed MCP trace projections."""

import json

from token_meter.mcp.projections import (
    native_structure_projection,
    standardized_trace_projection,
)


PROHIBITED_KEYS = {
    "args",
    "arguments",
    "content",
    "credentials",
    "detail",
    "environment",
    "path",
    "payload",
    "project",
    "reasoning_summary",
    "result",
    "results",
    "settings",
    "text",
    "title",
    "user_input",
    "user_inputs",
    "user_message",
}


def _scan_keys(testcase, value):
    if isinstance(value, dict):
        for key, item in value.items():
            testcase.assertNotIn(str(key), PROHIBITED_KEYS)
            _scan_keys(testcase, item)
    elif isinstance(value, list):
        for item in value:
            _scan_keys(testcase, item)


def assert_runtime_trace_privacy(testcase, source, state, *, runtime,
                                 forbidden=(), model=None, tool=None,
                                 native_types=()):
    """Assert both public trace views preserve structure without content."""
    standardized = standardized_trace_projection(source, state)
    native = native_structure_projection(source, state)
    combined = {"standardized": standardized, "native_structure": native}
    encoded = json.dumps(combined, ensure_ascii=False)

    testcase.assertEqual(standardized["session"]["runtime"], runtime)
    testcase.assertTrue(standardized["executions"])
    testcase.assertTrue(standardized["events"])
    testcase.assertTrue(native)
    if model:
        testcase.assertEqual(standardized["session"]["model"], model)
    if tool:
        testcase.assertIn(tool, [row["name"] for row in standardized["tools"]])
    if native_types:
        testcase.assertTrue(
            set(native_types) & {row["native_type"] for row in native},
        )

    _scan_keys(testcase, combined)
    sentinels = list(forbidden)
    sentinels.extend(
        str(source.get(key)) for key in ("path", "project", "title")
        if source.get(key)
    )
    for sentinel in sentinels:
        testcase.assertNotIn(str(sentinel), encoded)
    return combined
