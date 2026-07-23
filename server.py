"""
arena2api - Arena.ai to OpenAI API Proxy
=========================================

极简设计：Chrome 扩展提供 reCAPTCHA token 和 cookies，
本服务器负责 OpenAI 格式转换和 arena.ai API 调用。

使用方式：
  1. pip install -r requirements.txt
  2. python server.py
  3. 安装 Chrome 扩展，打开 arena.ai
  4. 在 OpenAI 客户端中配置 http://localhost:9090/v1
"""

import asyncio
import json
import logging
import os
import re
import secrets
import time
import uuid
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse, JSONResponse

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("DEBUG") else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("arena2api")

# ============================================================
# 配置
# ============================================================
PORT = int(os.environ.get("PORT", "9090"))
API_KEY = os.environ.get("API_KEY", "").strip()
ARENA_BASE = "https://arena.ai"
ARENA_CREATE_EVAL = f"{ARENA_BASE}/nextjs-api/stream/create-evaluation"
ARENA_POST_EVAL = f"{ARENA_BASE}/nextjs-api/stream/post-to-evaluation"  # + /{id}

# reCAPTCHA (arena.ai enterprise sitekey)
RECAPTCHA_V3_SITEKEY = "6LeTGMcsAAAAALuIlkVwIxaAuZA8VledA6d3Nnb0"

# Tool-call XML format for emulating OpenAI tools via prompt injection
TOOL_INSTRUCTION = """You have access to the following tools. When you want to call a tool, respond ONLY with this exact format and nothing else:
<tool_call>{{"name": "tool_name", "arguments": {{"arg": "value"}}}}</tool_call>

Available tools:
{tools_json}
"""

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

# ============================================================
# UUIDv7
# ============================================================
def uuid7() -> str:
    ts = int(time.time() * 1000)
    ra = secrets.randbits(12)
    rb = secrets.randbits(62)
    u = ts << 80 | (0x7000 | ra) << 64 | (0x8000000000000000 | rb)
    h = f"{u:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


# ============================================================
# Token / Cookie Store（从扩展接收）
# ============================================================
class Store:
    def __init__(self):
        self.cookies: dict = {}
        self.auth_token: str = ""
        self.cf_clearance: str = ""
        self.v3_tokens: list = []  # [{token, action, ts}]
        self.v2_token: Optional[dict] = None
        self.last_push: float = 0
        self.models: list = []
        self.text_models: dict = {
            "grok-4.5": "019f42aa-9c3b-76d1-8bdf-2e883b1ca227"
        }  # publicName -> id
        self.image_models: dict = {}
        self.vision_models: list = []
        self.next_actions: dict = {}  # action name -> hash
        self.user_agent: str = ""

        # On-demand token coordination
        self.command_queue: asyncio.Queue = asyncio.Queue()
        self.waiting_futures: list[asyncio.Future] = []

    @property
    def active(self) -> bool:
        return self.last_push > 0 and (time.time() - self.last_push < 120)

    def push(self, data: dict):
        self.last_push = time.time()
        if data.get("cookies"):
            self.cookies = data["cookies"]
        if data.get("auth_token"):
            self.auth_token = data["auth_token"]
        if data.get("cf_clearance"):
            self.cf_clearance = data["cf_clearance"]
        if data.get("user_agent"):
            self.user_agent = data["user_agent"]
        # V3 tokens
        new_token_added = False
        if data.get("v3_tokens"):
            for t in data["v3_tokens"]:
                tok = t.get("token", "")
                if not tok or len(tok) < 20:
                    continue
                age = t.get("age_ms", 0)
                if age > 120000:
                    continue
                if any(x["token"] == tok for x in self.v3_tokens):
                    continue
                self.v3_tokens.append({
                    "token": tok,
                    "action": t.get("action", "chat_submit"),
                    "ts": time.time() - age / 1000,
                })
                new_token_added = True
            while len(self.v3_tokens) > 10:
                self.v3_tokens.pop(0)

            # Resolve waiting futures if new token added
            if new_token_added:
                while self.waiting_futures and self.v3_tokens:
                    fut = self.waiting_futures.pop(0)
                    if not fut.done():
                        try:
                            fut.set_result(True)
                        except Exception:
                            pass
        # V2 token
        if data.get("v2_token"):
            v2 = data["v2_token"]
            if v2.get("token") and v2.get("age_ms", 0) < 120000:
                self.v2_token = {
                    "token": v2["token"],
                    "ts": time.time() - v2.get("age_ms", 0) / 1000,
                }
        # Models
        if data.get("models"):
            self._update_models(data["models"])
        # Next actions
        if data.get("next_actions"):
            self.next_actions.update(data["next_actions"])

    def _update_models(self, models: list):
        self.models = models
        self.text_models = {
            "grok-4.5": "019f42aa-9c3b-76d1-8bdf-2e883b1ca227"
        }
        self.image_models = {}
        self.vision_models = []
        for m in models:
            name = m.get("publicName", "")
            mid = m.get("id", "")
            caps = m.get("capabilities", {})
            out_caps = caps.get("outputCapabilities", [])
            in_caps = caps.get("inputCapabilities", [])
            if "text" in out_caps:
                self.text_models[name] = mid
            if "image" in out_caps:
                self.image_models[name] = mid
            if "image" in in_caps:
                self.vision_models.append(name)

    def pop_v3_token(self) -> Optional[str]:
        now = time.time()
        self.v3_tokens = [t for t in self.v3_tokens if now - t["ts"] < 120]
        if not self.v3_tokens:
            return None
        # Pop the newest token (LIFO) for maximum freshness/score
        return self.v3_tokens.pop()["token"]

    async def get_v3_token_ondemand(self, force: bool = False) -> Optional[str]:
        if not force:
            # Try to pop an existing fresh token first
            tok = self.pop_v3_token()
            if tok:
                return tok

        # Request new token from extension
        log.info("Requesting fresh reCAPTCHA token from extension on-demand...")

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.waiting_futures.append(fut)

        await self.command_queue.put({"command": "get_token"})

        # Wait up to 15 seconds for extension to push token
        try:
            await asyncio.wait_for(fut, timeout=15.0)
            return self.pop_v3_token()
        except asyncio.TimeoutError:
            log.warning("Timeout waiting for on-demand token from extension")
            if fut in self.waiting_futures:
                self.waiting_futures.remove(fut)
            return self.pop_v3_token()

    def pop_v2_token(self) -> Optional[str]:
        if not self.v2_token:
            return None
        if time.time() - self.v2_token["ts"] > 120:
            self.v2_token = None
            return None
        tok = self.v2_token["token"]
        self.v2_token = None
        return tok

    def build_cookie_header(self) -> str:
        parts = []
        for k, v in self.cookies.items():
            parts.append(f"{k}={v}")
        return "; ".join(parts)

    def status(self) -> dict:
        now = time.time()
        valid_v3 = [t for t in self.v3_tokens if now - t["ts"] < 120]
        return {
            "active": self.active,
            "last_push_ago": round(now - self.last_push, 1) if self.last_push else None,
            "v3_tokens": len(valid_v3),
            "has_v2": bool(self.v2_token and now - self.v2_token["ts"] < 120),
            "has_auth": bool(self.auth_token),
            "has_cf": bool(self.cf_clearance),
            "user_agent": self.user_agent,
            "text_models": len(self.text_models),
            "image_models": len(self.image_models),
            "next_actions": list(self.next_actions.keys()),
            "cookies": list(self.cookies.keys()),
        }


store = Store()

# ============================================================
# FastAPI
# ============================================================
app = FastAPI(title="arena2api", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def verify_api_key(request: Request):
    """Optional API key auth for OpenAI endpoints.

    If API_KEY is set, require: Authorization: Bearer <API_KEY>
    """
    if not API_KEY:
        return

    auth_header = request.headers.get("authorization", "")
    expected = f"Bearer {API_KEY}"
    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ============================================================
# 扩展端点
# ============================================================
@app.post("/v1/extension/push")
async def extension_push(request: Request):
    """接收扩展推送的 token、cookies、models"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    store.push(data)
    need = len([t for t in store.v3_tokens if time.time() - t["ts"] < 120]) < 3
    return {
        "status": "ok",
        "need_tokens": need,
        "v3_count": len(store.v3_tokens),
    }


@app.get("/v1/extension/status")
async def extension_status():
    return store.status()


@app.get("/v1/extension/command")
async def extension_command():
    """Long-polling command endpoint for the extension"""
    try:
        # Wait up to 30 seconds for a command
        cmd = await asyncio.wait_for(store.command_queue.get(), timeout=30.0)
        return cmd
    except asyncio.TimeoutError:
        return {"command": "idle"}


# ============================================================
# OpenAI 兼容端点
# ============================================================
@app.get("/v1/models")
async def list_models(request: Request):
    """列出可用模型"""
    verify_api_key(request)
    all_models = {}
    all_models.update(store.text_models)
    all_models.update(store.image_models)
    data = []
    for name in sorted(all_models.keys()):
        data.append({
            "id": name,
            "object": "model",
            "created": 0,
            "owned_by": "arena.ai",
        })
    if not data:
        # 返回一个占位模型
        data.append({
            "id": "waiting-for-extension",
            "object": "model",
            "created": 0,
            "owned_by": "arena.ai",
        })
    return {"object": "list", "data": data}


def detect_client(request: Request) -> str:
    """检测客户端类型"""
    ua = request.headers.get("user-agent", "").lower()
    if "claude" in ua or "anthropic" in ua:
        return "claude"
    if "gemini" in ua or "google" in ua:
        return "gemini"
    if "codex" in ua:
        return "codex"
    if "opencode" in ua:
        return "opencode"
    # NewAPI/OneAPI 通常使用标准 OpenAI 格式
    return "openai"


def _msg_text(content) -> str:
    """Extract plain text from OpenAI message content (str or multimodal list)."""
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if p.get("type") == "text")
    return content if isinstance(content, str) else str(content or "")


def build_prompt(messages: list, tools: list | None = None) -> str:
    """Build a single prompt for arena.ai from multi-turn messages + optional tools."""
    parts = []

    # Inject tool schemas as system instruction
    if tools:
        parts.append(TOOL_INSTRUCTION.format(tools_json=json.dumps(tools, ensure_ascii=False, indent=2)))

    for msg in messages:
        role = msg.get("role", "user")
        content = _msg_text(msg.get("content", ""))

        # OpenAI tool results → feed back as plain text
        if role == "tool":
            name = msg.get("name", "tool")
            parts.append(f"Tool result ({name}):\n{content}")
            continue

        # Assistant tool_calls → reconstruct as tool_call XML
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                parts.append(f'<tool_call>{json.dumps({"name": fn.get("name", ""), "arguments": args}, ensure_ascii=False)}</tool_call>')
            if content:
                parts.append(f"Assistant: {content}")
            continue

        label = {"system": "System", "user": "User", "assistant": "Assistant"}.get(role, role.capitalize())
        if content:
            parts.append(f"{label}: {content}")

    return "\n\n".join(parts)


def parse_tool_calls(text: str) -> list | None:
    """Extract <tool_call>{...}</tool_call> blocks → OpenAI tool_calls list, or None."""
    matches = TOOL_CALL_RE.findall(text)
    if not matches:
        return None
    tool_calls = []
    for i, raw in enumerate(matches):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        name = obj.get("name", "")
        args = obj.get("arguments", {})
        if not name:
            continue
        tool_calls.append({
            "id": f"call_{uuid7()[:12]}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args),
            },
        })
    return tool_calls or None


def resolve_model(model_name: str) -> tuple[str, str]:
    """Resolve model name → (publicName, arena_uuid). Fuzzy match: grok / grok4 → grok-4.5."""
    if "/" in model_name:
        model_name = model_name.split("/")[-1]

    all_models = {**store.text_models, **store.image_models}

    # Exact match
    if model_name in all_models:
        return model_name, all_models[model_name]

    needle = model_name.lower().replace(" ", "").replace("_", "").replace(".", "")
    # Fuzzy: substring either way, ignoring separators
    for name, mid in all_models.items():
        clean = name.lower().replace(" ", "").replace("_", "").replace(".", "").replace("-", "")
        if needle in clean or clean in needle:
            return name, mid
        # Also try raw lower substring
        if model_name.lower() in name.lower() or name.lower() in model_name.lower():
            return name, mid

    return model_name, ""


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 兼容的聊天补全"""
    verify_api_key(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    client_type = detect_client(request)
    model_name = body.get("model", "")
    messages = body.get("messages", [])
    stream = body.get("stream", False)
    tools = body.get("tools")
    # tool_choice ignored for emulation (always available when tools present)

    if not messages:
        raise HTTPException(400, "messages is required")

    # 检查扩展是否连接
    if not store.active:
        raise HTTPException(503, "Extension not connected. Please open arena.ai in Chrome with the extension installed.")

    # 解析模型 (exact + fuzzy)
    model_name, model_id = resolve_model(model_name)
    if not model_id:
        available = list(store.text_models.keys()) + list(store.image_models.keys())
        raise HTTPException(404, f"Model '{model_name}' not found. Available: {available[:20]}")

    # Build multi-turn prompt + optional tool schemas
    prompt = build_prompt(messages, tools=tools)

    # 获取 reCAPTCHA token (on-demand)
    v3_token = await store.get_v3_token_ondemand()
    v2_token = store.pop_v2_token() if not v3_token else None

    is_image = model_name in store.image_models
    modality = "image" if is_image else "chat"

    # Build arena.ai request — fresh UUIDv7s each request
    eval_id = uuid7()
    user_msg_id = uuid7()
    model_a_msg_id = uuid7()

    # Extract userId from cookies
    user_id = store.cookies.get("arena-user-id", "")
    if not user_id:
        for key, value in store.cookies.items():
            if "user" in key.lower() and len(value) > 20:
                user_id = value
                break

    arena_payload = {
        "id": eval_id,
        "mode": "direct-battle",
        "modelAId": model_id,
        "userMessageId": user_msg_id,
        "modelAMessageId": model_a_msg_id,
        "userMessage": {
            "content": prompt,
            "experimental_attachments": [],
            "metadata": {},
        },
        "modality": modality,
    }

    if user_id:
        arena_payload["userId"] = user_id

    if v2_token:
        arena_payload["recaptchaV2Token"] = v2_token
        arena_payload["recaptchaV3Token"] = None
    elif v3_token:
        arena_payload["recaptchaV3Token"] = v3_token
    else:
        log.error("No reCAPTCHA token available after on-demand check")
        raise HTTPException(503, "reCAPTCHA token missing. Please ensure your arena.ai tab is active and visible.")

    # Use real user-agent from store if available
    ua = store.user_agent or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

    # 构建 headers
    headers = {
        "accept": "*/*",
        "content-type": "text/plain;charset=UTF-8",
        "origin": ARENA_BASE,
        "referer": f"{ARENA_BASE}/c/{eval_id}",
        "user-agent": ua,
        "cookie": store.build_cookie_header(),
    }

    # Authorization Bearer from arena-auth-prod-v1 cookie (may be fragmented)
    auth = store.auth_token
    if auth and auth.startswith("base64-"):
        try:
            import base64
            raw_b64 = auth[7:]
            missing_padding = len(raw_b64) % 4
            if missing_padding:
                raw_b64 += "=" * (4 - missing_padding)
            decoded = json.loads(base64.b64decode(raw_b64).decode("utf-8", errors="ignore"))
            access_token = decoded.get("access_token")
            if access_token:
                headers["authorization"] = f"Bearer {access_token}"
                log.info("Injected Authorization header from Supabase session")
        except Exception as e:
            log.warning(f"Failed to decode auth token: {e}")
            # Do NOT send mangled base64- prefix token in Authorization header
    elif auth:
        headers["authorization"] = f"Bearer {auth}"

    # Log payload and headers for debugging
    log.debug(f"Payload sent to arena: {json.dumps(arena_payload)}")
    log.debug(f"Headers sent to arena (auth: {bool(headers.get('authorization'))}): {list(headers.keys())}")

    url = ARENA_CREATE_EVAL
    log.info(f"Sending to arena.ai: model={model_name}, eval_id={eval_id}, has_v3={bool(v3_token)}, has_v2={bool(v2_token)}, tools={bool(tools)}")

    if stream:
        return StreamingResponse(
            stream_response(url, arena_payload, headers, model_name, eval_id, client_type, has_tools=bool(tools)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        return await non_stream_response(url, arena_payload, headers, model_name, eval_id, client_type, has_tools=bool(tools))


def _sse_chunk(chat_id, created, model_name, delta, finish_reason=None):
    """Build one OpenAI SSE data line."""
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_name,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }


async def stream_response(url, payload, headers, model_name, eval_id, client_type="openai", has_tools=False):
    """流式响应生成器. When has_tools=True, buffer content and emit tool_calls if detected."""
    chat_id = f"chatcmpl-{eval_id}"
    created = int(time.time())
    buffered = []  # buffer all text when tools present

    req_payload = payload.copy()
    req_headers = headers.copy()

    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            for attempt in range(2):
                if attempt > 0:
                    log.info("Retrying stream request with a fresh on-demand token...")
                    v3_token = await store.get_v3_token_ondemand(force=True)
                    if v3_token:
                        req_payload["recaptchaV3Token"] = v3_token
                    else:
                        yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'content': '[Error: Failed to get fresh reCAPTCHA token for retry]'}, 'stop'))}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    # Regenerate eval ID to avoid cache/overlap issues
                    new_eval_id = uuid7()
                    req_payload["id"] = new_eval_id
                    req_payload["userMessageId"] = uuid7()
                    req_payload["modelAMessageId"] = uuid7()
                    req_headers["referer"] = f"{ARENA_BASE}/c/{new_eval_id}"

                async with client.stream("POST", url, content=json.dumps(req_payload), headers=req_headers) as resp:
                    if resp.status_code == 429 and attempt == 0:
                        body = await resp.aread()
                        log.warning(f"Arena API 429 on first attempt. Error body: {body[:200]}")
                        continue  # retry

                    if resp.status_code != 200:
                        body = await resp.aread()
                        log.error(f"Arena API error: {resp.status_code} {body[:500]}")
                        yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'content': f'[Error: Arena API returned {resp.status_code}]'}, 'stop'))}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    # role chunk first (OpenAI convention)
                    yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'role': 'assistant'}))}\n\n"

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue

                        content = None
                        reasoning = None
                        finish = None

                        if line.startswith("a0:"):
                            try:
                                content = json.loads(line[3:])
                                if content == "hasArenaError":
                                    content = "[Arena Error]"
                                    finish = "stop"
                            except json.JSONDecodeError:
                                continue
                        elif line.startswith("ag:"):
                            try:
                                reasoning = json.loads(line[3:])
                            except json.JSONDecodeError:
                                continue
                        elif line.startswith("ad:"):
                            finish = "stop"
                            try:
                                data = json.loads(line[3:])
                                if data.get("finishReason"):
                                    finish = data["finishReason"]
                            except json.JSONDecodeError:
                                pass
                        elif line.startswith("a2:"):
                            if "heartbeat" in line:
                                continue
                            try:
                                data = json.loads(line[3:])
                                images = [img.get("image") for img in data if img.get("image")]
                                if images:
                                    content = "\n".join(f"![image]({u})" for u in images)
                            except json.JSONDecodeError:
                                continue
                        elif line.startswith("a3:"):
                            try:
                                content = f"[Error: {json.loads(line[3:])}]"
                            except Exception:
                                content = f"[Error: {line[3:]}]"
                            finish = "stop"
                        else:
                            continue

                        if content is not None:
                            if has_tools:
                                buffered.append(content)
                            else:
                                yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'content': content}), ensure_ascii=False)}\n\n"

                        if reasoning is not None and not has_tools:
                            yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'reasoning_content': reasoning}), ensure_ascii=False)}\n\n"

                        if finish:
                            if has_tools:
                                full = "".join(buffered)
                                tool_calls = parse_tool_calls(full)
                                if tool_calls:
                                    # Emit tool_calls as a single delta
                                    yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'tool_calls': [
                                        {**tc, 'index': i} for i, tc in enumerate(tool_calls)
                                    ]}), ensure_ascii=False)}\n\n"
                                    yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {}, 'tool_calls'))}\n\n"
                                else:
                                    # No tool call — stream buffered content
                                    if full:
                                        yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'content': full}), ensure_ascii=False)}\n\n"
                                    yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {}, 'stop'))}\n\n"
                            else:
                                yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {}, finish if finish != 'stop' else 'stop'))}\n\n"
                            yield "data: [DONE]\n\n"
                            return

                    # Stream ended without ad: finish marker
                    if has_tools and buffered:
                        full = "".join(buffered)
                        tool_calls = parse_tool_calls(full)
                        if tool_calls:
                            yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'tool_calls': [
                                {**tc, 'index': i} for i, tc in enumerate(tool_calls)
                            ]}), ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {}, 'tool_calls'))}\n\n"
                        else:
                            yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'content': full}), ensure_ascii=False)}\n\n"
                            yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {}, 'stop'))}\n\n"
                    else:
                        yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {}, 'stop'))}\n\n"
                    yield "data: [DONE]\n\n"
                    return  # break outer loop on success

    except Exception as e:
        log.error(f"Stream error: {e}")
        yield f"data: {json.dumps(_sse_chunk(chat_id, created, model_name, {'content': f'[Stream Error: {e}]'}, 'stop'))}\n\n"
        yield "data: [DONE]\n\n"


async def non_stream_response(url, payload, headers, model_name, eval_id, client_type="openai", has_tools=False):
    """非流式响应"""
    content_parts = []
    reasoning_parts = []
    finish_reason = "stop"
    usage = {}

    req_payload = payload.copy()
    req_headers = headers.copy()

    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            for attempt in range(2):
                if attempt > 0:
                    log.info("Retrying non-stream request with a fresh on-demand token...")
                    v3_token = await store.get_v3_token_ondemand(force=True)
                    if v3_token:
                        req_payload["recaptchaV3Token"] = v3_token
                    else:
                        raise HTTPException(503, "Failed to get fresh reCAPTCHA token for retry")

                    # Regenerate eval ID to avoid cache/overlap issues
                    new_eval_id = uuid7()
                    req_payload["id"] = new_eval_id
                    req_payload["userMessageId"] = uuid7()
                    req_payload["modelAMessageId"] = uuid7()
                    req_headers["referer"] = f"{ARENA_BASE}/c/{new_eval_id}"

                async with client.stream("POST", url, content=json.dumps(req_payload), headers=req_headers) as resp:
                    if resp.status_code == 429 and attempt == 0:
                        body = await resp.aread()
                        log.warning(f"Arena API 429 on first attempt. Error body: {body[:200]}")
                        continue  # retry

                    if resp.status_code != 200:
                        body = await resp.aread()
                        log.error(f"Arena API error: {resp.status_code} {body[:500]}")
                        raise HTTPException(resp.status_code, f"Arena API error: {body[:200]}")

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        if line.startswith("a0:"):
                            try:
                                text = json.loads(line[3:])
                                if isinstance(text, str) and text != "hasArenaError":
                                    content_parts.append(text)
                            except json.JSONDecodeError:
                                pass
                        elif line.startswith("ag:"):
                            try:
                                text = json.loads(line[3:])
                                if isinstance(text, str):
                                    reasoning_parts.append(text)
                            except json.JSONDecodeError:
                                pass
                        elif line.startswith("ad:"):
                            try:
                                data = json.loads(line[3:])
                                if data.get("finishReason"):
                                    finish_reason = data["finishReason"]
                                if data.get("usage"):
                                    usage = data["usage"]
                            except json.JSONDecodeError:
                                pass
                        elif line.startswith("a2:"):
                            if "heartbeat" in line:
                                continue
                            try:
                                data = json.loads(line[3:])
                                images = [img.get("image") for img in data if img.get("image")]
                                for img_url in images:
                                    content_parts.append(f"![image]({img_url})")
                            except json.JSONDecodeError:
                                pass
                        elif line.startswith("a3:"):
                            try:
                                content_parts.append(f"[Error: {json.loads(line[3:])}]")
                            except Exception:
                                content_parts.append(f"[Error: {line[3:]}]")
                    break  # break outer loop on success

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Non-stream error: {e}")
        raise HTTPException(500, str(e))

    full_content = "".join(content_parts)
    full_reasoning = "".join(reasoning_parts)

    # Tool call detection
    tool_calls = parse_tool_calls(full_content) if has_tools else None

    if tool_calls:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls,
        }
        finish_reason = "tool_calls"
    else:
        message = {"role": "assistant", "content": full_content}
        if full_reasoning:
            message["reasoning_content"] = full_reasoning

    response = {
        "id": f"chatcmpl-{eval_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": usage or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

    # Claude 格式兼容
    if client_type == "claude" and not tool_calls:
        response["type"] = "message"
        response["role"] = "assistant"
        response["content"] = [{"type": "text", "text": full_content}]

    return response


# ============================================================
# 健康检查
# ============================================================
@app.get("/health")
@app.get("/")
async def health():
    st = store.status()
    return {
        "status": "ok",
        "version": "1.0.0",
        "extension_connected": st["active"],
        "token_count": st["v3_tokens"],
        "model_count": st["text_models"] + st["image_models"],
        "extension": st,
    }


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    log.info(f"Starting arena2api on port {PORT}")
    log.info(f"OpenAI API: http://localhost:{PORT}/v1")
    log.info("Waiting for Chrome extension to connect...")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
