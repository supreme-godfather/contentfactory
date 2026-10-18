from unittest.mock import Mock, patch


def test_sync_channel_analytics_persists_official_totals(monkeypatch):
    from app.config import get_settings
    from app.youtube_oauth import sync_channel_analytics
    from app.db import SessionLocal, AnalyticsSnapshot

    monkeypatch.setenv("YOUTUBE_ANALYTICS_DAYS", "30")
    get_settings.cache_clear()
    channel_response = Mock()
    channel_response.raise_for_status.return_value = None
    channel_response.json.return_value = {
        "items": [{"id": "UC123", "snippet": {"title": "AI Stories Island"}}]
    }
    analytics_response = Mock()
    analytics_response.raise_for_status.return_value = None
    analytics_response.json.return_value = {"rows": [[12, 3, 2, 4.5]]}
    with patch("app.youtube_oauth.get_valid_access_token", return_value="token"), patch(
        "httpx.Client.get", side_effect=[channel_response, analytics_response]
    ):
        result = sync_channel_analytics()
    assert result["channel_id"] == "UC123"
    with SessionLocal() as session:
        snapshot = session.query(AnalyticsSnapshot).filter_by(content_id="channel:UC123").one()
        assert snapshot.views == 12
        assert snapshot.watch_seconds == 270
