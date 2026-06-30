"""Capability/contract test for R-F2202 — chat-DD company-sharing parity.

A DD run via web CHAT must share to same-company colleagues like the /dd/orchestrate
button (R-F608). That needs the user's email to flow chat -> _execute_tool -> orchestrate_dd.
Before this fix the chat path passed user_id but NOT user_email, so chat DDs had no email
domain and were owner-only.

This asserts the wiring contract end-to-end at the seam points (the deep _execute_tool DD
branch is gated by semaphores/admission and isn't unit-drivable without booting the brain):
  1. ChatRequest carries user_email (so the Node-pinned email is parsed).
  2. _execute_tool accepts + defaults user_email and share_to_company (True, like the button).
  3. _launch_deep_dd_bg forwards user_email (background full-depth DD also shares).

Run: python -m pytest aria_service/tests/test_chat_dd_email_parity_rf2202.py -q
"""
import inspect

from aria_service.routes import aria as _aria


def test_chat_request_carries_user_email():
    req = _aria.ChatRequest(message="screen Acme", user_email="analyst@firma.de")
    assert req.user_email == "analyst@firma.de"
    # default empty (back-compat) when omitted
    assert _aria.ChatRequest(message="hi").user_email == ""


def test_execute_tool_forwards_email_and_sharing():
    sig = inspect.signature(_aria._execute_tool)
    assert "user_email" in sig.parameters, "chat DD must forward the user's email"
    assert "share_to_company" in sig.parameters
    # share defaults True so chat matches the button's company-visible behaviour
    assert sig.parameters["share_to_company"].default is True


def test_background_deep_dd_forwards_email():
    sig = inspect.signature(_aria._launch_deep_dd_bg)
    assert "user_email" in sig.parameters, "background full-depth chat DD must also carry the email"


if __name__ == "__main__":
    test_chat_request_carries_user_email()
    test_execute_tool_forwards_email_and_sharing()
    test_background_deep_dd_forwards_email()
    print("ALL PASS")
