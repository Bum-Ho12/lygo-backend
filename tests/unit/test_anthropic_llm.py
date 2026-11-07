import json

import anthropic
import httpx2
import pytest

from app.core.config import settings
from app.engine.ai.guardrails.citations import Source
from app.engine.ai.providers.anthropic_llm import FALLBACK_BETA, MIN_OUTPUT_TOKENS, AnthropicLLM
from app.engine.ai.providers.base import ChatMessage, LlmError, LlmRefused, LlmUnavailable
from app.engine.ai.providers.factory import get_llm
from app.engine.ai.settings import AiSettings
from app.engine.orchestrator.generate import generate_verified

pytestmark = pytest.mark.asyncio
MSGS = [ChatMessage("system", "RULES"), ChatMessage("user", "q1"), ChatMessage("assistant", "a1"), ChatMessage("user", "q2")]


def message(text: str = "hello [S1]", stop: str = "end_turn", **extra):  # type: ignore[no-untyped-def]
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": text}],
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 11, "output_tokens": 7}, **extra}


def llm(handler, model: str = "claude-opus-5-5", **kw) -> AnthropicLLM:  # type: ignore[no-untyped-def]
    client = anthropic.AsyncAnthropic(api_key="sk-ant-test", max_retries=0, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    return AnthropicLLM("sk-ant-test", model, client=client, **kw)


async def test_request_shape_follows_the_claude_5_rules() -> None:
    seen: dict = {}  # type: ignore[type-arg]

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["path"], seen["headers"], seen["body"] = req.url.path, req.headers, json.loads(req.read())
        return httpx2.Response(200, json=message())

    r = await llm(handler, effort="medium").generate(MSGS, temperature=0.7, max_tokens=900)
    body = seen["body"]
    assert (r.text, r.model, r.prompt_tokens, r.completion_tokens, r.finish_reason) == ("hello [S1]", "claude-opus-5-5", 11, 7, "end_turn")
    assert seen["headers"]["authorization"] == "Bearer sk-ant-test" and seen["headers"]["anthropic-version"]  # SDK 2.x auth + API version
    assert body["system"] == "RULES" and [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]  # system split out of messages
    assert "temperature" not in body and "top_p" not in body and "top_k" not in body  # removed on Claude 5.x (would 400)
    assert "thinking" not in body  # Opus 5.5 always thinks; we never try to disable it
    assert body["output_config"] == {"effort": "medium"}
    assert body["max_tokens"] == MIN_OUTPUT_TOKENS  # 900 would let adaptive thinking eat the whole answer
    assert seen["path"].endswith("/v1/messages") and FALLBACK_BETA in seen["headers"]["anthropic-beta"] and body["fallbacks"] == "default"


async def test_models_without_server_side_fallback_do_not_send_it() -> None:
    seen: dict = {}  # type: ignore[type-arg]

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["body"], seen["headers"] = json.loads(req.read()), req.headers
        return httpx2.Response(200, json=message())

    await llm(handler, model="claude-haiku-5-5").generate(MSGS)  # Haiku 5.5: "no server-side fallback"
    assert "fallbacks" not in seen["body"] and "anthropic-beta" not in seen["headers"]
    await llm(handler, fallbacks=False).generate(MSGS)  # explicitly disabled
    assert "fallbacks" not in seen["body"]


async def test_large_ceiling_is_capped_and_leading_assistant_turns_are_dropped() -> None:
    seen: dict = {}  # type: ignore[type-arg]

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(req.read())
        return httpx2.Response(200, json=message())

    await llm(handler).generate([ChatMessage("system", "S"), ChatMessage("assistant", "orphan"), ChatMessage("user", "q")], max_tokens=500_000)
    assert seen["body"]["max_tokens"] == 16000 and seen["body"]["messages"] == [{"role": "user", "content": "q"}]
    with pytest.raises(LlmError):
        await llm(handler).generate([ChatMessage("system", "S")])  # nothing to ask


async def test_refusal_is_raised_not_read_as_content() -> None:
    with pytest.raises(LlmRefused) as e:
        await llm(lambda r: httpx2.Response(200, json=message("partial text", "refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None}))).generate(MSGS)
    assert e.value.category == "cyber"
    with pytest.raises(LlmRefused) as e2:
        await llm(lambda r: httpx2.Response(200, json=message("", "refusal"))).generate(MSGS)
    assert e2.value.category is None  # a refusal without a category is a valid state


async def test_truncated_and_non_text_content() -> None:
    r = await llm(lambda r: httpx2.Response(200, json=message("cut off mid", "max_tokens"))).generate(MSGS)
    assert r.finish_reason == "max_tokens"  # surfaced so callers can tell
    body = message()
    body["content"] = [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": "A"}, {"type": "text", "text": "B"}]
    assert (await llm(lambda r: httpx2.Response(200, json=body)).generate(MSGS)).text == "AB"  # thinking blocks never leak into the answer


@pytest.mark.parametrize(
    ("status", "exc", "key"),
    [(401, LlmError, "credentials"), (403, LlmError, "credentials"), (400, LlmError, "rejected the request"),
     (429, LlmUnavailable, "unavailable"), (500, LlmUnavailable, "unavailable"), (529, LlmUnavailable, "unavailable"), (503, LlmUnavailable, "unavailable")],
)
async def test_error_mapping_and_no_secret_leakage(status: int, exc: type[Exception], key: str) -> None:
    err = {"type": "error", "error": {"type": "api_error", "message": "boom sk-ant-test leaked?"}}
    with pytest.raises(exc) as info:
        await llm(lambda r: httpx2.Response(status, json=err)).generate(MSGS)
    assert key in str(info.value) and "sk-ant-test" not in str(info.value)
    assert (exc is LlmUnavailable) == isinstance(info.value, LlmUnavailable)  # auth/validation problems are not "outages"


async def test_connection_failures_are_unavailable() -> None:
    def boom(req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=req)

    with pytest.raises(LlmUnavailable):
        await llm(boom).generate(MSGS)


async def test_missing_key_and_factory_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(LlmError):
        AnthropicLLM("")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "")
    assert get_llm().__class__.__name__ == "ExtractiveLLM"  # local fallback
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-x")
    from app.engine.ai.providers import factory

    factory._anthropic.cache_clear()
    chosen = get_llm()
    assert isinstance(chosen, AnthropicLLM) and chosen.model == "claude-opus-5-5" and chosen.use_fallbacks
    factory._anthropic.cache_clear()


async def test_the_orchestrator_abstains_when_the_provider_declines() -> None:
    src = [Source(1, "c", "Act", "s.5", "A claimant shall give notice within sixty days of the loss.")]

    def declined(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message("x", "refusal", stop_details={"type": "refusal", "category": "bio"}))

    gen = await generate_verified(llm(declined), "research", "advocate", "notice?", src, AiSettings())
    assert gen.status == "abstained" and gen.abstain_reason == "model_refused:bio" and gen.text == ""
    ok = await generate_verified(llm(lambda r: httpx2.Response(200, json=message("Notice is due within sixty days of the loss [S1]."))), "research", "advocate", "notice?", src, AiSettings())
    assert ok.status == "ok" and ok.used == [1] and ok.model == "claude-opus-5-5"


async def test_effort_is_chosen_per_task_but_falls_back_to_the_default() -> None:
    efforts = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        efforts.append(json.loads(req.read())["output_config"]["effort"])
        return httpx2.Response(200, json=message("Notice is due within sixty days of the loss [S1]."))

    model = llm(handler, effort="medium")
    src = [Source(1, "c", "Act", "s.5", "A claimant shall give notice within sixty days of the loss.")]
    for task, audience in (("explain", "client"), ("research", "advocate"), ("summarise", "advocate"), ("unknown_task_xyz", "advocate")):
        try:
            await generate_verified(model, task, audience, "notice?", src, AiSettings())
        except KeyError:
            await model.generate(MSGS)  # an unregistered task has no prompt; check the provider default directly
    assert efforts[:3] == ["low", "medium", "low"] and efforts[3] == "medium"
