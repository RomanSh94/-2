import asyncio
import types

import pytest

import config
import therapist_core_v1 as core
from interaction_preference import detect_interaction_preference
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
        interaction_contract="NONE",
        max_completion_tokens=1200,
    ))
    assert result == "Вижу важную развилку."
    assert len(completions.calls) == 1
    call = completions.calls[0]
    assert call["model"] == "gpt-core-compatible"
    assert call["messages"][-1] == {"role": "user", "content": "Сейчас я передумал."}
    assert {"role": "assistant", "content": "Что изменилось?"} in call["messages"]
    assert any(
        message["role"] == "system"
        and message["content"].endswith(": NONE")
        for message in call["messages"])
    assert any(
        "Trusted current local-time metadata: NONE" in message["content"]
        for message in call["messages"])
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
        interaction_contract="NONE",
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
            interaction_contract="NONE",
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
            interaction_contract="NONE",
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
    assert "investigate the mechanism before techniques" in text
    assert "generic productivity or wellness advice" in text
    assert "never abruptly close or redirect" in text
    assert "Do not diagnose" in text


def test_exact_three_turn_understand_regression_builds_one_trusted_request():
    first_user = (
        "Даже если я разбиваю дело на совсем маленький шаг, сопротивление часто всё равно "
        "остаётся. Я понимаю, что нужно сделать, но как будто не могу перейти к самому действию.")
    first_assistant = (
        "Вспомните последний такой момент: что произошло буквально перед тем, как вы "
        "отвернулись от дела?")
    second_user = (
        "Например сегодня мне нужно было ответить одному человеку. Я несколько раз открывал "
        "переписку, примерно понимал, что хочу написать, но закрывал её со словами «чуть позже».")
    second_assistant = (
        "Если текст уже написан и остаётся нажать «отправить», сопротивление всё ещё было бы?")
    current = (
        "И такое «сейчас сделаю чуть позже» повторяется почти каждый день. Я хочу понять, "
        "почему я так делаю, а не просто получить совет разбить задачу на маленькие шаги.")
    contract = detect_interaction_preference(current, "ru")
    assert contract == "UNDERSTAND"

    context = ProfessionalConversationContext(turns=(
        ConversationTurn(1, ConversationTurnRole.USER, first_user),
        ConversationTurn(2, ConversationTurnRole.ASSISTANT, first_assistant),
        ConversationTurn(3, ConversationTurnRole.USER, second_user),
        ConversationTurn(4, ConversationTurnRole.ASSISTANT, second_assistant),
    ))
    completions = FakeCompletions(
        "Повторяемость и эпизод с перепиской уточняют механизм: остановка возникает у самого "
        "контакта. Что именно меняется внутри в момент перед отправкой?")
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))

    run(core.generate_therapist_core_v1(
        client=client,
        model="gpt-core-compatible",
        source_text=current,
        conversation_context=context,
        risk_result={"level": "low", "categories": []},
        lang="ru",
        interaction_contract=contract,
        max_completion_tokens=1200,
    ))

    assert len(completions.calls) == 1
    messages = completions.calls[0]["messages"]
    assert messages[-1] == {"role": "user", "content": current}
    assert any(message["content"].endswith(": UNDERSTAND") for message in messages)
    assert any("Trusted current local-time metadata: NONE" in message["content"]
               for message in messages)
    user_context = [message["content"] for message in messages[:-1]
                    if message["role"] == "user"]
    assert any("маленький шаг" in text and "не могу перейти" in text
               for text in user_context)
    assert any("открывал переписку" in text and "чуть позже" in text
               for text in user_context)


def test_prior_assistant_time_claim_is_not_trusted_and_candidate_is_rejected():
    context = ProfessionalConversationContext(turns=(
        ConversationTurn(1, ConversationTurnRole.USER, "Мне трудно начать."),
        ConversationTurn(2, ConversationTurnRole.ASSISTANT,
                         "Сейчас ночь, продолжим утром."),
    ))
    completions = FakeCompletions(
        "Сейчас ночь. Сон сейчас полезнее любого разговора. Продолжим утром.")
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))

    with pytest.raises(core.UnsupportedTimeOfDayClaim):
        run(core.generate_therapist_core_v1(
            client=client,
            model="gpt-core-compatible",
            source_text="Я хочу понять, почему откладываю.",
            conversation_context=context,
            risk_result={"level": "low"},
            lang="ru",
            interaction_contract="UNDERSTAND",
            max_completion_tokens=1200,
        ))
    assert len(completions.calls) == 1


def test_user_authored_current_time_allows_grounded_time_reference():
    completions = FakeCompletions("Раз у тебя сейчас ночь, можно учитывать усталость.")
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
    result = run(core.generate_therapist_core_v1(
        client=client,
        model="gpt-core-compatible",
        source_text="У меня сейчас ночь, и я очень устал.",
        conversation_context=ProfessionalConversationContext(turns=()),
        risk_result={"level": "low"},
        lang="ru",
        interaction_contract="NONE",
        max_completion_tokens=1200,
    ))
    assert result == "Раз у тебя сейчас ночь, можно учитывать усталость."
    assert len(completions.calls) == 1


def test_invalid_interaction_contract_fails_before_provider_call():
    completions = FakeCompletions()
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
    with pytest.raises(ValueError):
        run(core.generate_therapist_core_v1(
            client=client,
            model="gpt-core-compatible",
            source_text="Сообщение",
            conversation_context=ProfessionalConversationContext(turns=()),
            risk_result={"level": "low"},
            lang="ru",
            interaction_contract="ADVICE_REQUEST",
            max_completion_tokens=1200,
        ))
    assert completions.calls == []
