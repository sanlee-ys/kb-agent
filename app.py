"""Gradio chat UI for the KB agent.

Wraps KBAgent in a gr.ChatInterface so you can ask the knowledge base questions
in a browser instead of the CLI.

Run:
    uv run python app.py

Then open the printed http://127.0.0.1:7860 URL.
"""

from __future__ import annotations

import gradio as gr

from agent.agent import KBAgent


def respond(message: str, history: list[dict]) -> str:
    """Answer one chat turn for the Gradio ChatInterface.

    Gradio owns the conversation history (a list of {"role", "content"} dicts).
    We rebuild a fresh KBAgent from that history each turn and let it run its
    tool-use loop. The agent's prior *text* answers are replayed as context; the
    per-turn tool calls don't need to be.

    Args:
        message: The newest user message to answer.
        history: Prior turns as Gradio role/content dicts, used to seed the
            agent's conversation context.

    Returns:
        The agent's text answer for this turn.
    """
    agent = KBAgent()
    agent.messages = [
        {"role": turn["role"], "content": turn["content"]}
        for turn in history
        if turn.get("content")
    ]
    return agent.ask(message)


demo = gr.ChatInterface(
    fn=respond,
    title="📚 KB Agent",
    description=(
        "Ask about your projects and the libraries they use. "
        "Answers are grounded in the local knowledge base (RAG + tool use)."
    ),
    examples=[
        "What projects are tracked?",
        "What is the defense-news-classifier and which libraries does it use?",
        "What is pandas used for?",
    ],
)


if __name__ == "__main__":
    demo.launch()
