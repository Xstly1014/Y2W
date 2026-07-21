"""Probe /api/chat/stream under LLM_MOCK=1 to verify mock LLM drives
the full ReAct loop + emits all expected SSE events.
"""
import json
import time
import urllib.request


def main() -> None:
    url = "http://127.0.0.1:8000/api/chat/stream"
    payload = {
        "message": "查询我的订单 1001",
        "tenant_id": "demo-tenant",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    step_count = 0
    with urllib.request.urlopen(req) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            try:
                data = json.loads(line[len("data: "):])
            except json.JSONDecodeError:
                continue
            wall = time.time() - t0
            et = data.get("event_type") or data.get("event") or "?"
            if et == "meta":
                print(f"[{wall:6.2f}s] META  thread={data.get('thread_id')}")
            elif et == "step_start":
                step_count += 1
                print(
                    f"[{wall:6.2f}s] START #{step_count} type={data.get('step_type')} "
                    f"msg={data.get('friendly_message')!r}"
                )
            elif et == "step_end":
                print(
                    f"[{wall:6.2f}s] END   #{step_count} "
                    f"latency_ms={data.get('latency_ms')} preview={(data.get('preview') or '')[:40]!r}"
                )
            elif et == "route":
                print(
                    f"[{wall:6.2f}s] ROUTE → {data.get('subagent_name')} ({data.get('route')})"
                )
            elif et == "interim_answer":
                print(
                    f"[{wall:6.2f}s] INTERIM  >>> {(data.get('answer') or '')[:60]!r}"
                )
            elif et == "action_card":
                print(
                    f"[{wall:6.2f}s] ACTION  id={data.get('id')} label={data.get('label')}"
                )
            elif et == "final":
                print(
                    f"[{wall:6.2f}s] FINAL  answer_len={len(data.get('answer') or '')}"
                )
                print(f"        answer preview: {(data.get('answer') or '')[:200]!r}")
                print(f"        action_cards: {data.get('action_cards')}")
            elif et == "summary":
                print(
                    f"[{wall:6.2f}s] SUMMARY  total_latency_ms={data.get('total_latency_ms')} "
                    f"num_steps={data.get('num_steps')} num_tools={data.get('num_tools_called')}"
                )
            elif et == "error":
                print(f"[{wall:6.2f}s] ERROR  {data.get('message')}")
            else:
                print(f"[{wall:6.2f}s] {et}  {data}")


if __name__ == "__main__":
    main()
