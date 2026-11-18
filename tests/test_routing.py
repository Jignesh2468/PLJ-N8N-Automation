"""Routing test: fake Claude + ChatGPT clients, no network.  Run: python tests/test_routing.py"""
import os, sys, types, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.update(ANTHROPIC_API_KEY="x", OPENAI_API_KEY="y")
from app import llm, config  # noqa: E402

calls = []
fail = set()

import inspect
from anthropic.resources.messages import Messages as _AM
from openai.resources.chat.completions import Completions as _OC
_A_OK = set(inspect.signature(_AM.stream).parameters)
_O_OK = set(inspect.signature(_OC.create).parameters)

class FakeStream:
    def __init__(self, kw):
        bad = set(kw) - _A_OK
        assert not bad, f"anthropic SDK does not accept: {bad}"
        self.kw = kw
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def get_final_message(self):
        calls.append(("claude", self.kw["messages"][0]["content"][0]["type"]))
        if "claude" in fail: raise RuntimeError("claude down")
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text='{"by":"claude"}')])

class FakeClaude:
    messages = types.SimpleNamespace(stream=lambda **kw: FakeStream(kw))

def oa_create(**kw):
    bad = set(kw) - _O_OK
    assert not bad, f"openai SDK does not accept: {bad}"
    calls.append(("openai", kw["messages"][1]["content"][0]["type"]))
    assert kw["response_format"] == {"type": "json_object"} and kw["temperature"] == 0
    if "openai" in fail: raise RuntimeError("openai down")
    msg = types.SimpleNamespace(content='{"by":"openai"}')
    return types.SimpleNamespace(choices=[types.SimpleNamespace(finish_reason="stop", message=msg)])

llm._claude = FakeClaude()
llm._openai = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=oa_create)))
llm.time.sleep = lambda s: None

PDF = [llm.pdf_block(b"%PDF-1.4"), llm.text_block("hint")]
IMG = [llm.image_block(b"\xff\xd8", "image/jpeg"), llm.text_block("f")]
TXT = [llm.text_block("docx text")]
def who(task, blocks): return llm.call_json(task, "Return JSON", blocks)["by"]

assert who("extract_documents", PDF) == "claude"    # scanned PDF OCR
assert who("extract_documents", IMG) == "claude"    # photo OCR
assert who("extract_documents", TXT) == "openai"    # DOCX text
assert who("igr_classify", TXT) == "openai"
assert who("draft_title", TXT) == "openai"
print("routing OK")

fail.add("openai")                                   # ChatGPT down → Claude takes over
assert who("igr_classify", TXT) == "claude" and who("draft_title", TXT) == "claude"
fail.clear(); fail.add("claude")                     # Claude down → photos go to ChatGPT, PDFs can't
assert who("extract_documents", IMG) == "openai"
try:
    who("extract_documents", PDF); raise SystemExit("PDF must not go to ChatGPT")
except llm.LLMError as e:
    assert "claude" in str(e) and not any(c == ("openai", "document") for c in calls)
fail.clear()
print("fallback OK", llm.usage())

config.LLM_DRAFT = "claude"                          # env switch works
assert who("draft_title", TXT) == "claude"
print("ALL ROUTING TESTS PASSED")
