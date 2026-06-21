# anthropic

The official Python SDK for accessing Anthropic's Claude family of large language models via the Anthropic API.

## What it's for
- Sending messages to Claude models through the Messages API for chat, summarization, extraction, and other text-generation tasks.
- Streaming responses incrementally for lower perceived latency in interactive applications.
- Using tool use (function calling) to let Claude invoke external functions and return structured results.
- Building multimodal prompts that combine text with images (and other supported content types).

## Gotchas
- Authentication requires an API key, typically supplied via the `ANTHROPIC_API_KEY` environment variable; the client will raise an error if no key is found.
- The modern interface is `client.messages.create(...)` (Messages API). Older `completions`-style usage is legacy/deprecated, and you must set `max_tokens` explicitly on requests—omitting it causes errors.
