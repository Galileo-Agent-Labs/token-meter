"""Runtime-neutral interval, wait, and throughput calculations."""

import math
import statistics

from token_meter.contracts import EvidenceBasis, TimingEvidence


def merge_execution_intervals(intervals):
    """Return wall-active seconds after collapsing overlapping windows."""

    clean = sorted(
        (float(start), float(end))
        for start, end in intervals
        if start is not None and end is not None and float(end) > float(start)
    )
    merged = []
    for start, end in clean:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged)


def timing_snapshot(evidence):
    """Project typed timing without converting unavailable evidence to zero."""

    if not isinstance(evidence, TimingEvidence):
        raise TypeError("evidence must be TimingEvidence")

    def project(value):
        available = value.basis is not EvidenceBasis.UNAVAILABLE
        return {
            "available": available,
            "seconds": float(value.value) if available else None,
            "basis": value.basis.value,
        }

    return {
        "active": project(evidence.active_seconds),
        "wait": project(evidence.wait_seconds),
        "ttft": project(evidence.ttft_seconds),
    }


def wait_time_summary(samples):
    """Summarize completed prompt-to-response wall-clock waits."""

    rows = [row for row in (samples or []) if float(row.get("duration_s") or 0) > 0]
    durations = sorted(float(row.get("duration_s") or 0) for row in rows)
    count = len(durations)
    total = sum(durations)
    p95_index = min(count - 1, max(0, math.ceil(count * 0.95) - 1)) if count else 0
    return {
        "available": bool(count),
        "total_s": total,
        "avg_s": total / count if count else 0,
        "median_s": statistics.median(durations) if count else 0,
        "p95_s": durations[p95_index] if count else 0,
        "max_s": durations[-1] if count else 0,
        "sample_count": count,
        "reported_samples": sum(row.get("timing_basis") == "reported" for row in rows),
        "observed_samples": sum(row.get("timing_basis") == "observed" for row in rows),
        "user_pause_s": sum(float(row.get("user_pause_s") or 0) for row in rows),
    }


def performance_summary(samples, total_output_tokens=0):
    """Summarize weighted output throughput without averaging rates."""

    timed = [
        row for row in (samples or [])
        if row.get("output_tokens", 0) > 0 and row.get("duration_s", 0) > 0
    ]
    tool_free = [row for row in timed if int(row.get("tool_calls") or 0) == 0]
    all_tool_free = bool(timed) and len(tool_free) == len(timed)
    selected = tool_free if all_tool_free else timed
    basis = "tool_free" if all_tool_free else ("end_to_end" if timed else "unavailable")

    def seconds(row):
        if basis == "tool_free":
            return float(row.get("generation_s") or row.get("duration_s") or 0)
        return float(row.get("duration_s") or 0)

    measured_seconds = sum(seconds(row) for row in selected)
    measured_output = sum(int(row.get("output_tokens") or 0) for row in selected)
    latest = max(selected, key=lambda row: row.get("ts") or 0) if selected else None
    latest_seconds = seconds(latest) if latest else 0
    ttft_rows = [
        float(row.get("ttft_s") or 0)
        for row in selected
        if row.get("ttft_s", 0) > 0
    ]
    denominator = int(total_output_tokens or 0)
    return {
        "available": bool(measured_seconds > 0 and measured_output > 0),
        "output_tps": (measured_output / measured_seconds) if measured_seconds > 0 else 0,
        "latest_output_tps": (
            (latest.get("output_tokens") or 0) / latest_seconds
            if latest_seconds > 0 else 0
        ),
        "basis": basis,
        "sample_count": len(selected),
        "timed_samples": len(timed),
        "tool_free_samples": len(tool_free),
        "measured_output_tokens": measured_output,
        "measured_seconds": measured_seconds,
        "timing_coverage": (measured_output / denominator) if denominator > 0 else 0,
        "avg_ttft_ms": (
            sum(ttft_rows) * 1000 / len(ttft_rows) if ttft_rows else 0
        ),
    }
