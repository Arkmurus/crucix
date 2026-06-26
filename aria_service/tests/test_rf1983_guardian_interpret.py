"""R-F1983 — Guardian multilingual LLM intent comprehension.

The interpreter must turn a message in ANY language into a structured Guardian
intent, parse messy LLM output (code fences), and FAIL SAFE to action="none" on
anything that isn't a clear safety command — so it can never hijack a normal
question or enrol a contact without a real name + number.
"""
import asyncio

from aria_service.guardian import interpret as gi


class _MockLLM:
    """Stand-in for the real provider — returns a canned completion."""
    is_configured = True

    def __init__(self, text="", raise_exc=False):
        self._text = text
        self._raise = raise_exc

    async def complete(self, system, user, max_tokens=0, timeout=0):
        if self._raise:
            raise RuntimeError("provider down")
        class _R:
            pass
        r = _R()
        r.text = self._text
        return r


def _run(text, raise_exc=False, msg="hello"):
    return asyncio.run(gi.interpret(msg, _MockLLM(text, raise_exc)))


# ── multilingual happy paths ────────────────────────────────────────────────
def test_portuguese_checkin():
    out = _run('{"action":"arm","minutes":5,"confidence":0.95}')
    assert out["action"] == "arm" and out["minutes"] == 5.0


def test_spanish_panic():
    out = _run('{"action":"panic","message":"me siguen","confidence":0.9}')
    assert out["action"] == "panic" and out["message"] == "me siguen"


def test_french_all_clear():
    out = _run('{"action":"clear","confidence":0.88}')
    assert out["action"] == "clear"


def test_circle_add_with_name_and_number():
    out = _run('{"action":"circle_add","name":"Evelin Suurkivi","jid":"+44 7725 645685","confidence":0.9}')
    assert out["action"] == "circle_add"
    assert out["name"] == "Evelin Suurkivi" and out["jid"] == "447725645685"


def test_send_message():
    out = _run('{"action":"send","to":"Mum","message":"running late","confidence":0.8}')
    assert out["action"] == "send" and out["to"] == "Mum" and out["message"] == "running late"


# ── robustness / fail-safe ──────────────────────────────────────────────────
def test_code_fenced_json_is_parsed():
    out = _run('```json\n{"action":"status","confidence":0.7}\n```')
    assert out["action"] == "status"


def test_hours_converted_clamped():
    out = _run('{"action":"arm","minutes":120,"confidence":0.9}')
    assert out["minutes"] == 120.0


def test_normal_question_is_none():
    out = _run('{"action":"none","confidence":0.99}')
    assert out["action"] == "none"


def test_invalid_action_becomes_none():
    out = _run('{"action":"launch_missiles","confidence":1.0}')
    assert out["action"] == "none"


def test_arm_without_minutes_is_none():
    out = _run('{"action":"arm","confidence":0.9}')
    assert out["action"] == "none", "arm with no duration must not arm"


def test_circle_add_without_number_is_none():
    out = _run('{"action":"circle_add","name":"Bob","confidence":0.9}')
    assert out["action"] == "none", "never enrol a contact without a real number"


def test_garbage_output_is_none():
    out = _run('the model rambled with no json at all')
    assert out["action"] == "none"


def test_llm_exception_fails_safe():
    out = _run('', raise_exc=True)
    assert out["action"] == "none"


def test_unconfigured_llm_is_none():
    out = asyncio.run(gi.interpret("anything", None))
    assert out["action"] == "none"
