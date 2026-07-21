"""Probe chat stream for order 1003 (pending status)."""
import json
import sys
import time
import urllib.request


def main() -> None:
    url = "http://127.0.0.1:8000/api/chat/stream"
    payload = {"message": "帮我查一下订单 1001 的物流轨迹", "tenant_id": "demo-tenant"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    print("connecting...", flush=True)
    with urllib.request.urlopen(req, timeout=60) as resp:
        print("connected", flush=True)
        event_name = None
        data_buf = []
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                # Dispatch
                if event_name and data_buf:
                    try:
                        data = json.loads("\n".join(data_buf))
                    except json.JSONDecodeError:
                        pass
                    else:
                        wall = time.time() - t0
                        if event_name == "final":
                            print(f"[{wall:6.2f}s] FINAL  answer:", flush=True)
                            print(data.get("answer"), flush=True)
                            print(f"action_cards: {data.get('action_cards')}", flush=True)
                        elif event_name == "summary":
                            print(f"[{wall:6.2f}s] SUMMARY  num_tools={data.get('num_tools_called')}", flush=True)
                        elif event_name == "step_end":
                            if data.get("step_type") == "llm_call":
                                print(f"[{wall:6.2f}s] llm_call preview: {(data.get('preview','') or '')[:200]}", flush=True)
                event_name = None
                data_buf = []
                continue
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_buf.append(line[len("data: "):])
    print("done", flush=True)


if __name__ == "__main__":
    main()
