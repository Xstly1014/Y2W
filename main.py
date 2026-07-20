"""Project entry point.

Wires every module into a single runnable application and exposes a CLI:

    python main.py chat         # interactive REPL, with live feedback → flywheel
    python main.py ingest PATH  # index files into the RAG vector store
    python main.py eval         # run eval suite, each case traced + fed to flywheel
    python main.py flywheel     # show badcase / goodcase stats
    python main.py post-train   # build SFT / DPO datasets from the flywheel
    python main.py traces       # show recent agent traces (for diagnosing badcases)

The agent assembly lives in `build_default_agent()` — that's the one place
to edit when you want to swap tools / skills / MCP / RAG in or out.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import settings
from core.agent import build_agent
from core.llm import build_llm
from data_flywheel.collector import BadCaseCollector
from evaluation.runner import EvalRunner
from memory import LongTermMemory, build_memory_tools
from observability.tracing import TraceRecorder, trace_invocation
from post_training.pipeline import PostTrainingPipeline
from rag.embeddings import build_embeddings
from rag.indexer import Indexer
from rag.ingest import ingest_paths
from rag.rag_tool import build_rag_tool
from skills.summarize import SummarizeSkill
from skills.translator import TranslatorSkill
from tools import get_builtin_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
console = Console()


SYSTEM_PROMPT = (
    "You are a helpful, concise assistant with access to tools. "
    "Use tools whenever they help you give an accurate answer. "
    "When you have finished using tools, reply with a short final answer."
)


# --------------------------------------------------------------------------- #
# Agent assembly
# --------------------------------------------------------------------------- #
def build_default_agent():
    """Assemble the full agent: LLM + builtin tools + RAG + skills + MCP + memory.

    The compiled agent is thread-agnostic — callers pass ``thread_id`` at
    invocation time via the langgraph config, so a single built agent can
    serve multiple conversation threads.
    """
    llm = build_llm()

    # RAG / long-term memory
    embeddings = build_embeddings()
    indexer = Indexer(embeddings)
    rag_tool = build_rag_tool(indexer, collection="documents")

    # Skills: summarize (text condensation) + translator (cross-border commerce
    # serves multilingual buyers).
    skills = [SummarizeSkill(llm), TranslatorSkill(llm)]
    skill_tools: list = []
    for s in skills:
        skill_tools.extend(s.get_tools())

    # Long-term memory tools: save_memory / recall_memory, backed by the
    # same Indexer used for RAG (collection = long_term_memory).
    long_term_memory = LongTermMemory(indexer, llm=llm)
    memory_tools = build_memory_tools(long_term_memory)

    # MCP (no-op when MCP_SERVER_URL is empty)
    from mcp_integration.client import MCPClient

    mcp_client = MCPClient()
    # Best-effort connect; ignore failures so the rest still runs.
    try:
        asyncio.run(mcp_client.connect())
    except Exception as exc:  # noqa: BLE001
        logging.warning("MCP connect skipped: %s", exc)
    mcp_tools = mcp_client.as_tools()

    tools = [*get_builtin_tools(), rag_tool, *skill_tools, *memory_tools, *mcp_tools]
    agent = build_agent(llm, tools, system_prompt=SYSTEM_PROMPT)
    return agent


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #
def cmd_chat(args: argparse.Namespace) -> None:
    """Interactive REPL. After each answer, asks for y/n feedback → flywheel."""
    agent = build_default_agent()
    collector = BadCaseCollector()  # for live feedback
    console.print(
        Panel.fit(
            "[bold]0719agent chat[/bold]\n"
            "Type 'exit' to quit, 'reset' to clear memory.\n"
            "After each answer you can rate it: y=good, n=bad, <enter>=skip. "
            "Rated answers feed the data flywheel.",
            border_style="cyan",
        )
    )
    while True:
        try:
            user_input = console.input("[bold green]you>[/bold green] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye.")
            break
        if user_input.strip().lower() in {"exit", "quit", ":q"}:
            break
        if user_input.strip().lower() == "reset":
            args.thread_id = f"thread-{time.time_ns()}"
            agent = build_default_agent()
            console.print("[dim]memory reset.[/dim]")
            continue
        if not user_input.strip():
            continue

        try:
            answer, recorder = trace_invocation(
                agent, user_input, thread_id=args.thread_id
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]agent error:[/red] {exc}")
            continue

        console.print(Panel(answer, title="assistant", border_style="magenta"))
        total_cost = sum(s.get("cost_usd", 0.0) for s in recorder.steps)
        console.print(
            f"[dim]trace={recorder.trace_id[:8]} "
            f"steps={len(recorder.steps)} "
            f"tokens_in={sum(s.get('tokens_in', 0) for s in recorder.steps)} "
            f"tokens_out={sum(s.get('tokens_out', 0) for s in recorder.steps)} "
            f"cost=${total_cost:.6f}[/dim]"
        )

        # Live feedback → flywheel. <enter> = skip (don't record).
        feedback = console.input("[dim]helpful? (y/n/<enter>=skip): [/dim]").strip().lower()
        if feedback == "y":
            collector.record_interaction(
                user_input, answer, passed=True,
                metadata={"trace_id": recorder.trace_id, "source": "chat"},
            )
            console.print("[dim]recorded as good case.[/dim]")
        elif feedback == "n":
            collector.record_interaction(
                user_input, answer, passed=False,
                metadata={"trace_id": recorder.trace_id, "source": "chat"},
            )
            console.print("[dim]recorded as bad case.[/dim]")


def cmd_ingest(args: argparse.Namespace) -> None:
    """Index files or directories into the RAG vector store."""
    embeddings = build_embeddings()
    indexer = Indexer(embeddings)

    results = ingest_paths(args.paths, indexer, collection=args.collection)
    table = Table(title=f"Ingest into collection '{args.collection}'")
    table.add_column("path")
    table.add_column("chunks", justify="right")
    total = 0
    for path, n_chunks in results.items():
        table.add_row(path, str(n_chunks))
        total += n_chunks
    console.print(table)
    console.print(f"[bold]total:[/bold] {total} chunks indexed")


def cmd_eval(args: argparse.Namespace) -> None:
    """Run the eval suite. Each case is traced; trace_id is attached to results."""
    agent = build_default_agent()

    # Side-channel to capture trace_id for each case (EvalRunner only returns
    # the answer string; we enrich results with trace_id afterwards).
    trace_ids: list[str] = []

    def invoke(text: str) -> str:
        answer, recorder = trace_invocation(agent, text, thread_id="eval")
        trace_ids.append(recorder.trace_id)
        return answer

    runner = EvalRunner(agent_invoke=invoke)
    results = runner.run()
    if not results:
        console.print("[yellow]No eval cases found.[/yellow]")
        return

    # Attach trace_id to each result so badcases are diagnosable later.
    for r, tid in zip(results, trace_ids):
        r.metadata["trace_id"] = tid

    collector = BadCaseCollector()
    for r in results:
        # Use the classified path so each case gets auto-tagged with a
        # category + deduped + assigned occurrence_count. See
        # `optimization_logs/2026-07-20/issues-and-fixes.md` P1-5.
        collector.record_case_classified(r, dedup=True)

    report_path = runner.write_report(results)
    passed = sum(r.passed for r in results)
    table = Table(title="Eval summary")
    table.add_column("case_id")
    table.add_column("metric")
    table.add_column("score")
    table.add_column("passed")
    table.add_column("trace_id")
    for r in results:
        # ASCII-only marks: Windows GBK console can't encode U+2713 / U+2717.
        table.add_row(
            r.case_id, r.metric, f"{r.score:.2f}",
            "Y" if r.passed else "N",
            (r.metadata.get("trace_id") or "")[:8],
        )
    console.print(table)
    console.print(
        f"[bold]pass rate[/bold]: {passed}/{len(results)} "
        f"= {passed / len(results):.1%}  |  report: {report_path}"
    )
    console.print(
        f"[dim]traces: data/traces/eval.jsonl "
        f"(grep for trace_id to diagnose any bad case)[/dim]"
    )


def cmd_flywheel(args: argparse.Namespace) -> None:
    collector = BadCaseCollector()
    stats = collector.stats()
    console.print(
        Panel.fit(
            f"bad cases : {stats['bad']}\ngood cases: {stats['good']}",
            title="Data flywheel",
            border_style="cyan",
        )
    )


def cmd_post_train(args: argparse.Namespace) -> None:
    pipeline = PostTrainingPipeline()
    pipeline.build()
    for name, path in pipeline.artefact_paths().items():
        console.print(f"[bold]{name}[/bold]: {path}")


def cmd_traces(args: argparse.Namespace) -> None:
    """Show the most recent traces (across all thread files)."""
    import json

    traces_dir = Path(settings.vector_store_dir).parent / "traces"
    if not traces_dir.exists():
        console.print("[yellow]No traces yet.[/yellow]")
        return

    # Collect last N traces across all thread files.
    all_traces: list[dict] = []
    for f in traces_dir.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                all_traces.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    all_traces.sort(key=lambda t: t.get("total_latency_ms", 0), reverse=True)
    all_traces = all_traces[: args.limit]

    if not all_traces:
        console.print("[yellow]No traces found.[/yellow]")
        return

    table = Table(title=f"Recent traces (top {len(all_traces)} by latency)")
    table.add_column("trace_id")
    table.add_column("thread")
    table.add_column("latency_ms", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("cost$", justify="right")
    table.add_column("steps", justify="right")
    table.add_column("input (preview)")
    for t in all_traces:
        table.add_row(
            (t.get("trace_id") or "")[:8],
            t.get("thread_id", ""),
            str(t.get("total_latency_ms", 0)),
            f"{t.get('total_tokens_in', 0)}/{t.get('total_tokens_out', 0)}",
            f"{t.get('total_cost_usd', 0):.6f}",
            str(t.get("num_steps", 0)),
            (t.get("user_input") or "")[:60],
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="0719agent", description="Minimal LangChain agent.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_chat = sub.add_parser("chat", help="Interactive REPL with the agent.")
    p_chat.add_argument("--thread-id", default="default", help="Conversation thread id.")
    p_chat.set_defaults(func=cmd_chat)

    p_ingest = sub.add_parser("ingest", help="Index files/directories into the RAG vector store.")
    p_ingest.add_argument("paths", nargs="+", help="Files or directories to ingest.")
    p_ingest.add_argument(
        "--collection", default="documents",
        help="Vector store collection to index into (default: documents).",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_eval = sub.add_parser("eval", help="Run the evaluation suite (traced).")
    p_eval.set_defaults(func=cmd_eval)

    sub.add_parser("flywheel", help="Show badcase / goodcase stats.").set_defaults(
        func=cmd_flywheel
    )

    sub.add_parser("post-train", help="Build SFT / DPO datasets.").set_defaults(
        func=cmd_post_train
    )

    p_traces = sub.add_parser("traces", help="Show recent agent traces.")
    p_traces.add_argument("--limit", type=int, default=20, help="Number of traces to show.")
    p_traces.set_defaults(func=cmd_traces)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
