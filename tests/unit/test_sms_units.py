import httpx
import pytest

from app.integrations.sms.base import SmsAuthError, SmsRejected, SmsTransientError, SmsUncertain
from app.integrations.sms.phone import InvalidPhone, mask, normalize_ke, same_number
from app.integrations.sms.templates import TemplateError, fold_gsm7, render, segments
from app.integrations.sms.thrivewave import ThrivewaveProvider, parse_dlr

pytestmark = pytest.mark.asyncio
KEY = "test-key-not-real"


def provider(handler) -> ThrivewaveProvider:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(base_url="https://sms.example.test", transport=httpx.MockTransport(handler))
    return ThrivewaveProvider("https://sms.example.test", KEY, client=client)


# --- phone ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", ["0712345678", "+254712345678", "254712345678", "712 345 678", "0112345678", "+254 (11) 2345678"])
def test_normalize_ke(raw: str) -> None:
    assert normalize_ke(raw) in {"+254712345678", "+254112345678"}


@pytest.mark.parametrize("raw", ["", "abc", "0812345678", "+447911123456", "07123", "+2547123456789", "0212345678"])
def test_normalize_rejects(raw: str) -> None:
    with pytest.raises(InvalidPhone):
        normalize_ke(raw)


def test_mask_and_same_number() -> None:
    assert mask("+254712345678") == "+25471***78" and "345" not in mask("+254712345678")
    assert same_number("254712345678", "+254712345678") and same_number("0712345678", "+254712345678")
    assert not same_number("254712345679", "+254712345678") and not same_number("garbage", "+254712345678")


# --- templates (privacy policy) --------------------------------------------------------------------
def test_render_and_limits() -> None:
    t, text = render("otp", {"code": "482913", "minutes": "5"})
    assert t.urgent and text == "Your Lygo code is 482913. It expires in 5 minutes. Do not share it." and segments(text) == 1
    assert render("update_nudge", {"firm": "Muthoni & Co."})[1] == "Lygo: Muthoni & Co. has an update for you. Log in to view it."


@pytest.mark.parametrize(
    ("tid", "params"),
    [
        ("nope", {}),
        ("otp", {"code": "12"}),  # missing param
        ("otp", {"code": "482913", "minutes": "5", "extra": "x"}),  # extra param
        ("otp", {"code": "48291a", "minutes": "5"}),
        ("update_nudge", {"firm": "Firm\nYour permit was rejected because"}),  # free text / newline injection
        ("update_nudge", {"firm": "Case: John Doe v. Insurer, claim denied"}),  # case details cannot fit the allowed pattern
        ("update_nudge", {"firm": "日本語"}),
        ("update_nudge", {"firm": "x" * 31}),
    ],
)
def test_templates_reject_unsafe_input(tid: str, params: dict[str, str]) -> None:
    with pytest.raises(TemplateError):
        render(tid, params)


def test_gsm_folding_and_segments() -> None:
    assert fold_gsm7("Müller") == "Müller"  # ü is in GSM-7
    assert fold_gsm7("naïve – ok") == "naive ? ok" or fold_gsm7("naïve – ok").startswith("naive")
    assert segments("a" * 160) == 1 and segments("a" * 161) == 2 and segments("a" * 307) == 3 and segments("€" * 80) == 1


# --- provider ----------------------------------------------------------------------------------------
async def test_send_success_and_request_shape() -> None:
    seen: dict = {}  # type: ignore[type-arg]

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("x-api-key"), req.read()
        return httpx.Response(200, json={"message": "SMS successfully queued for sending. Total recipients 1", "status": 200, "transaction_id": 2295})

    r = await provider(handler).send("+254712345678", "hi", sender_id="LYGO", callback_url="https://api.test/dlr/abc")
    assert (r.provider_message_id, r.status, r.recipients) == ("2295", "queued", 1)
    assert seen["url"] == "https://sms.example.test/v1/message/send" and seen["key"] == KEY
    assert b'"sender_id":"LYGO"' in seen["body"].replace(b" ", b"") and b"callback_url" in seen["body"]


@pytest.mark.parametrize(
    ("status", "body", "exc"),
    [
        (401, {"status": 401, "message": "authorization failed"}, SmsAuthError),
        (200, {"status": 428, "message": "session expired"}, SmsAuthError),  # error carried in the body
        (428, {"status": 428, "message": "Invalid API Key or session expired"}, SmsAuthError),
        (429, {"status": 429, "message": "slow down"}, SmsTransientError),
        (503, {"status": 503, "message": "maintenance"}, SmsTransientError),
        (500, {"status": 500, "message": "boom"}, SmsUncertain),  # may have been processed
        (502, {}, SmsUncertain),
        (422, {"status": 422, "message": "missing message"}, SmsRejected),
        (400, {"status": 400, "message": "bad"}, SmsRejected),
    ],
)
async def test_error_mapping(status: int, body: dict, exc: type[Exception]) -> None:  # type: ignore[type-arg]
    with pytest.raises(exc) as info:
        await provider(lambda req: httpx.Response(status, json=body)).send("+254712345678", "x")
    assert KEY not in str(info.value)


async def test_network_failures_are_classified_by_whether_the_request_could_have_arrived() -> None:
    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    def slow(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=req)

    with pytest.raises(SmsTransientError):  # never connected -> safe to retry
        await provider(refuse).send("+254712345678", "x")
    with pytest.raises(SmsUncertain):  # sent but no answer -> must NOT be retried blindly
        await provider(slow).send("+254712345678", "x")


async def test_non_json_body_does_not_crash() -> None:
    with pytest.raises(SmsUncertain):
        await provider(lambda req: httpx.Response(502, text="<html>bad gateway</html>")).send("+254712345678", "x")


def test_parse_dlr() -> None:
    e = parse_dlr({"msisdn": "254710000000", "status": 3, "country": "ke", "network": "Safaricom", "status_description": "Delivered To Teminal", "transaction_id": 2295})
    assert (e.status, e.transaction_id, e.network) == ("delivered", "2295", "Safaricom")
    assert [parse_dlr({"msisdn": "1", "status": s, "transaction_id": 1}).status for s in (0, 1, 2, 9)] == ["failed", "failed", "pending", "failed"]
    for bad in ({}, {"msisdn": "1", "transaction_id": 1}, {"msisdn": "1", "status": "x", "transaction_id": 1}):
        with pytest.raises(ValueError):
            parse_dlr(bad)


def test_sms_provider_selection_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.integrations.sms import factory

    monkeypatch.setattr(settings, "thrivewave_base_url", "")
    monkeypatch.setattr(settings, "thrivewave_api_key", "")
    assert "console" in factory.describe() and type(factory.get_sms_provider()).__name__ == "ConsoleSmsProvider"
    monkeypatch.setattr(settings, "app_env", "staging")
    assert factory.get_sms_provider() is None and "disabled" in factory.describe()
    monkeypatch.setattr(settings, "thrivewave_base_url", "https://sms.example.test")
    monkeypatch.setattr(settings, "thrivewave_api_key", "k")
    factory._thrivewave.cache_clear()
    assert isinstance(factory.get_sms_provider(), ThrivewaveProvider) and "thrivewave" in factory.describe()
    factory._thrivewave.cache_clear()
