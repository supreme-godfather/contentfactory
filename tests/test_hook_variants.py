from app.scriptwriter import _parse_json_array, build_hook_variants


def test_parse_json_array_accepts_json():
    variants = _parse_json_array('["पहला", "दूसरा", "तीसरा"]')

    assert variants == ["पहला", "दूसरा", "तीसरा"]


def test_parse_json_array_falls_back_to_lines():
    variants = _parse_json_array("1. पहला विकल्प\n2. दूसरा विकल्प\n")

    assert variants == ["पहला विकल्प", "दूसरा विकल्प"]


def test_build_hook_variants_returns_clean_options(monkeypatch):
    from app import scriptwriter

    monkeypatch.setattr(
        scriptwriter,
        "_minimax_chat",
        lambda *_args, **_kwargs: '["पहला लाइन", "दूसरी लाइन", "तीसरी लाइन"]',
    )

    variants = build_hook_variants("स्क्रिप्ट शुरू होती है यहाँ से।", "बाइक", "key")

    assert variants == ["पहला लाइन", "दूसरी लाइन", "तीसरी लाइन"]


def test_build_hook_variants_survives_a_provider_failure(monkeypatch):
    from app import scriptwriter

    def boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(scriptwriter, "_minimax_chat", boom)

    assert build_hook_variants("स्क्रिप्ट", "बाइक", "key") == []


def test_build_hook_variants_needs_a_script():
    assert build_hook_variants("", "बाइक", "key") == []
    assert build_hook_variants("स्क्रिप्ट", "बाइक", "") == []
