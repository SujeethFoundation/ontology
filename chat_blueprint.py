"""Chat route for the sujeeth.org chat window, hosted in the ontology app.

The page cannot call the Foundry agent itself: the agent endpoint accepts only Microsoft Entra tokens, and
tokens must never be put in a web page. This route takes the conversation from the page, calls the agent
as the app's managed identity, and returns the reply.

  POST /api/chat          {"messages": [{"role": "user" | "assistant", "content": "..."}]}  ->  {"reply": "..."}
  GET  /api/chat/health   ->  {"configured": true}

Every other route in this app needs a function key. These two are open to the web on purpose (the chat page
has no key to send), so they check the page's address, limit requests, and cap how many chats run at once.

It is loaded only when CHAT_ENABLED=true. Settings (application settings of the Function App):
  FOUNDRY_AGENT_BASE_URL   The agent's OpenAI-compatible base URL (required).
  FOUNDRY_API_VERSION      Optional api-version query value, only if your endpoint needs one.
  CHAT_ALLOWED_ORIGINS     Page origins allowed to use the route (default https://www.sujeeth.org,https://sujeeth.org).
  CHAT_RATE_PER_MINUTE (8), CHAT_RATE_PER_HOUR (60), CHAT_GLOBAL_PER_HOUR (600)   Limits per instance.
  CHAT_MAX_CONCURRENT (8)  Chats being answered at one time on an instance.
  CHAT_QUEUE_SECONDS (25)  How long a chat waits for a free slot before it is told the assistant is busy.
  CHAT_MODEL_RETRIES (3)   Times to retry when the model says it is over its token limit (honors its Retry-After).
  CHAT_MAX_MESSAGE_CHARS (2000), CHAT_MAX_HISTORY_MESSAGES (12), CHAT_TIMEOUT_SECONDS (90)
  CHAT_LOG_ERROR_DETAIL (false)  Set to true while diagnosing: failures also log the error's text (first 300 characters).
                                 Off by default, because an error's text can echo part of what a visitor wrote.

How it scales: Flex Consumption adds instances when an instance's requests in flight reach its per-instance HTTP
concurrency (a Python app's default is 1; see the README for the setting). A chat that is waiting for a slot is
still a request in flight, so a full instance makes the platform start another one instead of turning visitors
away. Set CHAT_MAX_CONCURRENT at or above that per-instance setting, so the platform's limit is the one that applies.

Why chats stay off the worker's threads: this app also answers every search's domain lookups (through websearch).
A chat waits up to a minute and a half for the agent, so chats are async and run on a small pool of their own.
"""
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

import azure.functions as func

bp = func.Blueprint()
log = logging.getLogger("sujeeth-chat")

FRIENDLY = {
    "busy": "The assistant is busy right now. Please try again in a minute.",
    "unavailable": "The assistant is unavailable right now. Please try again in a minute, or browse sujeeth.org.",
    "slow": "That took too long. Please try again, or ask a shorter question.",
}


def _int(name, default):
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


BASE_URL = (os.getenv("FOUNDRY_AGENT_BASE_URL") or "").strip().rstrip("/")
API_VERSION = (os.getenv("FOUNDRY_API_VERSION") or "").strip()
ALLOWED_ORIGINS = {
    o.strip().rstrip("/").lower()
    for o in (os.getenv("CHAT_ALLOWED_ORIGINS") or "https://www.sujeeth.org,https://sujeeth.org").split(",")
    if o.strip()
}
RATE_PER_MINUTE = _int("CHAT_RATE_PER_MINUTE", 8)
RATE_PER_HOUR = _int("CHAT_RATE_PER_HOUR", 60)
GLOBAL_PER_HOUR = _int("CHAT_GLOBAL_PER_HOUR", 600)
MAX_CONCURRENT = max(1, _int("CHAT_MAX_CONCURRENT", 8))
QUEUE_SECONDS = _int("CHAT_QUEUE_SECONDS", 25)
MODEL_RETRIES = max(0, _int("CHAT_MODEL_RETRIES", 3))
LOG_ERROR_DETAIL = (os.getenv("CHAT_LOG_ERROR_DETAIL") or "").strip().lower() in ("1", "true", "yes")
MAX_MESSAGE_CHARS = _int("CHAT_MAX_MESSAGE_CHARS", 2000)
MAX_HISTORY_MESSAGES = _int("CHAT_MAX_HISTORY_MESSAGES", 12)
TIMEOUT_SECONDS = _int("CHAT_TIMEOUT_SECONDS", 90)
MAX_BODY_BYTES = 64 * 1024

_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="chat")
_slots = None      # asyncio semaphore, made on first use inside the running event loop
_slots_loop = None
_in_flight = 0     # chats being answered right now; only changed on the event loop thread


def _get_slots():
    """The worker runs one long-lived event loop. The semaphore belongs to it, so make a new one if that ever changes."""
    global _slots, _slots_loop
    loop = asyncio.get_running_loop()
    if _slots is None or _slots_loop is not loop:
        _slots, _slots_loop = asyncio.Semaphore(MAX_CONCURRENT), loop
    return _slots


class Limiter:
    """Sliding-window limits kept in memory. Each running instance counts for itself."""

    def __init__(self):
        self.lock = threading.Lock()
        self.by_ip = defaultdict(deque)
        self.everyone = deque()

    def allow(self, ip, now=None):
        """Returns (allowed, seconds_to_wait)."""
        now = time.monotonic() if now is None else now
        with self.lock:
            hits = self.by_ip[ip]
            while hits and now - hits[0] > 3600:
                hits.popleft()
            while self.everyone and now - self.everyone[0] > 3600:
                self.everyone.popleft()
            last_minute = [t for t in hits if now - t <= 60]
            if RATE_PER_MINUTE and len(last_minute) >= RATE_PER_MINUTE:
                return False, max(1, int(60 - (now - last_minute[0])) + 1)
            if RATE_PER_HOUR and len(hits) >= RATE_PER_HOUR:
                return False, max(1, int(3600 - (now - hits[0])) + 1)
            if GLOBAL_PER_HOUR and len(self.everyone) >= GLOBAL_PER_HOUR:
                return False, 60
            hits.append(now)
            self.everyone.append(now)
            if len(self.by_ip) > 5000:  # forget addresses that have gone quiet
                for key in [k for k, v in self.by_ip.items() if not v][:1000]:
                    self.by_ip.pop(key, None)
            return True, 0


limiter = Limiter()


def client_ip(req):
    """The visitor's address. The platform appends the address it saw to X-Forwarded-For, so the LAST entry
    is the one to trust; earlier entries can be written by the visitor."""
    parts = [p.strip() for p in (req.headers.get("x-forwarded-for", "") or "").split(",") if p.strip()]
    last = parts[-1] if parts else ""
    if last.count(":") == 1:  # "1.2.3.4:5678"
        last = last.split(":")[0]
    return last or req.headers.get("x-client-ip", "") or "unknown"


def cors_headers(origin):
    if origin and origin.rstrip("/").lower() in ALLOWED_ORIGINS:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {"Vary": "Origin"}


def reply_json(status, body, origin=None, extra=None):
    headers = {"Cache-Control": "no-store", **cors_headers(origin), **(extra or {})}
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/json", headers=headers)


def parse_messages(raw):
    """Check the conversation the page sent. Returns (messages, error)."""
    if not isinstance(raw, list) or not raw:
        return None, "Send a list of messages."
    messages = []
    for item in raw[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            return None, "Each message needs a role and content."
        role, content = item.get("role"), item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            return None, "Each message needs a role (user or assistant) and text."
        content = content.strip()
        if not content:
            continue
        if len(content) > MAX_MESSAGE_CHARS:
            return None, f"Messages can be up to {MAX_MESSAGE_CHARS} characters."
        messages.append({"role": role, "content": content})
    if not messages or messages[-1]["role"] != "user":
        return None, "The last message must be from the visitor."
    while messages and messages[0]["role"] != "user":  # the agent should see the conversation start with the visitor
        messages.pop(0)
    return messages, None


_client = None
_client_lock = threading.Lock()


def get_client():
    """One client for the life of the instance. Its token provider refreshes tokens itself.
    The packages are imported here, not at the top, so the ontology routes pay nothing for them."""
    global _client
    with _client_lock:
        if _client is None:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            from openai import OpenAI

            kwargs = {
                "base_url": BASE_URL,
                "api_key": get_bearer_token_provider(DefaultAzureCredential(), "https://ai.azure.com/.default"),
                "timeout": TIMEOUT_SECONDS,
                "max_retries": MODEL_RETRIES,
            }
            if API_VERSION:
                kwargs["default_query"] = {"api-version": API_VERSION}
            _client = OpenAI(**kwargs)
        return _client


def ask_agent(messages):
    response = get_client().responses.create(input=messages)
    return (getattr(response, "output_text", "") or "").strip()


def explain(exc):
    """Map a failure to (status, friendly key, log note). Never includes visitor text."""
    name = type(exc).__name__
    if name == "RateLimitError":
        return 503, "busy", "agent rate limit (429); check the model deployment's tokens per minute"
    if name in ("APITimeoutError", "TimeoutError"):
        return 504, "slow", "agent timed out"
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return 502, "unavailable", "agent refused the app's identity; check the role assignment"
    if name == "NotFoundError":
        return 502, "unavailable", "agent endpoint not found; check FOUNDRY_AGENT_BASE_URL"
    return 502, "unavailable", f"agent call failed ({name})"


# These two routes are open to the web (the rest of this app needs a function key).
@bp.route(route="chat/health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def chat_health(req: func.HttpRequest) -> func.HttpResponse:
    return reply_json(200, {"configured": bool(BASE_URL), "in_flight": _in_flight, "max_concurrent": MAX_CONCURRENT},
                      req.headers.get("origin"))


@bp.route(route="chat", methods=["POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
async def chat(req: func.HttpRequest) -> func.HttpResponse:
    global _in_flight
    origin = req.headers.get("origin")
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers={
            **cors_headers(origin), "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400"})

    # Browsers always send Origin on a cross-site POST. This keeps other websites from using the route in a page.
    # It does not stop a script that sets its own headers, which is what the limits are for.
    if not origin or origin.rstrip("/").lower() not in ALLOWED_ORIGINS:
        return reply_json(403, {"error": "This address cannot use the chat."})
    if not BASE_URL:
        log.error("chat failed: FOUNDRY_AGENT_BASE_URL is not set")
        return reply_json(503, {"error": FRIENDLY["unavailable"]}, origin)

    body = req.get_body() or b""
    if len(body) > MAX_BODY_BYTES:
        return reply_json(413, {"error": "That request is too large."}, origin)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return reply_json(400, {"error": "Send JSON."}, origin)
    messages, problem = parse_messages(payload.get("messages") if isinstance(payload, dict) else None)
    if problem:
        return reply_json(400, {"error": problem}, origin)

    allowed, wait = limiter.allow(client_ip(req))
    if not allowed:
        return reply_json(429, {"error": "You are sending messages quickly. Please wait a moment.", "retry_after": wait},
                          origin, {"Retry-After": str(wait)})
    # Wait for a free slot rather than turning the visitor away. While it waits, this request counts as in flight,
    # which is what makes the platform start another instance.
    request_id, began = uuid.uuid4().hex[:8], time.perf_counter()
    slots = _get_slots()
    try:
        await asyncio.wait_for(slots.acquire(), QUEUE_SECONDS)
    except asyncio.TimeoutError:
        log.warning("chat %s busy: no free slot after %ds (in_flight=%d of %d)", request_id, QUEUE_SECONDS, _in_flight, MAX_CONCURRENT)
        return reply_json(503, {"error": FRIENDLY["busy"]}, origin, {"Retry-After": "10"})
    waited = time.perf_counter() - began

    _in_flight += 1
    future = asyncio.get_running_loop().run_in_executor(_pool, ask_agent, messages)

    def finished(done):
        # The slot is held until the call really ends, even if this visitor gave up waiting for it.
        global _in_flight
        _in_flight -= 1
        slots.release()
        if not done.cancelled():
            done.exception()  # mark any failure as seen

    future.add_done_callback(finished)
    try:
        text = await asyncio.wait_for(asyncio.shield(future), TIMEOUT_SECONDS + 5)
    except Exception as exc:  # the visitor sees a friendly line; the log has the cause
        status, key, note = explain(exc)
        detail = f" | {type(exc).__name__}: {str(exc)[:300]}" if LOG_ERROR_DETAIL else ""
        log.error("chat %s failed: %s%s", request_id, note, detail)
        return reply_json(status, {"error": FRIENDLY[key]}, origin)

    # Log sizes and timing only. Visitors may paste resumes, so message text is never logged.
    log.info("chat %s ok turns=%d chars_in=%d chars_out=%d seconds=%.1f waited=%.1f in_flight=%d", request_id, len(messages),
             sum(len(m["content"]) for m in messages), len(text), time.perf_counter() - began, waited, _in_flight)
    if not text:
        return reply_json(502, {"error": FRIENDLY["unavailable"]}, origin)
    return reply_json(200, {"reply": text}, origin)
