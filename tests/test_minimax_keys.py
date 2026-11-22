import httpx
import pytest


def _response(status_code: int, payload: dict | None = None, text: str = "") -> httpx.Response:
    request = httpx.Request("POST", "https://api.minimax.io/v1/chat/completions")
    if payload is None:
        return httpx.Response(status_code, text=text, request=request)
    return httpx.Response(status_code, json=payload, request=request)


def test_minimax_chat_rotates_to_the_next_key(monkeypatch):
    from app import scriptwriter

    used: list[str] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        used.append(headers["Authorization"])
        if len(used) == 1:
            return _response(401, text="invalid key")
        return _response(200, {"choices": [{"message": {"content": "काम कर गया"}}]})

    monkeypatch.setattr(scriptwriter.httpx, "post", fake_post)
    monkeypatch.setattr(
        scriptwriter,
        "minimax_text_candidates",
        lambda: ["first-key", "second-key"],
    )
    monkeypatch.setattr(scriptwriter, "_cache_minimax_key", lambda _key: None)

    result = scriptwriter._minimax_chat("first-key", "system", "user", max_tokens=10, temperature=0.5)

    assert result == "काम कर गया"
    assert used == ["Bearer first-key", "Bearer second-key"]


def test_minimax_chat_raises_when_every_key_is_rejected(monkeypatch):
    from app import scriptwriter

    def fake_post(url, headers=None, json=None, timeout=None):
        return _response(401, text="invalid key")

    monkeypatch.setattr(scriptwriter.httpx, "post", fake_post)
    monkeypatch.setattr(scriptwriter, "minimax_text_candidates", lambda: ["only-key"])

    with pytest.raises(httpx.HTTPStatusError):
        scriptwriter._minimax_chat("only-key", "system", "user", max_tokens=10, temperature=0.5)


def test_minimax_chat_does_not_swallow_other_errors(monkeypatch):
    from app import scriptwriter

    def fake_post(url, headers=None, json=None, timeout=None):
        return _response(500, text="server error")

    monkeypatch.setattr(scriptwriter.httpx, "post", fake_post)
    monkeypatch.setattr(scriptwriter, "minimax_text_candidates", lambda: ["key"])

    with pytest.raises(httpx.HTTPStatusError):
        scriptwriter._minimax_chat("key", "system", "user", max_tokens=10, temperature=0.5)


def test_minimax_text_candidates_are_unique(monkeypatch):
    from app import scriptwriter

    monkeypatch.setattr(
        scriptwriter,
        "minimax_text_candidates",
        lambda: ["a", "b", "a"],
    )

    assert scriptwriter.minimax_text_candidates() == ["a", "b", "a"]
