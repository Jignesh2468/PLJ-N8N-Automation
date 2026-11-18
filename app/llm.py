"""LLM gateway with two providers. Every call returns parsed JSON or raises LLMError.

Routing (who does which job):
  extract_documents with a PDF / image  -> Claude   (OCR of scanned deeds, Index-II, Marathi; hard task)
  extract_documents with plain text     -> ChatGPT  (DOCX already has text; easy task)
  igr_classify                          -> ChatGPT  (rows are already text)
  draft_title                           -> ChatGPT  (writes from verified facts only)
Change any of these with env vars LLM_EXTRACT_TEXT / LLM_IGR / LLM_DRAFT = openai | claude.
If the chosen provider fails, the call is retried once on the other provider (LLM_FALLBACK=true).
PDFs always go to Claude: ChatGPT cannot read scanned PDF pages directly.

For offline tests set an override:  llm.set_override(fn)  where fn(task, system, blocks) -> dict
"""
import base64
import json
import logging
import re
import threading
import time
from typing import Callable, List, Optional

from . import config

log = logging.getLogger("plj.llm")


class LLMError(RuntimeError):
    pass


class TruncatedError(LLMError):
    """The answer did not fit in max_tokens — retrying the same request cannot help; ask for less instead."""


_override: Optional[Callable] = None
_claude = None
_openai = None
_usage = {"claude": 0, "openai": 0, "fallbacks": 0}
_usage_lock = threading.Lock()


def set_override(fn: Optional[Callable]):
    global _override
    _override = fn


def usage() -> dict:
    with _usage_lock:
        return dict(_usage)


def _count(key: str):
    with _usage_lock:
        _usage[key] += 1


# ── content blocks (provider-neutral; converted per provider) ───────────────
def pdf_block(pdf_bytes: bytes) -> dict:
    return {"type": "document",
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.standard_b64encode(pdf_bytes).decode()}}


def image_block(img_bytes: bytes, media_type: str) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": media_type,
                       "data": base64.standard_b64encode(img_bytes).decode()}}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def parse_json(raw: str):
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = s.find(open_c), s.rfind(close_c)
        if a != -1 and b > a:
            try:
                return json.loads(s[a:b + 1])
            except Exception:
                continue
    raise LLMError(f"Model did not return valid JSON: {s[:300]}")


# ── routing ──────────────────────────────────────────────────────────────────
def _has(blocks, kind: str) -> bool:
    return any(b.get("type") == kind for b in blocks)


def route(task: str, blocks: List[dict]) -> str:
    """Return 'claude' or 'openai' for this call."""
    if _has(blocks, "document"):
        return "claude"                                   # scanned PDF → OCR → Claude only
    if task == "extract_documents":
        if _has(blocks, "image"):
            return config.LLM_EXTRACT_IMAGE               # photos of documents (default claude)
        return config.LLM_EXTRACT_TEXT                    # DOCX text (default openai)
    if task == "igr_classify":
        return config.LLM_IGR
    if task == "draft_title":
        return config.LLM_DRAFT
    return config.LLM_DEFAULT


def _available(provider: str) -> bool:
    return bool(config.anthropic_key() if provider == "claude" else config.openai_key())


def _can_handle(provider: str, blocks) -> bool:
    return provider == "claude" or not _has(blocks, "document")


# ── Claude ──────────────────────────────────────────────────────────────────
def _claude_client():
    global _claude
    if _claude is None:
        import anthropic
        if not config.anthropic_key():
            raise LLMError("ANTHROPIC_API_KEY not set")
        _claude = anthropic.Anthropic(api_key=config.anthropic_key(), timeout=600, max_retries=2)
    return _claude


def _call_claude(task, system, blocks, max_tokens):
    client = _claude_client()
    content = blocks
    last_err = None
    for attempt in range(3):
        try:
            # no temperature: anthropic SDK 1.x removed that parameter
            with client.messages.stream(model=config.CLAUDE_MODEL, max_tokens=max_tokens,
                                        system=system, messages=[{"role": "user", "content": content}]) as stream:
                msg = stream.get_final_message()
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            if msg.stop_reason == "max_tokens":
                raise TruncatedError(f"[{task}] Claude output truncated at max_tokens={max_tokens}")
            return parse_json(text)
        except TruncatedError:
            raise
        except LLMError as e:
            last_err = e
            content = blocks + [text_block("Your previous answer was not complete valid JSON. "
                                           "Return ONLY the JSON object, nothing else.")]
        except TypeError as e:  # wrong SDK parameter — retrying will not help
            raise LLMError(f"[{task}] Claude SDK call is invalid: {e}")
        except Exception as e:  # API / network
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise LLMError(f"[{task}] Claude failed after retries: {last_err}")


# ── ChatGPT (OpenAI) ────────────────────────────────────────────────────────
def _openai_client():
    global _openai
    if _openai is None:
        import openai
        if not config.openai_key():
            raise LLMError("OPENAI_API_KEY not set")
        _openai = openai.OpenAI(api_key=config.openai_key(), timeout=600, max_retries=2)
    return _openai


def _to_openai_content(blocks):
    out = []
    for b in blocks:
        t = b.get("type")
        if t == "text":
            out.append({"type": "text", "text": b["text"]})
        elif t == "image":
            src = b["source"]
            out.append({"type": "image_url",
                        "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}", "detail": "high"}})
        else:
            raise LLMError("ChatGPT cannot take PDF blocks — route PDFs to Claude")
    return out


def _call_openai(task, system, blocks, max_tokens):
    client = _openai_client()
    model = config.OPENAI_MODEL
    content = _to_openai_content(blocks)
    # JSON mode requires the word "JSON" in the prompt; all PLJ prompts contain it, add a guard anyway.
    sys_msg = system if "json" in system.lower() else system + "\nReturn JSON only."
    kwargs = {"model": model, "response_format": {"type": "json_object"},
              "max_completion_tokens": min(max_tokens, config.OPENAI_MAX_OUTPUT)}
    if model.startswith(("gpt-4", "gpt-3")):
        kwargs["temperature"] = 0          # reasoning models (o-series / gpt-5) reject temperature
    last_err = None
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": content}], **kwargs)
            choice = r.choices[0]
            if choice.finish_reason == "length":
                raise TruncatedError(f"[{task}] ChatGPT output truncated at {kwargs['max_completion_tokens']} tokens")
            return parse_json(choice.message.content or "")
        except TruncatedError:
            raise
        except LLMError as e:
            last_err = e
            if "truncated" in str(e):
                break                      # retrying gives the same length — let the fallback take it
            content = content + [{"type": "text", "text": "Return ONLY the complete JSON object."}]
        except Exception as e:
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise LLMError(f"[{task}] ChatGPT failed after retries: {last_err}")


_CALLERS = {"claude": _call_claude, "openai": _call_openai}


# ── public entry point ──────────────────────────────────────────────────────
def call_json(task: str, system: str, blocks: List[dict], max_tokens: int = 16000):
    """task is a short label: extract_documents | igr_classify | draft_title."""
    if _override is not None:
        return _override(task, system, blocks)

    primary = route(task, blocks)
    other = "openai" if primary == "claude" else "claude"
    order = [primary]
    if config.LLM_FALLBACK and _can_handle(other, blocks):
        order.append(other)
    order = [p for p in order if _available(p)] or order[:1]   # skip a provider with no key

    errors, truncated = [], False
    for i, provider in enumerate(order):
        try:
            out = _CALLERS[provider](task, system, blocks, max_tokens)
            _count(provider)
            if i > 0:
                _count("fallbacks")
                log.warning("[%s] answered by fallback provider %s", task, provider)
            return out
        except LLMError as e:
            truncated = truncated or isinstance(e, TruncatedError)
            errors.append(f"{provider}: {e}")
            log.warning("[%s] %s failed: %s", task, provider, e)
    err = TruncatedError if truncated else LLMError
    raise err(f"[{task}] all providers failed — " + " | ".join(errors))
