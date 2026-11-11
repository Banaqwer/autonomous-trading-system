"""QuantCopilot-style research agent.

A manual Claude tool-use loop (no beta dependencies) wired to the OpenQuant tool
registry. Mirrors the reverse-engineered agent: a system prompt defining the
quant-research role, a tool loop (pull data -> backtest -> grade -> iterate), and
Plan/Build modes. Defaults to claude-opus-5-5 with adaptive thinking.

Run:  ANTHROPIC_API_KEY=... python -m openquant.agent.agent "Is a 20/50 MA
      crossover on SPY worth trading? Grade it and run the prop-firm desk."

Without a key it prints setup instructions and exits cleanly.
"""
from __future__ import annotations

import json
import os
import sys

from .tools import TOOLS, dispatch

SYSTEM = """You are QuantCopilot, an autonomous quant-research agent.

You help a trader research, build, and rigorously validate systematic strategies
on real market data. You have tools to pull bars, backtest, grade a strategy
(Verdict + regime attribution + regime-aware Monte Carlo), sweep parameters, run
a prop-firm evaluation simulation, and execute Python.

Operating principles:
- Be skeptical. A backtest is a hypothesis, not a result. Always check sample
  adequacy, regime concentration, and statistical significance (the Verdict's
  bootstrap p-value) before calling an edge real.
- Prefer grade_strategy over a bare backtest when judging quality.
- When a user asks whether something is "worth trading", give a clear verdict
  with the numbers that drive it and the main risks.
- Think step by step; call tools to get evidence rather than guessing numbers.
"""

MODES = {
    "build": "Build mode: implement and test directly.",
    "plan": "Plan mode: research and propose a plan; do NOT change state, just recommend.",
}


def _text_blocks(content) -> str:
    return "\n".join(b.text for b in content if getattr(b, "type", None) == "text")


def run_agent(task: str, model: str = "claude-opus-5-5", mode: str = "build",
              max_turns: int = 12, effort: str = "high", verbose: bool = True) -> str:
    try:
        import anthropic
    except ImportError:
        return ("The `anthropic` package is not installed.\n"
                "  pip install anthropic\n"
                "Then set ANTHROPIC_API_KEY and re-run.")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return ("No Claude credentials found. Set ANTHROPIC_API_KEY (or run `ant auth login`).\n"
                "The engine and tools work without a key — only the LLM agent loop needs one.\n"
                f"Tools available to the agent: {[t['name'] for t in TOOLS]}")

    client = anthropic.Anthropic()
    system = SYSTEM + "\n" + MODES.get(mode, MODES["build"])
    messages = [{"role": "user", "content": task}]

    for turn in range(max_turns):
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            tools=TOOLS,
            messages=messages,
        )
        if resp.stop_reason == "refusal":
            return f"[refused] {getattr(resp, 'stop_details', None)}"

        messages.append({"role": "assistant", "content": resp.content})
        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if verbose:
            txt = _text_blocks(resp.content)
            if txt:
                print(f"\n── assistant ──\n{txt}")
            for tu in tool_uses:
                print(f"  → tool: {tu.name}({json.dumps(tu.input)[:120]})")

        if resp.stop_reason != "tool_use" or not tool_uses:
            return _text_blocks(resp.content)

        results = []
        for tu in tool_uses:
            out = dispatch(tu.name, tu.input)
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": json.dumps(out, default=str)})
        messages.append({"role": "user", "content": results})

    return "[reached max turns]"


def main() -> None:
    task = " ".join(sys.argv[1:]) or \
        "Grade a 20/50 MA crossover on SPY. Is it worth trading? Run the prop-firm desk too."
    mode = "plan" if os.environ.get("OQ_MODE") == "plan" else "build"
    print(run_agent(task, mode=mode))


if __name__ == "__main__":
    main()
