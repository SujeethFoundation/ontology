"""Chat route for the sujeeth.org chat window, hosted in the ontology app.

The page cannot call the Foundry agent itself: the agent endpoint accepts only Microsoft Entra tokens, and
tokens must never be put in a web page. This route takes the conversation from the page, calls the agent
as the app's managed identity, and returns the reply.

  POST /api/chat          {"messages": [{"role": "user" | "assistant", "content": "...", "attachments": [...]}]}  ->  {"reply": "..."}
  GET  /api/chat/health   ->  {"configured": true}

Visitors can attach files (PDF, Word, PowerPoint, Excel, text, Markdown, CSV) to a message. The page sends each as
base64; chat_attachments.py reads the text out of it and adds that text to the visitor's message before the agent
sees it. Attachment limits and settings are described in chat_attachments.py.

Every other route in this app needs a function key. These two are open to the web on purpose (the chat page
has no key to send), so they check the page's address, limit requests, and cap how many chats run at once.

It is loaded only when CHAT_ENABLED=true. Settings (application settings of the Function App):
  FOUNDRY_AGENT_BASE_URL   The agent's OpenAI-compatible base URL (required).
  FOUNDRY_API_VERSION      Optional api-version query value, only if your endpoint needs one.
  CHAT_ALLOWED_ORIGINS     Page origins allowed to use the route (default https://www.sujeeth.org,https://sujeeth.org).
  CHAT_RATE_PER_MINUTE (8), CHAT_RATE_PER_HOUR (60), CHAT_GLOBAL_PER_HOUR (600)   Limits per instance.
  CHAT_MAX_CONCURRENT (8)  Chats being answered at one time on an instance.
  CHAT_QUEUE_SECONDS (25)  How long a chat waits for a free slot before it is told the assistant is busy.
  CHAT_RATE_LIMIT_WAIT_SECONDS (40)  How long a chat keeps waiting when the model says it is over its tokens-per-minute limit
                           (it waits as the model asks, and all chats on the instance pause together).
  CHAT_MODEL_RETRIES (1)   Quick retries for connection problems and server errors.
  CHAT_MAX_MESSAGE_CHARS (2000)  What a visitor types in one message. Text read from attached files has its own limits.
  CHAT_MAX_HISTORY_MESSAGES (12), CHAT_TIMEOUT_SECONDS (90)
  CHAT_MAX_BODY_BYTES (12 MB)    The whole request, including base64 files. Keep it above CHAT_MAX_TOTAL_FILE_BYTES x 1.34.
  CHAT_EXTRACT_TIMEOUT_SECONDS (25)  How long reading the attached files may take.
  CHAT_SEND_CORS_HEADERS (true)  When the Function App's own CORS list already allows the page (portal > API > CORS), the platform
                                 adds the CORS headers itself. Set this to false so the route does not add them a second time
                                 (a browser rejects a response whose Access-Control-Allow-Origin appears twice). The route still
                                 refuses any page that is not in CHAT_ALLOWED_ORIGINS.
  CHAT_LOG_ERROR_DETAIL (false)  Set to true while diagnosing: failures also log the error's text (first 300 characters).
                                 Off by default, because an error's text can echo part of what a visitor wrote.

How it scales: Flex Consumption adds instances when an instance's requests in flight reach its per-instance HTTP
concurrency (a Python app's default is 1; see the README for the setting). A chat that is waiting for a slot is
still a request in flight, so a full instance makes the platform start another one instead of turning visitors
away. Set CHAT_MAX_CONCURRENT at or above that per-instance setting, so the platform's limit is the one that applies.

Why chats stay off the worker's threads: this app also answers every search's domain lookups (through websearch).
A chat waits up to a minute and a half for the agent, so chats are async and run on a small pool of their own.
Reading attached files is CPU work, so it runs on its own two-thread pool, not on the event loop.
"""
import asyncio
import json
import logging
import os
import random
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

import azure.functions as func

import chat_attachments as attachments

bp = func.Blueprint()
log = logging.getLogger("sujeeth-chat")

FRIENDLY = {
    "busy": "The assistant is busy right now. Please try again in a minute.",
    "unavailable": "The assistant is unavailable right now. Please try again in a minute, or browse sujeeth.org.",
    "slow": "That took too long. Please try again, or ask a shorter question.",
    "file": "I couldn't read that file. Please try a PDF or Word version.",
}
DEFAULT_FILE_PROMPT = "Please review the attached file(s)."


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
MODEL_RETRIES = max(0, _int("CHAT_MODEL_RETRIES", 1))
RATE_LIMIT_WAIT_SECONDS = max(0, _int("CHAT_RATE_LIMIT_WAIT_SECONDS", 40))
LOG_ERROR_DETAIL = (os.getenv("CHAT_LOG_ERROR_DETAIL") or "").strip().lower() in ("1", "true", "yes")
SEND_CORS_HEADERS = (os.getenv("CHAT_SEND_CORS_HEADERS") or "true").strip().lower() not in ("0", "false", "no")
MAX_MESSAGE_CHARS = _int("CHAT_MAX_MESSAGE_CHARS", 2000)
MAX_HISTORY_MESSAGES = _int("CHAT_MAX_HISTORY_MESSAGES", 12)
TIMEOUT_SECONDS = _int("CHAT_TIMEOUT_SECONDS", 90)
MAX_BODY_BYTES = _int("CHAT_MAX_BODY_BYTES", 12 * 1024 * 1024)
EXTRACT_TIMEOUT_SECONDS = _int("CHAT_EXTRACT_TIMEOUT_SECONDS", 25)

_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="chat")
_extract_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chat-files")
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
    if not SEND_CORS_HEADERS:
        return {}
    if origin and origin.rstrip("/").lower() in ALLOWED_ORIGINS:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {"Vary": "Origin"}


def reply_json(status, body, origin=None, extra=None):
    headers = {"Cache-Control": "no-store", **cors_headers(origin), **(extra or {})}
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/json", headers=headers)


def parse_messages(raw):
    """Check the conversation the page sent. Returns (messages, error).
    Each message is {"role", "content", "attachments"}. Attachments are checked for count, type and size here,
    but not read; build_agent_messages does that."""
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
        files = []
        if role == "user":  # the page never attaches files to the assistant's turns
            try:
                files = attachments.validate_attachments(item.get("attachments"))
            except attachments.AttachmentError as exc:
                return None, str(exc)
        if not content and not files:
            continue
        if len(content) > MAX_MESSAGE_CHARS:
            return None, f"Messages can be up to {MAX_MESSAGE_CHARS} characters."
        messages.append({"role": role, "content": content, "attachments": files})
    if not messages or messages[-1]["role"] != "user":
        return None, "The last message must be from the visitor."
    while messages and messages[0]["role"] != "user":  # the agent should see the conversation start with the visitor
        messages.pop(0)
    total = sum(f["size"] for m in messages for f in m["attachments"])
    if total > attachments.MAX_TOTAL_FILE_BYTES:
        return None, (f"Attachments in the recent conversation must total no more than "
                      f"{attachments.MAX_TOTAL_FILE_BYTES // (1024 * 1024)} MB. Start a new conversation to attach more.")
    return messages, None


def build_agent_messages(messages):
    """Read the attached files and fold their text into the visitor's messages.
    Returns a list of {"role", "content"} for the agent. Raises attachments.AttachmentError for a file that cannot
    be read. Runs on a worker thread. Newest messages are read first, so when the text budget runs out it is the
    oldest files that are left out, and the agent is told."""
    budget = attachments.MAX_TOTAL_ATTACHMENT_CHARS
    built = []
    for message in reversed(messages):
        parts = [message["content"]] if message["content"] else []
        blocks = []
        for file in message["attachments"]:
            if budget <= 0:
                blocks.append(f"[Attached file: {file['name']}]\n[Left out: earlier files in this conversation already "
                              f"used up the room for attached text.]\n[End of attached file]")
                continue
            text = attachments.extract_text(file, min(attachments.MAX_ATTACHMENT_CHARS, budget))
            budget -= len(text)
            blocks.append(attachments.format_attachment(file["name"], text))
        if blocks and not parts:
            parts.append(DEFAULT_FILE_PROMPT)
        built.append({"role": message["role"], "content": "\n\n".join(parts + blocks)})
    built.reverse()
    return built


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


_cooldown_until = 0.0  # monotonic time before which this instance sends no new model calls; a 429 sets it
_cooldown_lock = threading.Lock()


def _retry_after_seconds(exc):
    """How long the service asked us to wait, from its Retry-After headers, or None if it did not say."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        millis = headers.get("retry-after-ms")
        if millis:
            return float(millis) / 1000
        seconds = headers.get("retry-after")
        if seconds:
            return float(seconds)
    except (TypeError, ValueError):
        pass
    return None


def ask_agent(messages):
    """Ask the agent. If the model says it is over its token limit, wait as it asks and try again, for up to
    CHAT_RATE_LIMIT_WAIT_SECONDS. A limit hit by one chat pauses every chat on this instance, so they come back
    together once, instead of each retrying on its own and keeping the limit exceeded."""
    global _cooldown_until
    deadline = time.monotonic() + RATE_LIMIT_WAIT_SECONDS
    attempt = 0
    while True:
        pause = _cooldown_until - time.monotonic()
        if pause > 0:
            time.sleep(max(0.0, min(pause, deadline - time.monotonic())) + random.uniform(0, 0.5))
        try:
            response = get_client().responses.create(input=messages)
            return (getattr(response, "output_text", "") or "").strip()
        except Exception as exc:
            if type(exc).__name__ != "RateLimitError" or time.monotonic() >= deadline:
                raise
            attempt += 1
            wait = _retry_after_seconds(exc)
            if wait is None:
                wait = min(2 ** attempt, 15)
            wait = min(max(wait, 1.0), 20.0)
            with _cooldown_lock:
                _cooldown_until = max(_cooldown_until, time.monotonic() + wait)
            log.warning("chat model over its token limit (429); waiting %.1fs (attempt %d)", wait, attempt)


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
        preflight = ({"Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type",
                      "Access-Control-Max-Age": "86400"} if SEND_CORS_HEADERS else {})
        return func.HttpResponse(status_code=204, headers={**cors_headers(origin), **preflight})

    # Browsers always send Origin on a cross-site POST. This keeps other websites from using the route in a page.
    # It does not stop a script that sets its own headers, which is what the limits are for.
    if not origin or origin.rstrip("/").lower() not in ALLOWED_ORIGINS:
        return reply_json(403, {"error": "This address cannot use the chat."})
    if not BASE_URL:
        log.error("chat failed: FOUNDRY_AGENT_BASE_URL is not set")
        return reply_json(503, {"error": FRIENDLY["unavailable"]}, origin)

    body = req.get_body() or b""
    if len(body) > MAX_BODY_BYTES:
        return reply_json(413, {"error": "That request is too large. Attach files of "
                                         f"{attachments.MAX_FILE_BYTES // (1024 * 1024)} MB or less."}, origin)
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

    request_id, began = uuid.uuid4().hex[:8], time.perf_counter()
    file_count = sum(len(m["attachments"]) for m in messages)

    # Read any attached files before taking a chat slot, on a pool of their own, so a slow PDF never holds one.
    try:
        agent_messages = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(_extract_pool, build_agent_messages, messages),
            EXTRACT_TIMEOUT_SECONDS)
    except attachments.AttachmentError as exc:
        log.info("chat %s file rejected: %s", request_id, exc)  # the message names the file, never its contents
        return reply_json(400, {"error": str(exc)}, origin)
    except Exception as exc:
        detail = f" | {type(exc).__name__}: {str(exc)[:300]}" if LOG_ERROR_DETAIL else ""
        log.error("chat %s failed: reading attached files (%s)%s", request_id, type(exc).__name__, detail)
        return reply_json(400, {"error": FRIENDLY["file"]}, origin)

    # Wait for a free slot rather than turning the visitor away. While it waits, this request counts as in flight,
    # which is what makes the platform start another instance.
    slots = _get_slots()
    try:
        await asyncio.wait_for(slots.acquire(), QUEUE_SECONDS)
    except asyncio.TimeoutError:
        log.warning("chat %s busy: no free slot after %ds (in_flight=%d of %d)", request_id, QUEUE_SECONDS, _in_flight, MAX_CONCURRENT)
        return reply_json(503, {"error": FRIENDLY["busy"]}, origin, {"Retry-After": "10"})
    waited = time.perf_counter() - began

    _in_flight += 1
    future = asyncio.get_running_loop().run_in_executor(_pool, ask_agent, agent_messages)

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

    # Log sizes and timing only. Visitors may paste or attach resumes, so message and file text is never logged.
    log.info("chat %s ok turns=%d files=%d chars_in=%d chars_out=%d seconds=%.1f waited=%.1f in_flight=%d", request_id,
             len(agent_messages), file_count, sum(len(m["content"]) for m in agent_messages), len(text),
             time.perf_counter() - began, waited, _in_flight)
    if not text:
        return reply_json(502, {"error": FRIENDLY["unavailable"]}, origin)
    return reply_json(200, {"reply": text}, origin)
