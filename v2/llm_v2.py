"""v2 model caller: same model and SDK as production, plus a record of the tokens each call used (so the cost of
every reading can be reported). Production's app/llm.py is not modified."""
import time
from typing import List

from app import config
from app.llm import LLMError, TruncatedError, parse_json, text_block

USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        if not config.anthropic_key():
            raise LLMError("ANTHROPIC_API_KEY not set")
        _client = anthropic.Anthropic(api_key=config.anthropic_key(), timeout=600, max_retries=2)
    return _client


def call_json(task: str, system: str, blocks: List[dict], max_tokens: int = 16000):
    import os
    if os.environ.get("V2_ALLOW_API") != "1":
        # the owner's instruction: no Claude API use in v2 unless they explicitly switch it on for a run
        raise LLMError("Claude API use is switched OFF for v2 (set V2_ALLOW_API=1 only with the owner's approval)")
    client, content, last = _get_client(), blocks, None
    for attempt in range(3):
        try:
            with client.messages.stream(model=config.CLAUDE_MODEL, max_tokens=max_tokens, system=system,
                                        messages=[{"role": "user", "content": content}]) as stream:
                msg = stream.get_final_message()
            USAGE["calls"] += 1
            USAGE["input_tokens"] += getattr(msg.usage, "input_tokens", 0) or 0
            USAGE["output_tokens"] += getattr(msg.usage, "output_tokens", 0) or 0
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            if msg.stop_reason == "max_tokens":
                raise TruncatedError(f"[{task}] output truncated at max_tokens={max_tokens}")
            return parse_json(text)
        except TruncatedError:
            raise
        except LLMError as e:
            last = e
            content = blocks + [text_block("Your previous answer was not complete valid JSON. Return ONLY the JSON.")]
        except Exception as e:                       # API / network
            last = e
            time.sleep(5 * (attempt + 1))
    raise LLMError(f"[{task}] failed after retries: {last}")
