// R-F2946 — pure connection-watchdog decision logic, extracted so it is unit-testable
// without booting the listener (aria_wa_listener.mjs auto-connects to WhatsApp on import,
// so the resilience tests can only read source text — a real decision like this deserves
// a real test).
//
// WHY THIS EXISTS — the open-but-dead blind spot. The R-F1551 watchdog returned early
// whenever `isConnected` was true, so a Baileys socket sitting connection:'open' but
// silently dead — no 'close' event fired — was invisible. Live 2026-07-23 that produced a
// ~22-min window where the local heartbeat kept logging "connected=true" while the
// heard-message count stayed frozen at 13; only a late code-428 close finally flipped
// isConnected and let the OLD watchdog notice (measuring staleness from the connect time,
// it then reported "1349s without connection"). The fix decides from the last PROVEN sign
// of life (a real inbound WhatsApp event), not from the connection flag that lied.

export const WATCHDOG_DEFAULTS = {
  staleDisconnectMs: 5 * 60 * 1000,   // disconnected this long without reconnect → restart
  silentProbeMs:     4 * 60 * 1000,   // connected but no inbound activity → active keepalive probe
  silentRestartMs:  10 * 60 * 1000,   // connected but silent this long → treat as open-but-dead → restart
};

/**
 * Decide what the connection watchdog should do this tick. PURE — no side effects,
 * clock passed in as `now` so it is deterministic under test.
 *
 * @param {number} now  Date.now() at the tick.
 * @param {object} s
 * @param {boolean} s.isConnected          Baileys' current connection flag (can be a stale 'true').
 * @param {number}  s.lastConnectedTime    ms epoch of the last connection.update 'open' (0 = never).
 * @param {number}  s.lastInboundActivity  ms epoch of the last REAL inbound socket event (0 = none yet).
 * @param {number}  [s.staleDisconnectMs]  override threshold.
 * @param {number}  [s.silentProbeMs]      override threshold.
 * @param {number}  [s.silentRestartMs]    override threshold.
 * @returns {{action:'ok'|'probe'|'restart', reason:string, silentMs?:number, elapsedMs?:number}}
 */
export function watchdogAction(now, s) {
  const isConnected        = !!s.isConnected;
  const lastConnectedTime  = s.lastConnectedTime || 0;
  const lastInboundActivity = s.lastInboundActivity || 0;
  const staleDisconnectMs  = s.staleDisconnectMs ?? WATCHDOG_DEFAULTS.staleDisconnectMs;
  const silentProbeMs      = s.silentProbeMs     ?? WATCHDOG_DEFAULTS.silentProbeMs;
  const silentRestartMs    = s.silentRestartMs   ?? WATCHDOG_DEFAULTS.silentRestartMs;

  // Never connected yet — the listener is still starting; nothing to police.
  if (!lastConnectedTime) return { action: 'ok', reason: 'never-connected' };

  if (isConnected) {
    // Reference = the most recent PROVEN sign of life. A real inbound event beats the
    // connect time; the local heartbeat is deliberately NOT consulted — it is the thing
    // that lied "connected=true" throughout the dead window.
    const ref = lastInboundActivity || lastConnectedTime;
    const silent = now - ref;
    if (silent > silentRestartMs) return { action: 'restart', reason: 'silent-socket', silentMs: silent };
    if (silent > silentProbeMs)   return { action: 'probe',   reason: 'silent-probe',  silentMs: silent };
    return { action: 'ok', reason: 'fresh', silentMs: silent };
  }

  // Disconnected: the original R-F1551 rule — restart if it has stayed down too long.
  const elapsed = now - lastConnectedTime;
  if (elapsed > staleDisconnectMs) return { action: 'restart', reason: 'stale-disconnect', elapsedMs: elapsed };
  return { action: 'ok', reason: 'reconnecting', elapsedMs: elapsed };
}
