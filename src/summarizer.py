"""Ollama integration for summarizing Discord messages."""

import os
from typing import Any

from .lib import logger

MAX_CHARS = 8000  # Ollama context consideration
DEFAULT_MODEL = "llama3.2"
DEFAULT_HOST = "http://localhost:11434"


def _get_client() -> Any:
    """Create Ollama client with host from env."""
    try:
        from ollama import Client
    except ImportError:
        raise ImportError("ollama package required. Install with: pip install ollama")
    host = os.environ.get("OLLAMA_HOST", DEFAULT_HOST)
    return Client(host=host)


def summarize_messages(aggregated_text: str) -> str:
    """
    Send aggregated messages to Ollama and return summary.
    Truncates if text exceeds MAX_CHARS.
    On failure returns fallback message.
    """
    if not aggregated_text.strip():
        return "No new messages to summarize."

    text = aggregated_text
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[... truncated for length ...]"
        logger.warning("truncated messages for summarization", original_len=len(aggregated_text))

    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
    prompt = f"""Summarize these Discord messages concisely. Group by topic or channel if relevant.
Keep it readable and actionable. Use bullet points for key items.

Messages:
{text}"""

    try:
        client = _get_client()
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.message.content
        if not summary:
            return "Summary unavailable (empty response)."
        return summary.strip()
    except Exception as e:
        logger.exception("ollama summarization failed", error=str(e))
        return "Summary unavailable due to an error. Check logs."


def answer_question(context_text: str, question: str) -> str:
    """
    Answer a user question from provided Discord context.

    If context is empty, responds in a friendly conversational way instead of an error.
    """
    cleaned_question = question.strip()
    if not cleaned_question:
        return "Please provide a non-empty question."
    if not context_text.strip():
        # No channel context - respond naturally to greetings and casual messages
        model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        prompt = """You are a helpful Discord bot. The user sent you a message in a DM, but you don't have channel context to answer from. Respond in a friendly, natural way. Keep it short and conversational. If they're greeting you or making small talk, reply in kind. If they seem to be asking about channel updates, gently say you need them to run /update first or subscribe to channels in a server."""
        try:
            client = _get_client()
            response = client.chat(
                model=model,
                messages=[{"role": "user", "content": f"{prompt}\n\nUser message: {cleaned_question}"}],
            )
            answer = response.message.content
            if answer and answer.strip():
                return answer.strip()
        except Exception as e:
            logger.exception("ollama conversational reply failed", error=str(e))
        return "Hi! I don't have any channel messages to answer from right now. Use /update in a server to fetch updates, or /subscribe to pick channels first."

    text = context_text
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[... truncated for length ...]"
        logger.warning("truncated context for question answering", original_len=len(context_text))

    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
    prompt = f"""You are answering questions using Discord channel context.
Use only the provided context. If the answer is not in context, say so clearly.
Be concise and practical.

Question:
{cleaned_question}

Context:
{text}
"""

    try:
        client = _get_client()
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.message.content
        if not answer:
            return "Answer unavailable (empty response)."
        return answer.strip()
    except Exception as e:
        logger.exception("ollama question answering failed", error=str(e))
        return "Answer unavailable due to an error. Check logs."
