"""Kiểm tra envelope encryption cho credential (§21).

Test quan trọng nhất ở đây là ``test_ciphertext_khong_dung_duoc_o_tenant_khac``:
nó chứng minh rằng ciphertext bị sao chép sang tenant khác sẽ không giải mã
được. Đó là lớp phòng thủ theo chiều sâu cho §19 ở tầng mã hoá.
"""

from __future__ import annotations

import pytest

from app.core.crypto import (
    CredentialCipher,
    CredentialDecryptionError,
    EncryptedPayload,
    LocalMasterKeyProvider,
    build_aad,
)


@pytest.fixture
def cipher() -> CredentialCipher:
    return CredentialCipher(LocalMasterKeyProvider("khoa-thu-nghiem"))


def test_ma_hoa_roi_giai_ma_tra_lai_dung_plaintext(cipher: CredentialCipher) -> None:
    aad = build_aad(tenant_id="org_a", connection_id="conn_1")
    secret = b'{"access_token": "xoxb-khong-phai-that"}'

    payload = cipher.encrypt(secret, aad=aad)
    assert cipher.decrypt(payload, aad=aad) == secret


def test_ciphertext_khong_chua_plaintext(cipher: CredentialCipher) -> None:
    secret = b"xoxb-gia-tri-nhay-cam"
    payload = cipher.encrypt(secret, aad=build_aad(tenant_id="org_a", connection_id="conn_1"))
    assert secret not in payload.ciphertext


def test_moi_lan_ma_hoa_dung_mot_data_key_khac_nhau(cipher: CredentialCipher) -> None:
    aad = build_aad(tenant_id="org_a", connection_id="conn_1")
    a = cipher.encrypt(b"cung-mot-gia-tri", aad=aad)
    b = cipher.encrypt(b"cung-mot-gia-tri", aad=aad)

    assert a.ciphertext != b.ciphertext
    assert a.wrapped_data_key != b.wrapped_data_key


def test_ciphertext_khong_dung_duoc_o_tenant_khac(cipher: CredentialCipher) -> None:
    payload = cipher.encrypt(b"bi-mat", aad=build_aad(tenant_id="org_a", connection_id="conn_1"))

    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(payload, aad=build_aad(tenant_id="org_b", connection_id="conn_1"))


def test_ciphertext_khong_dung_duoc_o_connection_khac(cipher: CredentialCipher) -> None:
    payload = cipher.encrypt(b"bi-mat", aad=build_aad(tenant_id="org_a", connection_id="conn_1"))

    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(payload, aad=build_aad(tenant_id="org_a", connection_id="conn_2"))


def test_master_key_khac_thi_khong_giai_ma_duoc(cipher: CredentialCipher) -> None:
    aad = build_aad(tenant_id="org_a", connection_id="conn_1")
    payload = cipher.encrypt(b"bi-mat", aad=aad)

    khac = CredentialCipher(LocalMasterKeyProvider("mot-khoa-hoan-toan-khac"))
    with pytest.raises(CredentialDecryptionError):
        khac.decrypt(payload, aad=aad)


def test_serialize_qua_lai_voi_dinh_dang_luu_tru(cipher: CredentialCipher) -> None:
    aad = build_aad(tenant_id="org_a", connection_id="conn_1")
    payload = cipher.encrypt(b"bi-mat", aad=aad)

    khoi_phuc = EncryptedPayload.from_storage(payload.to_storage())
    assert cipher.decrypt(khoi_phuc, aad=aad) == b"bi-mat"


def test_repr_khong_lo_noi_dung(cipher: CredentialCipher) -> None:
    payload = cipher.encrypt(b"bi-mat", aad=build_aad(tenant_id="org_a", connection_id="conn_1"))
    text = repr(payload)
    assert "ciphertext" not in text
    assert "wrapped_data_key" not in text
