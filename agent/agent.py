"""The KB agent: a manual Anthropic tool-use loop over the KB tools.

KBAgent.ask() sends the conversation to claude-opus-4-8 with the tools defined in
tools.py. When the model asks to call a tool, we execute it, feed the result
back, and loop until the model produces a final answer. This is the "manual
agentic loop" (rather than the SDK tool runner) so the control flow is explicit
and easy to follow.

Run directly for a simple CLI chat:
    uv run python agent/agent.py
"""

from __future__ import annotations

from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Support both `python agent/agent.py` and `import agent.agent`.
try:
    from .tools import TOOLS, execute_tool
except ImportError:  # running as a script
    from tools import TOOLS, execute_tool

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL = "claude-opus-4-8"
MAX_TOOL_ITERATIONS = 10  # safety cap on the tool-use loop

SYSTEM_PROMPT = """You are a knowledge-base assistant for a developer's personal \
collection of projects, the libraries they use, and plain-language concept notes.

Answer questions using the search_kb and list_projects tools rather than your own \
prior knowledge about the user's projects. When you state a fact that came from \
the KB, mention the source file. If the tools return nothing relevant, say so \
plainly instead of guessing — do not invent project details.

Beyond answering questions, you can also act: when the user wants a defense-news \
snippet actually classified or labeled (not just described), call the \
classify_snippet tool, which routes to the defense-news-classifier service.

Tool results are JSON observations with a "status" field ("success", "warning", \
or "error"). On success, use the "payload" and cite the "source". On a "warning" \
or "error", read "next_actions" and follow it — retry as instructed, or stop and \
tell the user plainly when it says to. Never fabricate a result that a failed \
tool did not return."""


class KBAgent:
    """Stateful chat agent that retains conversation history across turns."""

    def __init__(self, model: str = MODEL, system: str = SYSTEM_PROMPT):
        load_dotenv(REPO_ROOT / ".env")
        self.client = anthropic.Anthropic()
        self.model = model
        self.system = system
        self.messages: list[dict] = []

    def ask(self, user_message: str) -> str:
        """Send a user message, run the tool-use loop, and return the answer.

        Appends the message to the running history, then repeatedly calls the
        model and executes any tools it requests until the model returns a final
        (non-tool-use) answer or the iteration cap is reached.

        Args:
            user_message: The user's question or instruction for this turn.

        Returns:
            The model's final text answer, or a notice if it exceeded the
            tool-call iteration cap without finishing.
        """
        self.messages.append({"role": "user", "content": user_message})

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=TOOLS,
                messages=self.messages,
            )

            # Always preserve the assistant turn (it holds any tool_use blocks).
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                # Model is done — return its text.
                return self._final_text(response)

            # Execute every tool the model requested and send results back.
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            self.messages.append({"role": "user", "content": tool_results})

        return "Stopped after too many tool calls without a final answer."

    @staticmethod
    def _final_text(response) -> str:
        """Extract the text from a final (non-tool-use) response.

        Args:
            response: The Anthropic Messages response whose content blocks to read.

        Returns:
            The concatenated text blocks, or a placeholder if the response
            carried no text content.
        """
        parts = [b.text for b in response.content if b.type == "text"]
        return "\n".join(parts).strip() or "(no answer)"


def main() -> None:
    """Run a minimal interactive CLI chat loop against a fresh KBAgent.

    Reads questions from stdin and prints answers until interrupted (Ctrl-C or
    EOF). Intended for quick manual testing of the agent.
    """
    agent = KBAgent()
    print("KB agent ready. Ask a question (Ctrl-C to quit).\n")
    try:
        while True:
            question = input("you> ").strip()
            if not question:
                continue
            print(f"\nkb> {agent.ask(question)}\n")
    except (KeyboardInterrupt, EOFError):
        print("\nbye")


if __name__ == "__main__":
    main()
