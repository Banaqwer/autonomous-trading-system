"""Optional LLM reasoning layer — composes causal trade hypotheses from the live
sensor snapshot (the 'understands reasoning' stage). Uses Claude if a key is set
(ANTHROPIC_API_KEY); otherwise the rule-based causal map in signals.py carries it.
"""
from __future__ import annotations
import os, json

def enabled():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

def hypotheses(snapshot: dict, max_ideas=5):
    if not enabled():
        return []
    try:
        import anthropic
    except ImportError:
        return []
    client = anthropic.Anthropic()
    sys = ("You are a macro/cross-asset trading analyst. Given a snapshot of live events, "
           "market signals and tradable instruments, propose causal trade hypotheses: an event "
           "or condition, the chain of cause and effect, the instrument it should move, and the "
           "direction. Be specific and skeptical. Return ONLY JSON: a list of objects with keys "
           "instrument, direction (LONG or SHORT), thesis, horizon_days (number), confidence (0..1).")
    try:
        msg = client.messages.create(
            model="claude-opus-5-5", max_tokens=1500,
            thinking={"type": "adaptive"}, output_config={"effort": "medium"},
            system=sys,
            messages=[{"role": "user", "content": json.dumps(snapshot)[:6000] +
                       f"\n\nReturn up to {max_ideas} hypotheses as JSON only."}])
        txt = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        s, e = txt.find("["), txt.rfind("]")
        ideas = json.loads(txt[s:e+1]) if s >= 0 else []
        out = []
        for i in ideas[:max_ideas]:
            if i.get("instrument") and i.get("direction") in ("LONG", "SHORT"):
                out.append({"source": "llm", "domain": "reasoning", "instrument": i["instrument"].upper(),
                            "direction": i["direction"], "thesis": i.get("thesis", ""),
                            "horizon_days": float(i.get("horizon_days", 5)), "confidence": float(i.get("confidence", 0.5))})
        return out
    except Exception:
        return []
