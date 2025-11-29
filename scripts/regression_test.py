# scripts/regression_test.py
# Simple post-deploy sanity tests for KrishiGPT

from __future__ import annotations
import os
import sys
import time
import json
import httpx

DEFAULT_CASES = [
    "कपास में गुलाबी सुंडी का प्रबंधन बताओ",
    "टमाटर की पत्तियां पीली हो रही हैं, क्या करूं?",
    "PM-KISAN योजना की जानकारी दो",
    "सोयाबीन में कौन सी खाद डालें?"
]

def load_cases():
    # If scripts/cases.json exists, use it; else use defaults
    path = os.getenv("CASES_PATH", "scripts/cases.json")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cases = data.get("cases", [])
            return cases or DEFAULT_CASES
    except Exception:
        pass
    return DEFAULT_CASES

def call_api(api_base: str, message: str) -> tuple[bool, str]:
    url = api_base.rstrip("/") + "/api/chat"
    try:
        r = httpx.post(url, json={"message": message}, timeout=60)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        data = r.json()
        if not data.get("success"):
            return False, f"API success=false. Body: {r.text[:200]}"
        ans = (data.get("response") or "").strip()
        if len(ans) < 20:
            return False, f"Very short answer: {ans!r}"
        return True, ans[:160]
    except Exception as e:
        return False, f"Exception: {e}"

def main():
    api = os.getenv("API_URL", "http://localhost:5000")
    # quick health check
    try:
        hz = httpx.get(api.rstrip("/") + "/health", timeout=20)
        if hz.status_code != 200:
            print("Health check failed:", hz.status_code, hz.text[:160])
            return 1
        h = hz.json()
        if not h.get("ai_ready"):
            print("ai_ready is false in /health")
            return 1
    except Exception as e:
        print("Health check exception:", e)
        return 1

    cases = load_cases()
    print(f"Testing {len(cases)} cases against {api} ...")
    ok = 0
    for i, q in enumerate(cases, 1):
        success, info = call_api(api, q)
        tag = "PASS" if success else "FAIL"
        print(f"{i:02d}. {tag} - {q} -> {info}")
        ok += 1 if success else 0
        time.sleep(0.5)

    print(f"\nSummary: {ok}/{len(cases)} passed")
    return 0 if ok == len(cases) else 1

if __name__ == "__main__":
    sys.exit(main())