"""ThriveVas ("Thrivewave") SMS adapter. API facts are recorded in THRIVEWAVE.md."""
import httpx

from app.integrations.sms.base import (
    DlrEvent,
    SmsAuthError,
    SmsError,
    SmsRejected,
    SmsResult,
    SmsTransientError,
    SmsUncertain,
)

# DLR status codes from the provider docs: 0/1 failed, 2 pending (sent to telco, no report), 3 delivered.
_DLR = {0: "failed", 1: "failed", 2: "pending", 3: "delivered"}


class ThrivewaveProvider:
    def __init__(self, base_url: str, api_key: str, *, client: httpx.AsyncClient | None = None, timeout: float = 15.0) -> None:
        if not base_url or not api_key:
            raise SmsError("Thrivewave is not configured (THRIVEWAVE_BASE_URL / THRIVEWAVE_API_KEY)")
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._headers = {"x-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"}

    async def send(self, to: str, text: str, *, sender_id: str | None = None, callback_url: str | None = None) -> SmsResult:
        payload: dict[str, str] = {"message": text, "msisdn": to}
        if sender_id:
            payload["sender_id"] = sender_id
        if callback_url:
            payload["callback_url"] = callback_url
        try:
            r = await self._client.post("/v1/message/send", json=payload, headers=self._headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise SmsTransientError(f"could not connect: {type(exc).__name__}") from exc  # never reached the provider
        except httpx.HTTPError as exc:
            raise SmsUncertain(f"no response: {type(exc).__name__}") from exc  # may have been accepted
        body = self._json(r)
        status = int(body.get("status", r.status_code))
        message = str(body.get("message", ""))[:300]
        if r.status_code == 200 and status == 200:
            return SmsResult(provider_message_id=str(body.get("transaction_id", "")), status="queued", recipients=_recipients(message))
        if r.status_code in (401, 428) or status in (401, 428):
            raise SmsAuthError(f"provider rejected the API key ({r.status_code}): {message}")
        if r.status_code in (429, 503) or status in (429, 503):
            raise SmsTransientError(f"provider busy ({r.status_code}): {message}")
        if r.status_code >= 500:
            raise SmsUncertain(f"provider error {r.status_code}: {message}")
        raise SmsRejected(f"provider rejected the request ({r.status_code}): {message}")

    @staticmethod
    def _json(r: httpx.Response) -> dict:  # type: ignore[type-arg]
        try:
            data = r.json()
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    async def aclose(self) -> None:
        await self._client.aclose()


def _recipients(message: str) -> int:
    import re

    m = re.search(r"Total recipients (\d+)", message)
    return int(m.group(1)) if m else 1


def parse_dlr(payload: dict) -> DlrEvent:  # type: ignore[type-arg]
    """Parse a delivery-report callback body. Raises ValueError on malformed input."""
    try:
        code = int(payload["status"])
        return DlrEvent(
            msisdn=str(payload["msisdn"]), transaction_id=str(payload["transaction_id"]),
            status=_DLR.get(code, "failed"), detail=str(payload.get("status_description", ""))[:200],
            network=str(payload.get("network", ""))[:50],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("malformed delivery report") from exc
