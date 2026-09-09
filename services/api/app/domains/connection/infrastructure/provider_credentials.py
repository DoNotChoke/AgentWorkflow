"""Tra ``client_id`` / ``client_secret`` của app mình, đọc từ cấu hình."""

from __future__ import annotations

from app.core.config import Settings
from app.domains.connection.application.ports import ClientCredentials
from app.domains.connection.domain.providers import GOOGLE, NOTION, SLACK


def _pair(client_id: str, client_secret: str) -> ClientCredentials | None:
    """Thiếu một nửa cũng coi như chưa cấu hình.

    Một client_id không kèm secret sẽ đi được tới tận trang chấp thuận của
    provider rồi mới hỏng ở bước đổi token — sau khi người dùng đã bấm đồng ý.
    Chặn ngay từ đây thì UI nói được "provider chưa bật" trước khi họ rời trang.
    """
    if not client_id or not client_secret:
        return None
    return ClientCredentials(client_id=client_id, client_secret=client_secret)


def build_provider_credentials(settings: Settings) -> dict[str, ClientCredentials]:
    """Danh sách provider đã cấu hình đủ để kết nối được."""
    configured: dict[str, ClientCredentials] = {}

    candidates = {
        SLACK.id: _pair(
            settings.oauth_slack_client_id,
            settings.oauth_slack_client_secret.get_secret_value(),
        ),
        GOOGLE.id: _pair(
            settings.oauth_google_client_id,
            settings.oauth_google_client_secret.get_secret_value(),
        ),
        NOTION.id: _pair(
            settings.oauth_notion_client_id,
            settings.oauth_notion_client_secret.get_secret_value(),
        ),
    }
    for provider_id, credentials in candidates.items():
        if credentials is not None:
            configured[provider_id] = credentials
    return configured


class SettingsProviderCredentials:
    """Hiện thực ``ProviderCredentials``."""

    def __init__(self, credentials: dict[str, ClientCredentials]) -> None:
        self._credentials = credentials

    def get(self, provider_id: str) -> ClientCredentials | None:
        return self._credentials.get(provider_id)
