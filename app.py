"""Gradio chat UI for the KB agent, served behind FastAPI.

GET /health returns ``{"status": "ok"}`` and does not run the agent. Chat
stays at ``/``.

Run:
    uv run python app.py

Then open the printed http://127.0.0.1:7860 URL. Probe liveness with:

    curl http://127.0.0.1:7860/health
"""

from __future__ import annotations

import os

import gradio as gr
from fastapi import FastAPI

from agent.agent import KBAgent

DEFAULT_PORT = 7860


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


def health() -> dict:
    """Return the liveness payload.

    Does not construct a KBAgent and does not open ChromaDB.

    Returns:
        A dict with ``status`` set to ``ok``.
    """
    return {"status": "ok"}


def create_app() -> FastAPI:
    """Build the FastAPI wrapper with /health and the Gradio chat at /.

    Registers ``GET /health`` first so the Gradio mount at ``/`` cannot
    shadow the probe.

    Returns:
        The FastAPI app uvicorn serves.
    """
    api = FastAPI(title="KB Agent")
    api.add_api_route("/health", health, methods=["GET"])
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
    return gr.mount_gradio_app(api, demo, path="/")


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", DEFAULT_PORT)),
    )
