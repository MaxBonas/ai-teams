from __future__ import annotations


def model_requires_default_temperature(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith(("gpt-5", "o1", "o3", "o4", "codex"))


def build_openai_compatible_body(
    *,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    stream: bool = False,
) -> dict[str, object]:
    body: dict[str, object] = {
        "model": model,
        "messages": messages,
    }
    if not model_requires_default_temperature(model):
        body["temperature"] = temperature
    if stream:
        body["stream"] = True
    return body
