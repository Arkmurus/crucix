"""R-F1930 (C1) — secondary WhatsApp accounts must PROCESS inbound messages and
reply on their OWN number, not be dark.

Before this fix, the `messages.upsert` handler was bound only to the global
(primary) socket; `_createAccount`/`_reconnectAccount` wired creds.update +
connection.update but NO message handler — so a linked secondary number connected
but silently dropped every inbound message. The reply helpers (sendReply, the
async /callback) were also hardwired to the primary socket.

Fix: the pipeline is factored into onMessagesUpsert(sock, account, ev), bound on
every socket; {sock, account} ride in AsyncLocalStorage so sendReply answers on the
arriving socket; and the job map records accountId so the async /callback delivers
on the right socket too.

Source-pinned (the listener mjs starts a server + Baileys on import — same approach
as the R-F1909/1918/1927 WA guards).
"""
from __future__ import annotations

import pathlib

WA = (pathlib.Path(__file__).resolve().parents[2]
      / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(encoding="utf-8", errors="ignore")


def test_handler_is_factored_and_bound_on_every_socket():
    assert "async function onMessagesUpsert(sock, account, ev)" in WA
    # global/primary bind (account=null)
    assert "onMessagesUpsert(sock, null, ev)" in WA
    # secondary accounts: create + reconnect both bind it
    assert "onMessagesUpsert(sock, account, ev)" in WA          # _createAccount
    assert "onMessagesUpsert(account.sock, account, ev)" in WA  # _reconnectAccount


def test_reply_path_uses_arriving_socket_via_async_local_storage():
    assert "AsyncLocalStorage" in WA and "new AsyncLocalStorage()" in WA
    # the handler establishes the per-batch context
    assert "_waCtx.run({ sock, account }" in WA
    # sendReply resolves the socket from the context (falls back to primary)
    assert "_waCtx.getStore()" in WA
    assert "await _s.sendMessage(chatId" in WA


def test_async_callback_delivers_on_the_right_account_socket():
    # the job map records which account the request came in on...
    assert "accountId:" in WA
    # ...and /callback resolves that account's socket (fallback to primary)
    assert "_accounts.get(mapping.accountId)" in WA
    assert "await _dsock.sendMessage(chatId" in WA
