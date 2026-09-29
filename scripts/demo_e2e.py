import sys
import time

import httpx

TEXT = sys.argv[1] if len(sys.argv) > 1 else "7 хоногийн хүнсний жагсаалт гарга. 150 мянган төгрөг, 2 хүн, гахайн мах иддэггүй."

with httpx.Client(base_url="http://localhost:8000", timeout=30) as h:
    pid = h.post("/chat", json={"text": TEXT}).json()["plan_id"]

    def wait(states):
        for _ in range(600):
            p = h.get(f"/plans/{pid}").json()
            if p["state"] in states:
                return p
            time.sleep(1)
        raise SystemExit(f"timeout in state {p['state']}")

    p = wait({"awaiting_approval", "failed"})
    assert p["state"] == "awaiting_approval", p.get("error")
    print("proposed", p["basket"]["total"], "at", list(p["basket"]["stores"]))
    h.post(f"/plans/{pid}/approve", json={"version": p["version"], "confirm_high": True}).raise_for_status()
    p = wait({"completed", "failed"})
    assert p["state"] == "completed", p.get("error")
    print("receipts:", [(r["store"], r["total"]) for r in p["receipts"]])
