import pytest
import httpx
from types import SimpleNamespace


def test_icon_button_uses_unicode_without_configuration():
    from app.telegram_icons import icon_button

    button = icon_button("studio", "Studio", callback_data="nav:studio")
    assert button == {"text": "✨ Studio", "callback_data": "nav:studio"}


def test_configured_icon_uses_custom_emoji_field_and_falls_back():
    from app.telegram_icons import (
        icon_button,
        set_icon_id,
        set_icons_enabled,
        unicode_fallback_markup,
    )

    set_icon_id("studio", "5368324170671202286")
    set_icons_enabled(True)
    button = icon_button("studio", "Studio", callback_data="nav:studio")
    assert button["text"] == "Studio"
    assert button["icon_custom_emoji_id"] == "5368324170671202286"

    fallback = unicode_fallback_markup({"inline_keyboard": [[button]]})
    assert fallback["inline_keyboard"][0][0] == {
        "text": "✨ Studio",
        "callback_data": "nav:studio",
    }


def test_icon_slot_validation_and_clear():
    from app.telegram_icons import clear_icon_id, get_icon_configuration, set_icon_id

    with pytest.raises(ValueError, match="Unknown"):
        set_icon_id("not-a-slot", "123")
    with pytest.raises(ValueError, match="printable"):
        set_icon_id("studio", "bad id")

    set_icon_id("studio", "123456")
    assert get_icon_configuration()[1]["studio"] == "123456"
    clear_icon_id("studio")
    assert "studio" not in get_icon_configuration()[1]


def test_all_icon_callbacks_fit_telegram_limit():
    from app.telegram import (
        _clip_studio_keyboard,
        _insights_keyboard,
        _keyboard,
        _operations_keyboard,
        _settings_keyboard,
        _studio_keyboard,
    )

    for keyboard in (
        _keyboard(),
        _studio_keyboard(),
        _insights_keyboard(),
        _operations_keyboard(),
        _settings_keyboard(),
        _clip_studio_keyboard(),
    ):
        for row in keyboard["inline_keyboard"]:
            for button in row:
                callback = button.get("callback_data")
                if callback:
                    assert len(callback.encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_telegram_http_400_retries_once_with_unicode(monkeypatch):
    from app.telegram import _keyboard, send_message
    from app.telegram_icons import set_icon_id, set_icons_enabled

    set_icon_id("studio", "5368324170671202286")
    set_icons_enabled(True)
    payloads = []

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.request = httpx.Request("POST", "https://api.telegram.org/test")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("bad icon", request=self.request, response=self)

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def post(self, _url, json):
            payloads.append(json)
            return Response(400 if len(payloads) == 1 else 200)

    monkeypatch.setattr(
        "app.telegram.get_settings",
        lambda: SimpleNamespace(telegram_bot_token="test-token"),
    )
    monkeypatch.setattr("app.telegram.httpx.AsyncClient", Client)
    await send_message(12345, "test", _keyboard())

    assert len(payloads) == 2
    assert any(
        "icon_custom_emoji_id" in button
        for row in payloads[0]["reply_markup"]["inline_keyboard"]
        for button in row
    )
    assert all(
        "icon_custom_emoji_id" not in button
        for row in payloads[1]["reply_markup"]["inline_keyboard"]
        for button in row
    )
