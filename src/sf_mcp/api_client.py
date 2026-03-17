from __future__ import annotations

from typing import Any, Iterable

import httpx

from .config import Settings


class OnboardApiError(RuntimeError):
    pass


class InsufficientCreditsError(OnboardApiError):
    """Raised when a 402 insufficient_credits response is returned by the API."""

    def __init__(self, message: str, available: int = 0, required: int = 0):
        super().__init__(message)
        self.available = available
        self.required = required


class OnboardApiClient:
    _shared_http: httpx.Client | None = None
    _shared_base_url: str | None = None

    def __init__(self, settings: Settings):
        self._settings = settings
        if (
            OnboardApiClient._shared_http is None
            or OnboardApiClient._shared_base_url != settings.onboard_api_base_url
        ):
            timeout = httpx.Timeout(
                connect=min(settings.request_timeout_seconds, 5.0),
                read=settings.request_timeout_seconds,
                write=min(settings.request_timeout_seconds, 10.0),
                pool=min(settings.request_timeout_seconds, 5.0),
            )
            limits = httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            )
            OnboardApiClient._shared_http = httpx.Client(
                base_url=settings.onboard_api_base_url,
                timeout=timeout,
                limits=limits,
            )
            OnboardApiClient._shared_base_url = settings.onboard_api_base_url

        self._http = OnboardApiClient._shared_http

    def close(self) -> None:
        return

    def _build_headers(
        self,
        client_id: str | None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        token = (client_id or self._settings.default_client_id or "").strip()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers:
            for key, value in extra_headers.items():
                normalized_key = str(key or "").strip()
                if not normalized_key:
                    continue
                headers[normalized_key] = str(value)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        client_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | Iterable[tuple[str, Any]] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        endpoint = path if path.startswith("/") else f"/{path}"
        headers = self._build_headers(client_id, extra_headers)
        try:
            response = self._http.request(
                method=method.upper(),
                url=endpoint,
                headers=headers,
                params=params,
                json=json,
            )
        except httpx.HTTPError as exc:
            raise OnboardApiError(f"Request failed for {method.upper()} {endpoint}: {exc}") from exc

        if response.status_code >= 400:
            detail: Any
            try:
                detail = response.json()
            except ValueError:
                detail = response.text

            if response.status_code == 402:
                detail_json = detail if isinstance(detail, dict) else {}
                if detail_json.get("error") == "insufficient_credits":
                    raise InsufficientCreditsError(
                        detail_json.get("message", "Insufficient credits"),
                        required=detail_json.get("required", 0),
                    )

            raise OnboardApiError(
                f"{method.upper()} {endpoint} returned {response.status_code}: {detail}"
            )

        if not response.content:
            return {"ok": True}

        content_type = (response.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            return response.json()
        return {"text": response.text}

    def get(
        self,
        path: str,
        *,
        client_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | Iterable[tuple[str, Any]] | None = None,
    ) -> Any:
        return self.request(
            "GET",
            path,
            client_id=client_id,
            extra_headers=extra_headers,
            params=params,
        )

    def post(
        self,
        path: str,
        *,
        client_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | Iterable[tuple[str, Any]] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        return self.request(
            "POST",
            path,
            client_id=client_id,
            extra_headers=extra_headers,
            params=params,
            json=json,
        )

    def put(
        self,
        path: str,
        *,
        client_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | Iterable[tuple[str, Any]] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        return self.request(
            "PUT",
            path,
            client_id=client_id,
            extra_headers=extra_headers,
            params=params,
            json=json,
        )

    def delete(
        self,
        path: str,
        *,
        client_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
        params: dict[str, Any] | Iterable[tuple[str, Any]] | None = None,
    ) -> Any:
        return self.request(
            "DELETE",
            path,
            client_id=client_id,
            extra_headers=extra_headers,
            params=params,
        )
