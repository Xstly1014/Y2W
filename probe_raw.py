"""Test SSE response for a mock LLM request."""
import json
import time
import urllib.request


def main() -> None:
    url = "http://127.0.0.1:8000/api/chat/stream"
    payload = {"message": "查询我的订单 1001", "tenant_id": "demo-tenant"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open("probe_mock.log", "w", encoding="utf-8") as f:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                wall = time.time() - t0
                f.write(f"[{wall:6.2f}s] {line}\n")
                f.flush()
    print("DONE. See probe_mock.log")


if __name__ == "__main__":
    main()
