#!/usr/bin/env python3
"""Load test for the chat route. Standard library only.

It plays visitors who each hold a conversation: send a question, wait for the reply, pause ("think"), send
another. It reports how many were answered, how long replies took, and how many were turned away.

THIS SPENDS REAL MODEL TOKENS, because every message goes to the agent. Start small, and keep --max-messages low.

Before a run, raise the per-visitor limits on the Function App for the length of the test (all test traffic comes
from one address, so the route's own limit would stop it), then put them back:
    CHAT_RATE_PER_MINUTE=1000   CHAT_RATE_PER_HOUR=100000   CHAT_GLOBAL_PER_HOUR=1000000

Example (10 visitors, 3 messages each, about 20 s between messages, starting over 30 s):
    python load_test.py --url https://<ontology-app-name>.azurewebsites.net/api/chat --visitors 10 --messages 3 --think 20 --ramp 30

While it runs, watch the instance count (Azure portal > the Function App > Metrics > Instance Count) and the model
deployment's token usage (Foundry > Models + endpoints > your deployment > Metrics).
"""
import argparse
import json
import random
import statistics
import threading
import time
import urllib.error
import urllib.request

QUESTIONS = [
    "What is a Career Circle?", "How do I become a mentor?", "What happens in Week 3?", "How do I volunteer?",
    "What should I put on my resume if I have no experience?", "How do I ask someone for an informational interview?",
    "Which circles are running right now?", "How do I follow up after meeting a mentor?",
]


def percentile(values, p):
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))]


def main():
    ap = argparse.ArgumentParser(description="Load test for the chat route.")
    ap.add_argument("--url", required=True, help="the chat route, ending in /api/chat")
    ap.add_argument("--origin", default="https://www.sujeeth.org", help="the page address the route allows")
    ap.add_argument("--visitors", type=int, default=5)
    ap.add_argument("--messages", type=int, default=2, help="messages each visitor sends")
    ap.add_argument("--think", type=float, default=15.0, help="average pause between a visitor's messages, seconds")
    ap.add_argument("--ramp", type=float, default=10.0, help="spread the visitors' start times over this many seconds")
    ap.add_argument("--timeout", type=float, default=120.0, help="give up on one reply after this many seconds")
    ap.add_argument("--max-messages", type=int, default=40, help="stop sending after this many messages in total (limits token spend)")
    args = ap.parse_args()

    lock = threading.Lock()
    results = []               # (status, seconds)
    state = {"sent": 0, "in_flight": 0, "peak": 0, "stop": False}

    def send(history):
        body = json.dumps({"messages": history}).encode()
        req = urllib.request.Request(args.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Origin": args.origin})
        began = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, time.perf_counter() - began, data.get("reply", "")
        except urllib.error.HTTPError as err:
            try:
                data = json.loads(err.read().decode("utf-8"))
            except Exception:
                data = {}
            return err.code, time.perf_counter() - began, data.get("error", "")
        except Exception as exc:  # timeout, connection reset, DNS
            return type(exc).__name__, time.perf_counter() - began, ""

    def visitor(index):
        time.sleep(random.uniform(0, args.ramp))
        history = []
        for n in range(args.messages):
            with lock:
                if state["stop"] or state["sent"] >= args.max_messages:
                    state["stop"] = True
                    return
                state["sent"] += 1
                state["in_flight"] += 1
                state["peak"] = max(state["peak"], state["in_flight"])
            history.append({"role": "user", "content": random.choice(QUESTIONS)})
            status, seconds, text = send(history)
            with lock:
                state["in_flight"] -= 1
                results.append((status, seconds))
            if status == 200 and text:
                history.append({"role": "assistant", "content": text})
            else:
                history.pop()
                if status == 429:
                    print("  (429: the route's own per-visitor limit stopped this. Raise the CHAT_RATE_* settings for the test.)")
            if n < args.messages - 1:
                time.sleep(random.uniform(0.5, 1.5) * args.think)

    print(f"{args.visitors} visitors x {args.messages} messages (cap {args.max_messages}), think {args.think}s, ramp {args.ramp}s")
    started = time.perf_counter()
    threads = [threading.Thread(target=visitor, args=(i,), daemon=True) for i in range(args.visitors)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - started

    ok = [s for st, s in results if st == 200]
    counts = {}
    for st, _ in results:
        counts[st] = counts.get(st, 0) + 1
    print(f"\nFinished in {wall:.0f}s. Messages sent: {len(results)}. Highest number waiting at once: {state['peak']}.")
    print("Results by status:", ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: str(kv[0]))))
    if ok:
        print(f"Reply time for answered messages: median {statistics.median(ok):.1f}s, 90th percentile {percentile(ok, 90):.1f}s, "
              f"95th {percentile(ok, 95):.1f}s, slowest {max(ok):.1f}s")
        print(f"Answered {len(ok)} of {len(results)} ({100 * len(ok) / len(results):.0f}%), about {len(ok) / (wall / 60):.1f} replies a minute.")
    turned_away = sum(v for k, v in counts.items() if k in (503, 429))
    if turned_away:
        print(f"{turned_away} message(s) were turned away (503 = assistant busy, 429 = limit). "
              "503 after a long wait means every slot stayed full for the whole queue time; compare with the instance count.")
    print("Now read the instance count and the model's token use for this time window, and divide tokens by messages.")


if __name__ == "__main__":
    main()
