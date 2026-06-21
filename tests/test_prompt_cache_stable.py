"""SYSTEM_CORE must be byte-identical across builds and unaffected by dynamic data."""
from __future__ import annotations

import prompts


# A frozen snapshot of the expected core. If SYSTEM_CORE changes, this test
# fails loudly — which is the point: editing the cached prefix raises cost for
# everyone, so it must be a deliberate decision, not an accident.
_EXPECTED_FIRST_LINE = (
    "Ты — агент службы поддержки бренда NikaBet, работающего на платформе "
    "NowPlix (казино и ставки на спорт). Отвечай уверенно, кратко и "
    "доброжелательно, как живой оператор поддержки."
)


def test_system_core_is_stable_string():
    a = prompts.get_system_core()
    b = prompts.get_system_core()
    assert a == b
    assert isinstance(a, str)
    assert a.splitlines()[0] == _EXPECTED_FIRST_LINE


def test_system_core_unaffected_by_language_or_context():
    """Changing language / KB / context must not alter the cached prefix."""
    core_before = prompts.get_system_core()

    # Build messages for two very different requests; the system prefix up to the
    # KB separator must be byte-identical.
    session = {"user_context": {"id": "1", "full_name": "A", "email": "a@x.com",
                                "activation_status": "active"}}
    msgs_en = prompts.build_messages(session, kb_block="KB ONE", history=[],
                                     user_text="hi", resolved_lang="en")
    msgs_es = prompts.build_messages(session, kb_block="KB TWO", history=[],
                                     user_text="hola", resolved_lang="es")

    def prefix(system_content: str) -> str:
        return system_content.split("=== БАЗА ЗНАНИЙ", 1)[0]

    assert prefix(msgs_en[0]["content"]) == prefix(msgs_es[0]["content"])
    assert prefix(msgs_en[0]["content"]).rstrip("\n") == core_before


def test_kb_block_appended_after_stable_core():
    session = {"user_context": {}}
    msgs = prompts.build_messages(session, kb_block="SOME KB", history=[],
                                  user_text="q", resolved_lang="en")
    sys_msg = msgs[0]["content"]
    assert sys_msg.startswith(prompts.get_system_core())
    assert "SOME KB" in sys_msg


def test_no_kb_block_is_just_core():
    session = {"user_context": {}}
    msgs = prompts.build_messages(session, kb_block=None, history=[],
                                  user_text="q", resolved_lang="en")
    assert msgs[0]["content"] == prompts.get_system_core()


def test_dynamic_data_lives_in_user_message_not_system():
    session = {"user_context": {"id": "SECRET-ID", "full_name": "Jane",
                                "email": "j@x.com", "activation_status": "active"}}
    msgs = prompts.build_messages(session, kb_block="KB", history=[],
                                  user_text="my question", resolved_lang="es")
    assert "SECRET-ID" not in msgs[0]["content"]  # not in system
    last_user = msgs[-1]["content"]
    assert "SECRET-ID" in last_user
    assert "my question" in last_user
    assert "Spanish" in last_user  # fallback language name in Layer 3


def test_layer3_directive_tells_model_to_mirror_player_language():
    """Regression: the answer must follow the player's own language, not be

    strict-locked to the resolved/browser default. A Russian question must be
    able to get a Russian answer even when the session default is English.
    """
    session = {"user_context": {}}
    msgs = prompts.build_messages(session, kb_block=None, history=[],
                                  user_text="Привет, помогите с депозитом",
                                  resolved_lang="en")
    directive = msgs[-1]["content"]
    # Mentions determining/answering in the player's message language…
    assert "язык" in directive.lower()
    # …and only falls back to the resolved default ("English") when unclear.
    assert "English" in directive
    # It must NOT hard-force the default language regardless of the message.
    assert "отвечай строго на языке — English" not in directive
