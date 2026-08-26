"""Nơi cất credential của provider, đã mã hoá."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import CredentialCipher, EncryptedPayload, build_aad, build_cipher
from app.core.logging import get_logger
from app.domains.connection.domain.entities import OAuthTokens, parse_scopes
from app.domains.connection.infrastructure.models import ConnectionCredentialModel

log = get_logger(__name__)


def _serialize(tokens: OAuthTokens) -> bytes:
    return json.dumps(
        {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
            # Ngăn bởi dấu cách — quy ước nội bộ, xem `models.py`.
            "scopes": " ".join(sorted(tokens.scopes)),
            "token_type": tokens.token_type,
        },
        separators=(",", ":"),
    ).encode()


def _deserialize(raw: bytes) -> OAuthTokens:
    data: dict[str, Any] = json.loads(raw)
    expires_raw = data.get("expires_at")
    return OAuthTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=datetime.fromisoformat(expires_raw) if expires_raw else None,
        scopes=parse_scopes(data.get("scopes")),
        token_type=data.get("token_type", "Bearer"),
    )


class EncryptedCredentialStore:
    """Hiện thực ``CredentialStore`` bằng PostgreSQL + envelope encryption."""

    def __init__(self, session: AsyncSession, cipher: CredentialCipher) -> None:
        self._session = session
        self._cipher = cipher

    async def save(
        self, *, connection_id: str, tenant_id: str, tokens: OAuthTokens, at: datetime
    ) -> None:
        payload = self._cipher.encrypt(
            _serialize(tokens),
            # AAD buộc ciphertext vào đúng cặp (tenant, connection) của nó.
            aad=build_aad(tenant_id=tenant_id, connection_id=connection_id),
        )
        stored = payload.to_storage()

        # UPSERT: uỷ quyền lại một connection đã có phải ghi đè credential cũ,
        # không phải ném lỗi trùng khoá.
        statement = pg_insert(ConnectionCredentialModel).values(
            connection_id=connection_id,
            created_at=at,
            updated_at=at,
            **stored,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[ConnectionCredentialModel.connection_id],
                set_={**stored, "updated_at": at},
            )
        )

    async def load(self, *, connection_id: str, tenant_id: str) -> OAuthTokens | None:
        row = await self._session.scalar(
            select(ConnectionCredentialModel).where(
                ConnectionCredentialModel.connection_id == connection_id
            )
        )
        if row is None:
            return None

        plaintext = self._cipher.decrypt(
            EncryptedPayload.from_storage(
                {
                    "ciphertext": row.ciphertext,
                    "nonce": row.nonce,
                    "wrapped_data_key": row.wrapped_data_key,
                    "key_id": row.key_id,
                    "algorithm": row.algorithm,
                }
            ),
            aad=build_aad(tenant_id=tenant_id, connection_id=connection_id),
        )
        try:
            return _deserialize(plaintext)
        finally:
            del plaintext

    async def delete(self, connection_id: str) -> None:
        await self._session.execute(
            delete(ConnectionCredentialModel).where(
                ConnectionCredentialModel.connection_id == connection_id
            )
        )
        log.info("connection_credential_deleted", connection_id=connection_id)


def build_credential_store(session: AsyncSession, settings: Settings) -> EncryptedCredentialStore:
    """Dựng store từ cấu hình.

    Nhận ``Settings`` chứ không nhận sẵn một ``CredentialCipher``, để nơi gọi
    không phải import ``app.core.crypto``. Giữ được như vậy thì câu mở đầu của
    module này — "module duy nhất được phép import ``app.core.crypto``" — đúng
    theo nghĩa đen, chứ không chỉ đúng với các domain khác.
    """
    return EncryptedCredentialStore(session, build_cipher(settings))
