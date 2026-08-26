import asyncio
import types

import pytest

import config
import therapist_core_v1 as core
from professional_turn_conversation_context import (
    ConversationTurn,
    ConversationTurnRole,
    ProfessionalConversationContext,
)


def run(awaitable):
    return asyncio.run(awaitable)


class FakeCompletions:
    def __init__(self, text="Принято."):
        self.text = text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=self.text))])


def test_one_call_uses_exact_model_current_message_and_trusted_context():
    completions = FakeCompletions("  Вижу важную развилку.  ")
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
    context = ProfessionalConversationContext(turns=(
        ConversationTurn(1, ConversationTurnRole.USER, "Раньше было иначе."),
        ConversationTurn(2, ConversationTurnRole.ASSISTANT, "Что изменилось?"),
    ))
    result = run(core.generate_therapist_core_v1(
        client=client,
        model="gpt-core-compatible",
        source_text="Сейчас я передумал.",
        conversation_context=context,
        risk_result={"level": "low", "categories": []},
        lang="ru",
        max_completion_tokens=1200,
    ))
    assert result == "Вижу важную развилку."
    assert len(completions.calls) == 1
    call = completions.calls[0]
    assert call["model"] == "gpt-core-compatible"
    assert call["messages"][-1] == {"role": "user", "content": "Сейчас я передумал."}
    assert {"role": "assistant", "content": "Что изменилось?"} in call["messages"]
    assert call["n"] == 1
    assert call["temperature"] == 0.55
    assert call["max_tokens"] == 300
    assert "extra_body" not in call


def test_gpt56_sol_uses_exact_proven_transport_kwargs_once():
    completions = FakeCompletions()
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
    result = run(core.generate_therapist_core_v1(
        client=client,
        model="gpt-5.6-sol",
        source_text="Сообщение",
        conversation_context=ProfessionalConversationContext(turns=()),
        risk_result={"level": "low"},
        lang="ru",
        max_completion_tokens=1200,
    ))
    assert result == "Принято."
    assert len(completions.calls) == 1
    call = completions.calls[0]
    assert call["model"] == "gpt-5.6-sol"
    assert call["n"] == 1
    assert call["extra_body"] == {"max_completion_tokens": 1200}
    assert "temperature" not in call
    assert "max_tokens" not in call


@pytest.mark.parametrize("model", ["", "   ", " model "])
def test_model_is_explicit_and_never_substituted(model):
    completions = FakeCompletions()
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
    with pytest.raises(ValueError):
        run(core.generate_therapist_core_v1(
            client=client,
            model=model,
            source_text="Сообщение",
            conversation_context=ProfessionalConversationContext(turns=()),
            risk_result={"level": "low"},
            lang="ru",
            max_completion_tokens=1200,
        ))
    assert completions.calls == []


@pytest.mark.parametrize("value", [0, -1, 8193, 1.5, True])
def test_completion_budget_is_positive_bounded_integer(value):
    completions = FakeCompletions()
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
    with pytest.raises(ValueError):
        run(core.generate_therapist_core_v1(
            client=client,
            model="gpt-5.6-sol",
            source_text="Сообщение",
            conversation_context=ProfessionalConversationContext(turns=()),
            risk_result={"level": "low"},
            lang="ru",
            max_completion_tokens=value,
        ))
    assert completions.calls == []


def test_completion_budget_config_default_and_bounds(monkeypatch):
    monkeypatch.delenv("THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS", raising=False)
    assert config._bounded_positive_int_env(
        "THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS", 1200, 8192) == 1200
    monkeypatch.setenv("THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS", "8192")
    assert config._bounded_positive_int_env(
        "THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS", 1200, 8192) == 8192
    for invalid in ("", "0", "8193", "1.5", "not-an-int"):
        monkeypatch.setenv("THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS", invalid)
        with pytest.raises(ValueError):
            config._bounded_positive_int_env(
                "THERAPIST_CORE_V1_MAX_COMPLETION_TOKENS", 1200, 8192)


def test_constitution_carries_evidence_correction_and_stronger_later_rules():
    text = " ".join(core.THERAPIST_CORE_V1_CONSTITUTION.split())
    assert "Model inference is not fact" in text
    assert "Current user evidence and corrections outrank" in text
    assert "Prior ASSISTANT text is continuity context, not evidence" in text
    assert "JUST_TALK" in text and "UNDERSTAND" in text and "ACTION" in text
    assert "STRONGER LATER" in text
    assert "Do not diagnose" in text
