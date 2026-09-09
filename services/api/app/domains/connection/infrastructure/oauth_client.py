"""Client nói chuyện với endpoint OAuth của provider.

Hai trách nhiệm, và chỉ hai:

1. Chuẩn hoá lỗi của provider về error model §37, để phía trên biết lỗi nào
   đáng retry mà không phải hiểu quy ước riêng của từng provider.
2. Không để credential rò ra ngoài qua lỗi hay log. Thân của phản hồi token
   **chính là** nơi chứa token, nên nó không bao giờ được đính vào exception.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.errors import (
    PlatformTimeoutError,
    ProviderPermanentError,
    ProviderUnavailableError,
    RateLimitedError,
)
from app.core.logging import get_logger
from app.domains.connection.application.ports import ClientCredentials, ProviderAccount
from app.domains.connection.domain.entities import OAuthTokens, parse_scopes
from app.domains.connection.domain.providers import (
    ProviderDescriptor,
    get_provider,
    read_path,
)

log = get_logger(__name__)

#: Endpoint token của provider phải trả lời nhanh. Chờ lâu hơn thế thì request
#: của người dùng đã hỏng về mặt cảm nhận rồi, và Temporal retry đúng hơn là chờ.
_TIMEOUT_SECONDS = 15.0

#: Mã lỗi RFC 6749 §5.2 mà thử lại chắc chắn cũng không khá hơn.
_PERMANENT_OAUTH_ERRORS: frozenset[str] = frozenset(
    {
        "invalid_grant",
        "invalid_client",
        "invalid_request",
        "invalid_scope",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)

#: Lỗi mà thử lại có cơ hội thành công.
#:
#: Cần tập này vì Slack không dùng status code: nó trả HTTP 200 kèm
#: ``ok: false`` cho **mọi** loại lỗi, kể cả quá tải. Không tách ra thì một lần
#: Slack nghẽn sẽ bị đọc thành "credential hỏng vĩnh viễn", và kết nối bị đánh
#: dấu cần uỷ quyền lại dù chẳng có gì hỏng cả.
_TRANSIENT_OAUTH_ERRORS: frozenset[str] = frozenset(
    {
        "ratelimited",
        "rate_limited",
        "service_unavailable",
        "internal_error",
        "fatal_error",
        "temporarily_unavailable",
        "server_error",
    }
)

#: Trường trong phản hồi mang giá trị bí mật. Bị loại trước khi phần thân được
#: trả lên tầng trên để đọc danh tính tài khoản.
_SECRET_FIELDS: frozenset[str] = frozenset({"access_token", "refresh_token", "id_token"})


def _redact(body: dict[str, Any]) -> dict[str, Any]:
    """Bỏ mọi trường bí mật, kể cả bí mật lồng trong object con.

    Slack đặt một access token thứ hai ở ``authed_user.access_token``; trả
    nguyên khối lên trên sẽ đưa nó vào đường đi của log và trace.
    """
    cleaned: dict[str, Any] = {}
    for key, value in body.items():
        if key in _SECRET_FIELDS:
            continue
        cleaned[key] = _redact(value) if isinstance(value, dict) else value
    return cleaned


def _expires_at(body: dict[str, Any]) -> datetime | None:
    raw = body.get("expires_in")
    if raw is None:
        return None
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return None
    return datetime.now(tz=UTC) + timedelta(seconds=seconds)


class HttpxOAuthClient:
    """Hiện thực ``OAuthClient``."""

    def __init__(self, credentials: dict[str, ClientCredentials]) -> None:
        self._credentials = credentials

    # ─── Công khai ───────────────────────────────────────────────────────────

    async def exchange_code(
        self,
        *,
        provider: str,
        code: str,
        redirect_uri: str,
        code_verifier: str | None,
    ) -> tuple[OAuthTokens, dict[str, Any]]:
        descriptor = get_provider(provider)
        client = self._require_client(descriptor)

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier is not None:
            payload["code_verifier"] = code_verifier

        body = await self._post(
            descriptor,
            descriptor.token_url,
            self._token_request(descriptor, payload, client=client),
        )
        return self._to_tokens(descriptor, body), _redact(body)

    async def refresh(self, *, provider: str, refresh_token: str) -> OAuthTokens:
        descriptor = get_provider(provider)
        client = self._require_client(descriptor)

        body = await self._post(
            descriptor,
            descriptor.token_url,
            self._token_request(
                descriptor,
                {"grant_type": "refresh_token", "refresh_token": refresh_token},
                client=client,
            ),
        )
        return self._to_tokens(descriptor, body)

    async def fetch_account(self, *, provider: str, access_token: str) -> ProviderAccount:
        descriptor = get_provider(provider)
        if descriptor.identity_endpoint is None:
            return ProviderAccount(id=None, label=None)

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http:
                response = await http.get(
                    descriptor.identity_endpoint,
                    headers={"authorization": f"Bearer {access_token}"},
                )
        except httpx.TimeoutException:
            return ProviderAccount(id=None, label=None)
        except httpx.HTTPError as exc:
            log.warning(
                "connection_identity_lookup_failed",
                provider=descriptor.id,
                error_type=type(exc).__name__,
            )
            return ProviderAccount(id=None, label=None)

        if response.status_code >= 400:
            # Danh tính tài khoản chỉ để hiển thị. Kết nối vẫn dùng được khi
            # thiếu nó, nên đây không phải lý do để làm hỏng cả vòng uỷ quyền.
            log.warning(
                "connection_identity_lookup_rejected",
                provider=descriptor.id,
                status_code=response.status_code,
            )
            return ProviderAccount(id=None, label=None)

        body = self._parse_json(response)
        return ProviderAccount(
            id=read_path(body, descriptor.account_id_path),
            label=read_path(body, descriptor.account_label_path),
        )

    async def revoke(self, *, provider: str, tokens: OAuthTokens) -> None:
        """Báo cho provider thu hồi token. Nuốt mọi lỗi, có chủ đích.

        Thu hồi phía mình đã là thao tác quyết định: credential bị xoá khỏi
        database ngay sau lời gọi này. Nếu để một provider đang chết chặn được
        bước đó, kết quả là mình vẫn giữ token mà người dùng thì tưởng đã gỡ —
        tệ hơn hẳn việc token còn sống thêm ở phía provider.
        """
        descriptor = get_provider(provider)
        if descriptor.revoke_url is None:
            return

        # Bearer chỉ là mặc định, cho provider xác thực lời gọi thu hồi bằng
        # chính token bị thu hồi (Slack). Provider đòi Basic sẽ ghi đè header
        # này trong `_token_request` — danh tính client thắng.
        request = self._token_request(
            descriptor,
            {"token": tokens.access_token},
            client=self._credentials.get(descriptor.id),
            headers={"authorization": f"Bearer {tokens.access_token}"},
        )

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http:
                response = await http.post(descriptor.revoke_url, **request)
            if response.status_code >= 400:
                log.warning(
                    "connection_provider_revoke_rejected",
                    provider=descriptor.id,
                    status_code=response.status_code,
                )
        except httpx.HTTPError as exc:
            log.warning(
                "connection_provider_revoke_failed",
                provider=descriptor.id,
                error_type=type(exc).__name__,
            )

    # ─── Nội bộ ──────────────────────────────────────────────────────────────

    def _require_client(self, descriptor: ProviderDescriptor) -> ClientCredentials:
        client = self._credentials.get(descriptor.id)
        if client is None:
            raise ProviderPermanentError(
                f"{descriptor.label} chưa được cấu hình trên hệ thống này.",
                details={"provider": descriptor.id},
            )
        return client

    def _token_request(
        self,
        descriptor: ProviderDescriptor,
        payload: dict[str, str],
        *,
        client: ClientCredentials | None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Dựng kwargs httpx cho một lời gọi tới endpoint token hoặc thu hồi."""
        body = dict(payload)
        request_headers = {"accept": "application/json", **(headers or {})}

        if client is not None:
            if descriptor.client_auth == "basic":
                raw = f"{client.client_id}:{client.client_secret}".encode()
                request_headers["authorization"] = f"Basic {base64.b64encode(raw).decode()}"
            else:
                body["client_id"] = client.client_id
                body["client_secret"] = client.client_secret

        return {
            "headers": request_headers,
            "json" if descriptor.request_format == "json" else "data": body,
        }

    async def _post(
        self, descriptor: ProviderDescriptor, url: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http:
                response = await http.post(url, **request)
        except httpx.TimeoutException as exc:
            raise PlatformTimeoutError(
                f"{descriptor.label} không phản hồi kịp.",
                details={"provider": descriptor.id},
                cause=exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"Không kết nối được tới {descriptor.label}.",
                details={"provider": descriptor.id},
                cause=exc,
            ) from exc

        self._raise_for_status(descriptor, response)
        body = self._parse_json(response)
        self._raise_for_oauth_error(descriptor, body)
        return body

    def _raise_for_status(self, descriptor: ProviderDescriptor, response: httpx.Response) -> None:
        if response.status_code == 429:
            raise RateLimitedError(
                f"{descriptor.label} đang giới hạn tần suất.",
                details={"provider": descriptor.id},
                retry_after_seconds=_retry_after(response),
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"{descriptor.label} đang gặp sự cố.",
                details={"provider": descriptor.id, "status_code": response.status_code},
            )
        # 4xx khác được để lọt xuống: thân phản hồi thường mang mã lỗi OAuth cụ
        # thể hơn nhiều so với con số status, và `_raise_for_oauth_error` đọc nó.

    def _parse_json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            raise ProviderUnavailableError(
                "Provider trả về phản hồi không đọc được.",
                details={"status_code": response.status_code},
            ) from None
        return body if isinstance(body, dict) else {}

    def _raise_for_oauth_error(self, descriptor: ProviderDescriptor, body: dict[str, Any]) -> None:
        # Slack trả HTTP 200 kèm ``{"ok": false, "error": "..."}`` thay vì dùng
        # status code. Kiểm tra ``ok`` trước, vì với Slack thì status code không
        # nói lên điều gì.
        error = body.get("error")
        if body.get("ok") is False and not error:
            error = "unknown_error"
        if not error:
            return

        code = str(error)
        details = {"provider": descriptor.id, "provider_error": code}

        if code in _TRANSIENT_OAUTH_ERRORS:
            raise ProviderUnavailableError(
                f"{descriptor.label} đang quá tải hoặc gặp sự cố tạm thời.", details=details
            )

        # Mặc định của phần còn lại là "vĩnh viễn", kể cả với mã lỗi lạ.
        #
        # Đoán sai theo hướng này thì người dùng phải bấm kết nối lại một lần
        # thừa. Đoán sai theo hướng kia thì Activity quay vòng retry cho tới lúc
        # cạn lượt trên một credential không bao giờ còn dùng được — tốn quota
        # của provider và làm chậm mọi thứ đứng sau nó (§15).
        raise ProviderPermanentError(
            f"{descriptor.label} từ chối yêu cầu uỷ quyền.", details=details
        )

    def _to_tokens(self, descriptor: ProviderDescriptor, body: dict[str, Any]) -> OAuthTokens:
        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ProviderPermanentError(
                f"{descriptor.label} không trả về access token.",
                details={"provider": descriptor.id},
            )

        refresh_token = body.get("refresh_token")
        return OAuthTokens(
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            expires_at=_expires_at(body),
            scopes=parse_scopes(body.get("scope"), delimiter=descriptor.scope_delimiter),
            token_type=str(body.get("token_type") or "Bearer"),
        )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        # Retry-After cũng được phép là một mốc HTTP-date. Bỏ qua dạng đó thay
        # vì đoán bừa — phía trên đã có backoff mặc định.
        return None
