import json

import pytest


def test_audio_catalog_registers_selects_and_deletes_assets(tmp_path):
    from app.audio_catalog import (
        delete_audio_asset,
        get_default_audio_asset,
        list_audio_assets,
        register_audio_asset,
        set_default_audio_asset,
    )

    first = register_audio_asset(
        tmp_path,
        kind="bgm",
        original_name="calm.mp3",
        data=b"ID3" + b"a" * 256,
    )
    second = register_audio_asset(
        tmp_path,
        kind="bgm",
        original_name="energy.mp3",
        data=b"ID3" + b"b" * 256,
    )

    assert get_default_audio_asset(tmp_path, "bgm")["id"] == first["id"]
    set_default_audio_asset(tmp_path, "bgm", str(second["id"]))
    assert get_default_audio_asset(tmp_path, "bgm")["id"] == second["id"]
    assert len(list_audio_assets(tmp_path, "bgm")) == 2
    assert delete_audio_asset(tmp_path, str(second["id"]))
    assert get_default_audio_asset(tmp_path, "bgm")["id"] == first["id"]
    manifest = json.loads((tmp_path / "audio-catalog.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1


def test_audio_catalog_rejects_mismatched_or_oversized_files(tmp_path):
    from app.audio_catalog import register_audio_asset

    with pytest.raises(ValueError, match="does not match"):
        register_audio_asset(
            tmp_path,
            kind="bgm",
            original_name="not-audio.mp3",
            data=b"this is not mp3 data",
        )


def test_audio_catalog_rejects_path_like_original_name_but_stores_basename(tmp_path):
    from app.audio_catalog import register_audio_asset

    entry = register_audio_asset(
        tmp_path,
        kind="sfx",
        original_name="../transition.wav",
        data=b"RIFF" + (32).to_bytes(4, "little") + b"WAVE" + b"x" * 64,
    )
    assert entry["name"] == "transition.wav"
    assert "/" not in str(entry["filename"])
