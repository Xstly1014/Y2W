"""CLI entry: python -m evaluation [retrieval|answers] [--dataset PATH] [--out PATH]

retrieval: run RetrievalEvalRunner against the configured Indexer (no LLM).
answers:   run EvalRunner against the full agent (LLM, existing behaviour).

Examples:
    python -m evaluation retrieval
    python -m evaluation retrieval --dataset path/to/cases.yaml --out report.json
    python -m evaluation retrieval --collection kb_demo-tenant --k 5
    python -m evaluation answers
    python -m evaluation answers --dataset path/to/eval_cases.yaml
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="evaluation",
        description="Run evaluation suites (retrieval-only or full agent answers).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ret = sub.add_parser(
        "retrieval", help="Run retrieval-only eval (no LLM, just the Indexer)."
    )
    p_ret.add_argument(
        "--dataset", type=str, default=None,
        help="Path to retrieval cases YAML (default: evaluation/fixtures/retrieval_cases.yaml).",
    )
    p_ret.add_argument(
        "--out", type=str, default=None,
        help="Write JSON report to this path (default: data/eval/results/retrieval_eval_<ts>.json).",
    )
    p_ret.add_argument(
        "--collection", type=str, default=None,
        help="Override the collection on every case (default: per-case collection).",
    )
    p_ret.add_argument(
        "--k", type=int, default=None,
        help="Override the top-k cutoff on every case (default: per-case k).",
    )

    p_ans = sub.add_parser(
        "answers", help="Run full agent eval (LLM). Existing EvalRunner behaviour."
    )
    p_ans.add_argument(
        "--dataset", type=str, default=None,
        help="Path to answer eval cases YAML (default: settings.eval_dataset_path).",
    )
    p_ans.add_argument(
        "--out", type=str, default=None,
        help="Write JSON report to this path (default: data/eval/results/eval_<ts>.json).",
    )
    p_ans.add_argument(
        "--tenant", type=str, default=None,
        help="Tenant id whose agent to eval (default: settings.default_tenant_id).",
    )

    args = parser.parse_args()

    if args.cmd == "retrieval":
        return _run_retrieval(args)
    if args.cmd == "answers":
        return _run_answers(args)
    return 1


def _run_retrieval(args: argparse.Namespace) -> int:
    """Run RetrievalEvalRunner against the Indexer — no LLM involved."""
    from api.deps import get_indexer
    from evaluation.retrieval_runner import (
        DEFAULT_RETRIEVAL_DATASET,
        RetrievalEvalRunner,
    )

    indexer = get_indexer()
    runner = RetrievalEvalRunner(indexer)
    dataset = Path(args.dataset) if args.dataset else None
    cases = runner.load_cases(dataset)
    if not cases:
        where = dataset or DEFAULT_RETRIEVAL_DATASET
        print(f"no cases loaded from {where}")
        return 1

    # Apply --collection / --k overrides if given.
    if args.collection or args.k is not None:
        cases = [
            case.model_copy(update={
                **({"collection": args.collection} if args.collection else {}),
                **({"k": args.k} if args.k is not None else {}),
            })
            for case in cases
        ]

    results = runner.run(cases)
    out = Path(args.out) if args.out else None
    if out:
        path = runner.write_report(results, out)
        print(f"report written to {path}")

    summary = runner.summary(results)
    print("\n=== Retrieval Eval Summary ===")
    for metric, score in summary.items():
        print(f"  {metric:15s} = {score:.4f}")
    print(f"\n  cases: {len(results)}")
    return 0


def _run_answers(args: argparse.Namespace) -> int:
    """Run the existing EvalRunner against the full agent (LLM-based).

    Reuses the production agent from `api.deps.get_agent_for_tenant` so the
    eval exercises the same code path real traffic does.
    """
    from pathlib import Path

    from config import settings
    from evaluation.runner import EvalRunner

    # Lazy, heavy imports — only needed for the answers subcommand.
    from api.deps import get_agent_for_tenant
    from observability.tracing import trace_invocation

    tenant_id = args.tenant or settings.default_tenant_id
    agent = get_agent_for_tenant(tenant_id)

    trace_ids: list[str] = []

    def invoke(text: str) -> str:
        answer, recorder = trace_invocation(
            agent, text, thread_id=f"eval-{tenant_id}"
        )
        trace_ids.append(recorder.trace_id)
        return answer

    runner = EvalRunner(agent_invoke=invoke)
    dataset = Path(args.dataset) if args.dataset else None
    cases = runner.load_cases(dataset)
    if not cases:
        where = dataset or settings.eval_dataset_path
        print(f"no cases loaded from {where}")
        return 1

    results = runner.run(cases)

    # Attach trace_ids so badcases can be diagnosed later.
    for r, tid in zip(results, trace_ids):
        r.metadata.setdefault("trace_id", tid)

    out = Path(args.out) if args.out else None
    if out:
        path = runner.write_report(results, out)
        print(f"report written to {path}")

    passed = sum(r.passed for r in results)
    print("\n=== Answer Eval Summary ===")
    if results:
        rate = passed / len(results)
        print(f"  pass rate: {passed}/{len(results)} = {rate:.1%}")
    else:
        print("  no cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
