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
    """R-F1930: a secondary number must reply on ITS OWN socket, not the primary.

    R-F2801: the last assertion used to be the literal ``await _s.sendMessage(chatId``.
    R-F2459 replaced that raw send with ``_sendChunkWithRetry(target, msg,
    () => ({ sock, connected }))`` so a mid-send reconnect re-resolves the live
    socket instead of writing to a dead one. The R-F1930 contract is UNCHANGED —
    the reply still resolves the arriving socket out of AsyncLocalStorage — but
    the string moved. That was a source-spelling gate.

    Asserted as the contract instead: the ALS context is established per inbound
    batch, and the send path resolves its socket FROM that context rather than
    capturing the module-level `sock`.
    """
    assert "AsyncLocalStorage" in WA and "new AsyncLocalStorage()" in WA
    # the handler establishes the per-batch context
    assert "_waCtx.run({ sock, account }" in WA
    # the resolver reads the arriving socket out of that context, with the
    # primary as fallback (account=null means the global connection)
    assert "_waCtx.getStore()" in WA
    assert "function _resolveLiveSock()" in WA, (
        "R-F1930 regression: no resolver that maps the ALS context to a socket"
    )
    assert "(_ctx && _ctx.sock) || sock" in WA, (
        "R-F1930 regression: the reply no longer prefers the ARRIVING socket "
        "over the module-level primary — secondary numbers would reply on the "
        "wrong account"
    )
    # and the resolver's result is what sends actually use
    assert "_resolveLiveSock()" in WA


def test_async_callback_delivers_on_the_right_account_socket():
    """The async DD callback must land on the account that ASKED, not the primary.

    R-F2801: the last assertion was the literal ``await _dsock.sendMessage(chatId``.
    R-F2459 replaced the raw send with ``_sendChunkWithRetry(chatId, msg,
    _resolveDsock)`` — a RE-RESOLVING thunk, so a reconnect mid-delivery picks up
    the new socket instead of writing to a dead one. The contract is unchanged and
    in fact stronger; only the spelling moved. Asserted as the contract.
    """
    # the job map records which account the request came in on...
    assert "accountId:" in WA
    # ...and the callback resolves THAT account's socket, falling back to primary
    assert "_accounts.get(mapping.accountId)" in WA
    assert "const _resolveDsock = () =>" in WA, (
        "R-F1930 regression: no per-callback socket resolver"
    )
    assert "(_acct && _acct.sock) || sock" in WA, (
        "R-F1930 regression: callback no longer prefers the REQUESTING account's "
        "socket — a secondary number's DD result would be delivered on the wrong "
        "account"
    )
    # …and delivery goes through the re-resolving retry path, not a captured socket
    assert "_sendChunkWithRetry(chatId, { text: chunks[i] }, _resolveDsock)" in WA
