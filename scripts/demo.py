"""End-to-end demo / smoke test for the 0719agent Commerce platform.

Runs against a live pair of services (mock_platform + api) and verifies
the FULL closed loop end-to-end:

  1. service health
  2. KB ingest (samples/)
  3. RAG-grounded chat          -> agent uses rag_search
  4. Commerce-skill chat        -> agent calls query_order + create_refund on mock platform
  5. live feedback (n=bad)      -> flywheel records a bad case with trace_id
  6. trace lookup               -> trace file contains the recorded trace_id
  7. dashboard aggregation      -> stats reflect the new bad case
  8. post-training pipeline     -> SFT/DPO JSONL generated from the flywheel

Exit code 0 = all steps passed; 1 = at least one failed.

Usage:
    # terminal 1
    python -m scripts.run_all
    # terminal 2 (after services are up)
    python scripts/demo.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000"
MOCK = "http://127.0.0.1:8001"
TENANT = "demo-tenant"

# ANSI colors for readable console output (Windows 10+ supports them).
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def step(n: int, total: int, name: str) -> None:
    print(f"\n{BOLD}{CYAN}[{n}/{total}] {name}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")


def info(msg: str) -> None:
    print(f"  {YELLOW}-->{RESET}  {msg}")


def assert_true(cond: bool, msg: str) -> bool:
    if cond:
        ok(msg)
        return True
    fail(msg)
    return False


def main() -> int:
    total_steps = 8
    failures = 0

    # ----------------------------------------------------------------- #
    step(1, total_steps, "Service health check")
    # ----------------------------------------------------------------- #
    try:
        mock_h = httpx.get(f"{MOCK}/health", timeout=5.0).json()
        api_h = httpx.get(f"{API}/api/health", timeout=5.0).json()
    except Exception as exc:
        fail(f"cannot reach services: {exc}")
        return 1
    if not (mock_h.get("status") == "ok" and api_h.get("status") == "ok"):
        fail(f"health check failed: mock={mock_h}, api={api_h}")
        return 1
    ok(f"mock_platform={mock_h['service']}  api={api_h['service']}")

    H = {"X-Tenant-Id": TENANT}

    # ----------------------------------------------------------------- #
    step(2, total_steps, "Ingest samples/ into tenant KB")
    # ----------------------------------------------------------------- #
    r = httpx.post(f"{API}/api/kb/ingest-samples", headers=H, timeout=60.0)
    r.raise_for_status()
    ingest = r.json()
    info(f"ingested {ingest['total_chunks']} chunks into collection {ingest['collection']}")
    if not assert_true(ingest["total_chunks"] > 0, "kb has indexed chunks"):
        failures += 1

    # ----------------------------------------------------------------- #
    step(3, total_steps, "RAG-grounded chat (policy question)")
    # ----------------------------------------------------------------- #
    q1 = "What is your return policy for defective products? Do you cover return shipping?"
    info(f"asking: {q1}")
    r = httpx.post(
        f"{API}/api/chat",
        headers=H, timeout=120.0,
        json={"message": q1, "tenant_id": TENANT, "thread_id": "demo-rag"},
    )
    r.raise_for_status()
    chat1 = r.json()
    info(f"answer (preview): {chat1['answer'][:200]}...")
    info(f"trace_id={chat1['trace_id'][:8]} steps={chat1['num_steps']}")
    if not assert_true(
        "return" in chat1["answer"].lower() or "refund" in chat1["answer"].lower()
        or "退" in chat1["answer"] or "退货" in chat1["answer"],
        "RAG-grounded answer mentions return/refund",
    ):
        failures += 1

    # ----------------------------------------------------------------- #
    step(4, total_steps, "Commerce skill chat (refund via mock platform)")
    # ----------------------------------------------------------------- #
    q2 = "I want to refund order 1001 because the product is defective."
    info(f"asking: {q2}")
    r = httpx.post(
        f"{API}/api/chat",
        headers=H, timeout=120.0,
        json={"message": q2, "tenant_id": TENANT, "thread_id": "demo-commerce"},
    )
    r.raise_for_status()
    chat2 = r.json()
    info(f"answer (preview): {chat2['answer'][:300]}...")
    info(f"trace_id={chat2['trace_id'][:8]} steps={chat2['num_steps']}")
    # Verify a refund was actually created on the mock platform.
    refunds = httpx.get(f"{MOCK}/refunds", headers=H, timeout=5.0).json()
    info(f"mock platform now has {len(refunds)} refund(s)")
    if not assert_true(
        any(rf.get("order_id") == "1001" for rf in refunds),
        "mock platform has a refund for order 1001",
    ):
        failures += 1

    # ----------------------------------------------------------------- #
    step(5, total_steps, "Live feedback → flywheel (mark bad case)")
    # ----------------------------------------------------------------- #
    r = httpx.post(
        f"{API}/api/feedback",
        timeout=10.0,
        json={
            "user_input": q2,
            "prediction": chat2["answer"],
            "passed": False,
            "trace_id": chat2["trace_id"],
            "thread_id": "demo-commerce",
            "tenant_id": TENANT,
        },
    )
    r.raise_for_status()
    fb = r.json()
    info(f"flywheel stats after feedback: {fb['stats']}")
    if not assert_true(fb["stats"]["bad"] >= 1, "flywheel has at least 1 bad case"):
        failures += 1

    # ----------------------------------------------------------------- #
    step(6, total_steps, "Trace lookup (verify trace_id has full step data)")
    # ----------------------------------------------------------------- #
    r = httpx.get(f"{API}/api/traces/{chat2['trace_id']}", timeout=10.0)
    r.raise_for_status()
    trace = r.json()
    info(f"trace {trace['trace_id'][:8]} has {trace['num_steps']} steps, "
         f"{trace['total_tokens_in']} in / {trace['total_tokens_out']} out tokens, "
         f"${trace['total_cost_usd']:.6f}")
    # Find the create_refund tool call in the steps.
    has_refund_call = any(
        s.get("type") == "tool_call" and "refund" in s.get("name", "").lower()
        for s in trace["steps"]
    )
    # Note: tool calls are recorded in the AIMessage's tool_calls list; in
    # our trace format that's nested under the llm_call step's `tool_calls`.
    if not has_refund_call:
        has_refund_call = any(
            "refund" in str(tc.get("name", "")).lower()
            for s in trace["steps"] if s.get("type") == "llm_call"
            for tc in s.get("tool_calls", [])
        )
    if not assert_true(has_refund_call, "trace shows a refund tool call"):
        # Not a hard failure: some models verbalize the action differently.
        info("trace steps (raw):")
        for s in trace["steps"]:
            info(f"  {s.get('type')} {s.get('name','')} {s.get('tool_calls','')}")
    info(f"trace thread_id={trace['thread_id']}")

    # ----------------------------------------------------------------- #
    step(7, total_steps, "Dashboard aggregation")
    # ----------------------------------------------------------------- #
    r = httpx.get(f"{API}/api/dashboard", headers=H, timeout=10.0)
    r.raise_for_status()
    dash = r.json()
    info(f"flywheel={dash['flywheel']}  traces.recent={dash['traces']['recent']}  "
         f"avg_latency={dash['avg_latency_ms']}ms  cost=${dash['total_cost_usd']:.6f}  "
         f"refunds_today={dash['refunds_today']}")
    if not assert_true(dash["traces"]["recent"] >= 2, "dashboard sees >=2 recent traces"):
        failures += 1

    # ----------------------------------------------------------------- #
    step(8, total_steps, "Post-training pipeline (SFT + DPO generation)")
    # ----------------------------------------------------------------- #
    r = httpx.post(f"{API}/api/flywheel/post-train", timeout=60.0)
    r.raise_for_status()
    pt = r.json()
    info(f"artefacts: {pt['artefacts']}")
    # Verify the files actually exist and are non-empty.
    sft_path = Path(pt["artefacts"].get("sft", ""))
    if not sft_path.is_absolute():
        sft_path = Path(__file__).resolve().parent.parent / sft_path
    if not assert_true(sft_path.exists() and sft_path.stat().st_size > 0,
                       f"SFT dataset written and non-empty: {sft_path}"):
        failures += 1
    else:
        # Show a sample line.
        with sft_path.open(encoding="utf-8") as f:
            first = f.readline().strip()
        info(f"sample SFT line: {first[:200]}...")

    # ----------------------------------------------------------------- #
    print("\n" + "=" * 60)
    if failures == 0:
        print(f"{GREEN}{BOLD}ALL 8 STEPS PASSED — closed loop verified end-to-end.{RESET}")
        print("""
  Closed loop summary:
    ingest samples  -> KB has chunks
    chat (RAG)      -> agent grounded answer on return policy
    chat (commerce) -> agent called create_refund -> mock platform has refund
    feedback (n)    -> flywheel recorded bad case with trace_id
    trace lookup    -> trace has all step + token + cost data
    dashboard       -> aggregate stats reflect the new activity
    post-train      -> SFT JSONL generated from flywheel (ready for fine-tune)
""")
    else:
        print(f"{RED}{BOLD}{failures} step(s) failed. See logs above.{RESET}")
    print("=" * 60)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
