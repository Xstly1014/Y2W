"""Observability package.

Closes the "I can't see what the agent did" gap. Two pieces:
  * `tracing` — record every LLM call / tool call inside one agent
                 invocation, write to data/traces/<trace_id>.jsonl.
  * `cost`    — extract token usage from LLM responses and estimate
                 dollar cost per model.

Traces are the single most valuable artefact for diagnosing badcases:
when the flywheel captures a failed case, you want to know *which* tool
call went wrong, not just the final wrong answer.

Future expansion hooks:
  * LangSmith / LangFuse integration (set LANGSMITH_TRACING=true)
  * OpenTelemetry export
  * live trace viewer (web UI)
  * aggregated metrics (P50 latency, tool success rate, ...)
"""
from observability.tracing import TraceRecorder, trace_invocation
from observability.cost import extract_usage, estimate_cost

__all__ = ["TraceRecorder", "trace_invocation", "extract_usage", "estimate_cost"]
