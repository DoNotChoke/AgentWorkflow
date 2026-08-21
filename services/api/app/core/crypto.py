"""Envelope encryption cho credential của provider."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings

_KEY_BYTES = 32  # AES-256
_NONCE_BYTES = 12  # GCM's recommended


@dataclass(frozen=True, slots=True)
class EncryptedPayload:
    """Kết quả mã hóa, an toàn để lưu vào DB."""

    ciphertext: bytes
    nonce: bytes
    wrapped_data_key: bytes
    key_id: str
    algorithm: str = "AES-256-GCM"

    def to_storage(self) -> dict[str, str]:
        return {
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
            "nonce": base64.b64encode(self.nonce).decode(),
            "wrapped_data_key": base64.b64encode(self.wrapped_data_key).decode(),
            "key_id": self.key_id,
            "algorithm": self.algorithm,
        }

    @classmethod
    def from_storage(cls, data: dict[str, str]) -> EncryptedPayload:
        return cls(
            ciphertext=base64.b64decode(data["ciphertext"]),
            nonce=base64.b64decode(data["nonce"]),
            wrapped_data_key=base64.b64decode(data["wrapped_data_key"]),
            key_id=data["key_id"],
            algorithm=data.get("algorithm", "AES-256-GCM"),
        )

    def __repr__(self) -> str:
        # Không bao giờ để nội dung lọt vào log qua repr.
        return f"EncryptedPayload(key_id={self.key_id!r}, algorithm={self.algorithm!r})"


@runtime_checkable
class MasterKeyProvider(Protocol):
    """KMS's abstraction"""

    @property
    def key_id(self) -> str: ...

    def wrap(self, data_key: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes, key_id: str) -> bytes: ...


class LocalMasterKeyProvider:
    """Master key tĩnh, **chỉ dùng cho phát triển cục bộ**."""

    def __init__(self, master_key_material: str) -> None:
        self._key = hashlib.sha256(master_key_material.encode()).digest()
        self._key_id = "local/" + hashlib.sha256(self._key).hexdigest()[:16]

    @property
    def key_id(self) -> str:
        return self._key_id

    def wrap(self, data_key: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + AESGCM(self._key).encrypt(nonce, data_key, None)

    def unwrap(self, wrapped: bytes, key_id: str) -> bytes:
        if key_id != self._key_id:
            raise CredentialDecryptionError(
                f"Payload được mã hoá bằng khoá {key_id!r}, khoá hiện tại là {self._key_id!r}. "
                "Cần quy trình xoay/di trú khoá."
            )
        nonce, ciphertext = wrapped[:_NONCE_BYTES], wrapped[_NONCE_BYTES:]
        return AESGCM(self._key).decrypt(nonce, ciphertext, None)


class CredentialDecryptionError(RuntimeError):
    """Không giải mã được credential. Không bao giờ đính plaintext vào lỗi này."""


class CredentialCipher:
    """Mã hoá / giải mã credential bằng envelope encryption."""

    def __init__(self, provider: MasterKeyProvider) -> None:
        self._provider = provider

    def encrypt(self, plaintext: bytes, *, aad: bytes | None = None) -> EncryptedPayload:
        data_key = os.urandom(_KEY_BYTES)
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)
        return EncryptedPayload(
            ciphertext=ciphertext,
            nonce=nonce,
            wrapped_data_key=self._provider.wrap(data_key),
            key_id=self._provider.key_id,
        )

    def decrypt(self, payload: EncryptedPayload, *, aad: bytes | None = None) -> bytes:
        """Giải mã. Thời gian sống của plaintext phải ngắn nhất có thể"""
        try:
            data_key = self._provider.unwrap(payload.wrapped_data_key, payload.key_id)
            return AESGCM(data_key).decrypt(payload.nonce, payload.ciphertext, aad)
        except CredentialDecryptionError:
            raise
        except Exception as exc:  # noqa: BLE001 — không để chi tiết lỗi rò ra ngoài
            raise CredentialDecryptionError("Giải mã credential thất bại.") from exc


def build_aad(*, tenant_id: str, connection_id: str) -> bytes:
    """Ràng buộc ciphertext vào đúng tenant và connection của nó (§19)."""
    return f"tenant={tenant_id}|connection={connection_id}".encode()


def build_cipher(settings: Settings) -> CredentialCipher:
    if settings.kms_provider == "local":
        return CredentialCipher(
            LocalMasterKeyProvider(settings.kms_local_master_key.get_secret_value())
        )
    raise NotImplementedError(
        f"KMS provider {settings.kms_provider!r} chưa được hiện thực. "
        "Xem §21 — production bắt buộc dùng KMS hoặc Vault thật."
    )
