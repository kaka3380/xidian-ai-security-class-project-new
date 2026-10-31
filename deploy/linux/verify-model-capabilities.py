import base64
import json
from pathlib import Path
import struct
import urllib.request
import zlib


def main():
    settings = json.loads(Path("workbench/data/model-session/gateway.json").read_text())
    headers = {"Authorization": "Bearer " + settings["token"], "Content-Type": "application/json"}
    base = "http://127.0.0.1:" + str(settings["port"])
    def send(messages, **kwargs):
        request = {"model": "deepseek-flash", "messages": messages, "max_tokens": 1024, **kwargs}
        with urllib.request.urlopen(urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(request).encode(), headers=headers), timeout=190) as response:
            result = json.load(response)
        Path("deploy/linux/evidence/deepseek-last-probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    text = send([{"role": "user", "content": 'Return JSON only: {"ok":true}'}], response_format={"type": "json_object"})
    assert json.loads(text["choices"][0]["message"]["content"])["ok"] is True
    tool = send([{"role": "user", "content": "Call record with value 7."}], tools=[{"type": "function", "function": {"name": "record", "description": "Record test value", "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}}}], tool_choice={"type": "function", "function": {"name": "record"}})
    call = tool["choices"][0]["message"]["tool_calls"][0]["function"]
    assert call["name"] == "record" and json.loads(call["arguments"])["value"] == 7
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 32, 32, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 32) * 32)) + chunk(b"IEND", b"")
    vision = send([{"role": "user", "content": [{"type": "text", "text": 'What is the dominant color? Return JSON {"color":"red|green|blue"}.'}, {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode(), "detail": "low"}}]}], response_format={"type": "json_object"})
    assert json.loads(vision["choices"][0]["message"]["content"])["color"].lower() == "red"
    with urllib.request.urlopen(urllib.request.Request(base + "/budget", headers=headers)) as response:
        usage = json.load(response)
    evidence = {"status": "passed", "model": "deepseek-flash", "json_output": True, "forced_tool_call": True, "vision": True, "budget": usage}
    Path("deploy/linux/evidence/deepseek-capabilities.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
