#!/usr/bin/env python3
"""
test_agent_mode.py — прогоняет агентный путь Copilot сквозь настоящий server.py.

arena.ai подменяется локальным фейком, который отвечает тем же SSE-форматом
(строки a0:/ag:/ad:), что разбирает server.py. Запрос идёт in-process через
httpx ASGITransport, поэтому выполняются настоящие
chat_completions → build_prompt → stream_response → parse_tool_calls.

    python3 test_agent_mode.py
"""
import asyncio
import json
import pathlib
import sys
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402

import server  # noqa: E402

FAKE_PORT = 9911
TOOL_CALL_TEXT = '<tool_call>{"name": "read_file", "arguments": {"path": "main.py"}}</tool_call>'
# arena отдаёт текст кусками: a0:"..." ... ad:{"finishReason":"stop"}
CHUNKS = ["Вызываю ", "инструмент.\n", TOOL_CALL_TEXT]


class FakeArena(BaseHTTPRequestHandler):
    last_body: dict = {}

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            FakeArena.last_body = json.loads(raw)
        except json.JSONDecodeError:
            FakeArena.last_body = {}
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for c in CHUNKS:
            self.wfile.write(f"a0:{json.dumps(c)}\n".encode())
        self.wfile.write(b'ad:{"finishReason":"stop"}\n')

    def log_message(self, *a):
        pass


def prepare_store():
    """Состояние, которое в бою даёт расширение: cookies, токен, модели."""
    server.store.push({
        "cookies": {"arena-user-id": "test-user", "arena-auth-prod-v1": "fake"},
        "auth_token": "fake",
        "user_agent": "test-agent",
        # токен каждый раз новый: push() дедуплицирует по значению,
        # а pop_v3_token() выпивает его безвозвратно
        "v3_tokens": [{"token": "t" + secrets.token_hex(30), "action": "chat_submit", "age_ms": 500}],
        "models": json.load(open(ROOT / "tools" / "models" / "push_payload.json"))["models"],
    })


def parse_sse(text):
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            p = line[6:]
            out.append("[DONE]" if p == "[DONE]" else json.loads(p))
    return out


async def call(body):
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        r = await c.post("/v1/chat/completions", json=body)
        r.raise_for_status()
        return r.text


TOOLS = [{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
}]


def main():
    srv = HTTPServer(("127.0.0.1", FAKE_PORT), FakeArena)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    server.ARENA_CREATE_EVAL = f"http://127.0.0.1:{FAKE_PORT}/nextjs-api/stream/create-evaluation"
    prepare_store()

    checks = []

    # ---- ход 1: Copilot-агент шлёт tools + stream ----
    text = asyncio.run(call({
        "model": "Max",
        "messages": [
            {"role": "system", "content": "You are a coding assistant"},
            {"role": "user", "content": "Прочитай main.py"},
        ],
        "temperature": 0.1,
        "stream": True,
        "tools": TOOLS,
    }))
    events = parse_sse(text)
    deltas = [e["choices"][0]["delta"] for e in events if isinstance(e, dict)]
    tc = next((d["tool_calls"] for d in deltas if "tool_calls" in d), None)
    prompt1 = FakeArena.last_body.get("userMessage", {}).get("content", "")

    checks.append(("SSE распарсился и закончился [DONE]",
                   bool(events) and events[-1] == "[DONE]", f"{len(events)} событий"))
    checks.append(("в deltas есть tool_calls", tc is not None,
                   json.dumps(tc, ensure_ascii=False) if tc else "нет"))
    if tc:
        checks.append(("имя инструмента доехало",
                       tc[0]["function"]["name"] == "read_file", tc[0]["function"]["name"]))
        checks.append(("аргументы — JSON с нужным значением",
                       json.loads(tc[0]["function"]["arguments"]) == {"path": "main.py"},
                       tc[0]["function"]["arguments"]))
        checks.append(("есть id вызова и type=function",
                       bool(tc[0].get("id")) and tc[0].get("type") == "function",
                       f"{tc[0].get('id')} / {tc[0].get('type')}"))
    finishes = [e["choices"][0]["finish_reason"] for e in events
                if isinstance(e, dict) and e["choices"][0].get("finish_reason")]
    checks.append(("finish_reason = tool_calls", finishes == ["tool_calls"], str(finishes)))
    checks.append(("схема инструмента попала в промпт arena", "read_file" in prompt1,
                   "да" if "read_file" in prompt1 else "нет"))
    checks.append(("modality = chat, не image",
                   FakeArena.last_body.get("modality") == "chat",
                   str(FakeArena.last_body.get("modality"))))
    checks.append(("modelAId = UUID модели Max",
                   FakeArena.last_body.get("modelAId") == "019b24bb-5caf-71c3-b854-37d0c7086f21",
                   str(FakeArena.last_body.get("modelAId"))))
    dumped = "".join(json.dumps(d, ensure_ascii=False) for d in deltas)
    checks.append(("сырой тег <tool_call> клиенту не протёк",
                   "<tool_call>" not in dumped,
                   "протёк!" if "<tool_call>" in dumped else "чисто"))

    # ---- ход 2: Copilot возвращает результат инструмента ----
    # токен одноразовый (pop_v3_token его выпивает), как и в бою — кладём свежий
    prepare_store()
    text2 = asyncio.run(call({
        "model": "Max",
        "stream": False,
        "messages": [
            {"role": "user", "content": "Прочитай main.py"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "main.py"}'},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "name": "read_file",
             "content": "print('hello')"},
        ],
        "tools": TOOLS,
    }))
    prompt2 = FakeArena.last_body.get("userMessage", {}).get("content", "")
    resp2 = json.loads(text2)
    checks.append(("ход 2: прошлый вызов восстановлен в промпте",
                   "tool_call" in prompt2 and "read_file" in prompt2, "да" if "tool_call" in prompt2 else "нет"))
    checks.append(("ход 2: результат инструмента попал в промпт",
                   "Tool result (read_file):" in prompt2,
                   "да" if "Tool result (read_file):" in prompt2 else "нет"))
    checks.append(("ход 2: ответ — обычный OpenAI-объект",
                   resp2.get("object") == "chat.completion" and "choices" in resp2,
                   str(resp2.get("object"))))

    fail = 0
    for name, ok, val in checks:
        print(("PASS  " if ok else "FAIL  ") + name + "  (" + str(val) + ")")
        if not ok:
            fail += 1
    srv.shutdown()
    print()
    print(str(fail) + " провалено" if fail else "все проверки пройдены")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
