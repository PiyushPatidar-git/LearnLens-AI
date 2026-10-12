"""Optional LLM wrapper. Every caller must handle None (no key / failure) with a deterministic fallback."""
import os, json, re
_client = None
def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
def _get():
    global _client
    if _client is None:
        import anthropic; _client = anthropic.Anthropic()
    return _client
def complete(system, user, max_tokens=900, image_b64=None):
    if not available(): return None
    try:
        content = [{"type": "text", "text": user}]
        if image_b64:
            content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}})
        r = _get().messages.create(model=os.environ.get("LLM_MODEL", "claude-sonnet-5-5"), max_tokens=max_tokens,
                                   system=system, messages=[{"role": "user", "content": content}])
        return "".join(b.text for b in r.content if b.type == "text")
    except Exception:
        return None
def complete_json(system, user, max_tokens=1200):
    t = complete(system + "\nReturn ONLY valid JSON.", user, max_tokens)
    if not t: return None
    try: return json.loads(re.sub(r"^```json|```$", "", t.strip(), flags=re.M).strip())
    except Exception: return None
