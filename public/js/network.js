/* ARIA Network (R-F2342) — opt-in presence directory + real-time 1:1 DM.
   Rides the existing Socket.IO gateway (server.mjs) + chat REST API.
   Self-contained; depends only on app.js (API, Auth) + the socket.io client. */
(function () {
  'use strict';

  // R-F2354 — resolve the signed-in user RELIABLY. `Auth` is a const global (NOT
  // window.Auth) and Auth.user is a property (not a method); the previous
  // `window.Auth && Auth.user()` silently short-circuited to {} → myId null →
  // boot() aborted before wiring ANY button. Read the cache directly.
  let me = (function () {
    try { if (typeof Auth !== 'undefined' && Auth.user && typeof Auth.user === 'object') return Auth.user; } catch (e) {}
    try { return JSON.parse(localStorage.getItem('crucix_user') || '{}'); } catch (e) { return {}; }
  })();
  let myId = me.id || me.userId || null;
  let myName = me.fullName || me.username || 'You';

  let socket = null;
  let members = [];              // opt-in directory (other users)
  let convos = [];               // conversation summaries
  const online = new Set();      // online AND visible user ids
  const userInfo = new Map();    // id -> { fullName, username, role, sector, ... }
  let activeId = null;           // current DM partner id
  let iAmVisible = false;
  let myAvatarUrl = me.avatarUrl || null;   // R-F2349 — my shared profile photo
  let view = 'members';
  let connected = false;
  let typingTimer = null, typingSent = false, peerTypingTimer = null;
  let activePeer = null;         // R-F2371 — current conversation partner (for bubble avatars + profile pane)
  let activeConversation = null; // R-F2732 — direct or group conversation summary
  let filterText = '';           // R-F2371 — left-list search query (lower-cased)
  let attachTimer = null;        // R-F2371 — attach "coming soon" hint timer

  // ---------- utilities ----------
  const $ = (id) => document.getElementById(id);
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function initials(name) {
    const p = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!p.length) return '·';
    if (p.length === 1) return p[0].slice(0, 2).toUpperCase();
    return (p[0][0] + p[p.length - 1][0]).toUpperCase();
  }
  function hueFor(id) {
    let h = 0; const s = String(id || '');
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return h;
  }
  function avatarStyle(id, isAria) {
    if (isAria) return '';
    const h = hueFor(id);
    return `background:linear-gradient(135deg,hsl(${h},68%,54%),hsl(${(h + 42) % 360},72%,44%));`;
  }
  // R-F2349 — a real profile photo (shared with the main profile) when present,
  // else the deterministic initials tile. ARIA always keeps her brand gradient.
  function avatarInner(user) {
    if (user.avatarUrl && !user.isAria) {
      return `<img class="av-img" src="${esc(user.avatarUrl)}" alt="" loading="lazy">`;
    }
    return esc(initials(user.fullName || user.username));
  }
  function avatar(user, { size = '', isOnline = false } = {}) {
    const aria = !!user.isAria;
    const cls = ['av', size, aria ? 'aria' : '', aria ? 'pulse' : '',
      isOnline ? 'on' : 'offl'].filter(Boolean).join(' ');
    const style = (user.avatarUrl && !aria) ? '' : avatarStyle(user.id, aria);
    return `<span class="${esc(cls)}" style="${esc(style)}">${avatarInner(user)}</span>`;
  }
  // Mutate an existing avatar node in place (keeps its id stable across renders).
  function paintAvatar(el, user, { size = '', isOnline = false } = {}) {
    if (!el) return;
    const aria = !!user.isAria;
    el.className = ['av', size, aria ? 'aria pulse' : '', isOnline ? 'on' : 'offl'].filter(Boolean).join(' ');
    el.style.cssText = (user.avatarUrl && !aria) ? '' : avatarStyle(user.id, aria);
    el.innerHTML = avatarInner(user);
  }
  function fmtTime(ts) {
    const d = new Date(ts); if (isNaN(d)) return '';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  function fmtDay(ts) {
    const d = new Date(ts); if (isNaN(d)) return '';
    const now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === now.toDateString()) return 'Today';
    if (d.toDateString() === y.toDateString()) return 'Yesterday';
    return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  }
  function relSeen(ts) {
    if (!ts) return 'offline';
    const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
    if (s < 90) return 'last seen just now';
    if (s < 3600) return `last seen ${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `last seen ${Math.floor(s / 3600)}h ago`;
    return `last seen ${Math.floor(s / 86400)}d ago`;
  }
  // R-F2371 — insert text (emoji) at the textarea caret, then refresh state.
  function insertAtCaret(el, text) {
    if (!el) return;
    const s = el.selectionStart != null ? el.selectionStart : el.value.length;
    const e = el.selectionEnd != null ? el.selectionEnd : el.value.length;
    el.value = el.value.slice(0, s) + text + el.value.slice(e);
    const pos = s + text.length; el.setSelectionRange(pos, pos);
    el.focus();
    el.dispatchEvent(new Event('input'));
  }

  const ARIA = { id: 'aria', isAria: true, fullName: 'ARIA', username: 'aria',
    role: 'Intelligence', sector: 'always on' };

  // R-F2371 — friendly labels for the sector codes stored on the profile.
  const SECTOR_LABELS = {
    defence_broker: 'Defence broker / dealer / agent',
    oem_export: 'OEM (defence manufacturer)',
    government_acquisition: 'Government / acquisition / intel',
    compliance_consultancy: 'Compliance / export-control consultancy',
    banking_insurance: 'Banking / insurance (defence accounts)',
    research: 'Research / academic / NGO',
    journalism: 'Defence journalism',
    other: 'Other',
  };
  function sectorLabel(v) { return SECTOR_LABELS[v] || v || ''; }

  // R-F2371 — small avatar for a message row (no presence ring).
  function miniAvatar(user) {
    const aria = !!user.isAria;
    const style = (user.avatarUrl && !aria) ? '' : avatarStyle(user.id, aria);
    return `<span class="av msgav${esc(aria ? ' aria' : '')}" style="${esc(style)}">${avatarInner(user)}</span>`;
  }

  // R-F2371 — does a roster entry match the current search query?
  function memMatch(u) {
    if (!filterText) return true;
    const hay = [u.fullName, u.username, u.jobTitle, u.role, u.companyName, u.companyCountry,
      sectorLabel(u.sector)].filter(Boolean).join(' ').toLowerCase();
    return hay.includes(filterText);
  }

  // ---------- data loading ----------
  async function loadDirectory() {
    try {
      const d = await API.get('/api/network/directory');
      if (!d) return;
      iAmVisible = !!d.visible;
      members = Array.isArray(d.members) ? d.members : [];
      online.clear();
      members.forEach(m => { userInfo.set(m.id, m); if (m.online) online.add(m.id); });
      renderSelf(); renderMembers(); updateCounts();
    } catch (e) { console.warn('[network] directory load failed', e); }
  }
  async function loadConversations() {
    try {
      const c = await API.get('/api/chat/conversations');
      convos = Array.isArray(c) ? c : [];
      convos.forEach(s => { if (!userInfo.has(s.userId)) userInfo.set(s.userId, s); });
      renderChats(); updateCounts();
    } catch (e) { console.warn('[network] conversations load failed', e); }
  }

  // ---------- rendering: left column ----------
  function renderSelf() {
    paintAvatar($('self-av'), { id: myId, fullName: myName, avatarUrl: myAvatarUrl }, { size: 'lg', isOnline: iAmVisible });
    $('self-name').textContent = myName;
    const st = $('self-status');
    st.textContent = iAmVisible ? 'Appearing online' : 'Presence off — you are hidden';
    st.className = 'st ' + (iAmVisible ? 'on' : 'off');
    const t = $('self-toggle');
    t.setAttribute('aria-checked', iAmVisible ? 'true' : 'false');
    renderEmptyState();
  }
  // R-F2353 — the welcome pane must REACT when presence is activated, so the
  // user gets clear confirmation that "Turn on my presence" worked.
  function renderEmptyState() {
    const msg = $('empty-msg'), cta = $('empty-cta'), h = document.querySelector('#net-empty h2');
    if (!msg || !cta) return;
    if (iAmVisible) {
      if (h) h.textContent = "You're live on the network";
      msg.innerHTML = "✅ <b>Presence on</b> — you're visible to other members. "
        + "Pick someone on the left to start a secure conversation, or message "
        + "<b>ARIA</b> to screen a company, check sanctions, or summarise intel.";
      cta.style.display = 'none';
    } else {
      if (h) h.textContent = 'Welcome to the ARIA network';
      msg.textContent = 'Turn on your presence to appear to other members, then pick '
        + 'someone to start a secure conversation. Share intel, documents, and context '
        + '— with the people building ARIA alongside you.';
      cta.style.display = '';
    }
  }

  function memberRow(m, isOnline) {
    const sub = m.isAria ? 'Ask ARIA anything'
      : [m.jobTitle || m.role, m.companyName || m.sector].filter(Boolean).join(' · ')
        || (isOnline ? 'online' : relSeen(m.lastSeenAt));
    return `<div class="row ${esc(isOnline ? '' : 'offl')} ${esc(activeId === m.id ? 'active' : '')}"
                 data-open="${esc(m.id)}" role="button" tabindex="0">
        ${avatar(m, { isOnline })}
        <div class="bd"><div class="nm">${esc(m.fullName || m.username)}</div>
        <div class="sub">${esc(sub)}</div></div></div>`;
  }
  function renderMembers() {
    const host = $('view-members');
    const on = members.filter(m => online.has(m.id) && memMatch(m));
    const off = members.filter(m => !online.has(m.id) && memMatch(m));
    let html = memMatch(ARIA) ? memberRow(ARIA, true) : ''; // ARIA pinned, always available
    if (on.length) html += `<div class="net-grouplbl">Online · ${esc(on.length)}</div>`
      + on.map(m => memberRow(m, true)).join('');
    if (off.length) html += `<div class="net-grouplbl">Offline · ${esc(off.length)}</div>`
      + off.map(m => memberRow(m, false)).join('');
    if (filterText && !on.length && !off.length && !memMatch(ARIA)) {
      host.innerHTML = `<div class="net-hollow">No one matches “<b>${esc(filterText)}</b>”.</div>`;
      return;
    }
    if (!members.length) {
      html += `<div class="net-hollow">${iAmVisible
        ? 'You are the first one here — invite your team to turn on their presence and the network fills in.'
        : '<b>Turn on your presence</b> to join the network and see other members who are online.'}</div>`;
    }
    host.innerHTML = html;
  }
  function renderChats() {
    const host = $('view-chats');
    if (!convos.length) { host.innerHTML = `<div class="net-hollow">No conversations yet.<br>Pick a member to start one.</div>`; return; }
    const shown = convos.filter(s => memMatch(s.type === 'group' ? { fullName: s.name } : (userInfo.get(s.userId) || s)));
    if (!shown.length) { host.innerHTML = `<div class="net-hollow">No conversations match “<b>${esc(filterText)}</b>”.</div>`; return; }
    host.innerHTML = shown.map(s => {
      const isGroup = s.type === 'group';
      const u = isGroup ? { fullName: s.name || 'Group chat' } : (userInfo.get(s.userId) || s);
      const isOnline = online.has(s.userId);
      const last = s.lastMessage || {};
      const mine = last.from === myId;
      const prev = (mine ? 'You: ' : '') + (last.text || '');
      const openId = isGroup ? s.conversationId : s.userId;
      return `<div class="row ${esc(activeId === openId ? 'active' : '')}" data-open="${esc(openId)}" role="button" tabindex="0">
        ${avatar({ id: openId, fullName: u.fullName || u.username, avatarUrl: u.avatarUrl }, { isOnline: isGroup ? false : isOnline })}
        <div class="bd"><div class="nm">${esc(u.fullName || u.username)}</div>
        <div class="sub">${esc(isGroup ? `${(s.members || []).length} members · ` : '')}${esc(prev.slice(0, 42))}</div></div>
        ${s.unread ? `<span class="unread">${esc(s.unread)}</span>` : `<span class="time">${esc(fmtTime(last.ts))}</span>`}
      </div>`;
    }).join('');
  }
  function updateCounts() {
    $('cnt-members').textContent = online.size ? `${online.size} online` : String(members.length);
    const unread = convos.reduce((n, s) => n + (s.unread || 0), 0);
    $('cnt-chats').textContent = unread ? `${unread} new` : String(convos.length);
  }

  // ---------- rendering: thread ----------
  function openConversation(id) {
    activeId = id;
    activeConversation = convos.find(c => c.conversationId === id || c.userId === id) || { type: 'direct', userId: id };
    document.getElementById('net-wrap').classList.add('showing-thread');
    $('net-empty').hidden = true; $('net-convo').hidden = false;
    const isGroup = activeConversation.type === 'group';
    const isAria = id === ARIA.id;
    const u = isGroup ? { id, fullName: activeConversation.name || 'Group chat' } : (isAria ? ARIA : (userInfo.get(id) || { id, fullName: 'Member' }));
    const isOnline = isAria ? true : online.has(id);   // ARIA is always on
    paintAvatar($('convo-av'), { id, fullName: u.fullName || u.username, isAria, avatarUrl: u.avatarUrl }, { size: 'sm', isOnline });
    $('convo-who').textContent = u.fullName || u.username || 'Member';
    if (isGroup) { $('convo-pres').textContent = `${(activeConversation.members || []).length} members`; $('convo-pres').className = 'pres on'; }
    else setConvoPresence(isOnline, u.lastSeenAt, isAria);
    // R-F2371 — remember the peer (bubble avatars) + populate the profile pane
    activePeer = Object.assign({ id }, u, { isAria });
    renderProfilePanel(activePeer, isOnline, isAria);
    document.getElementById('net-wrap').classList.toggle('has-profile', !isGroup);
    $('net-messages').innerHTML = '<div class="net-hollow">Loading…</div>';
    $('net-typing').textContent = '';
    $('net-input').placeholder = isAria
      ? 'Ask ARIA to screen a company, check sanctions, summarise intel…'
      : 'Write a message…';
    highlightActive();
    loadHistory(id);
    updateSendEnabled();
  }
  function setConvoPresence(isOnline, lastSeenAt, isAria) {
    const p = $('convo-pres');
    p.textContent = isAria ? 'online · your always-on analyst'
      : (isOnline ? 'online now' : relSeen(lastSeenAt));
    p.className = 'pres ' + ((isOnline || isAria) ? 'on' : 'off');
  }
  // R-F2371 — right-hand profile pane, populated live from the roster entry.
  function renderProfilePanel(u, isOnline, isAria) {
    if (!$('net-profile')) return;
    paintAvatar($('np-av'), { id: u.id, fullName: u.fullName || u.username, isAria, avatarUrl: u.avatarUrl },
      { size: 'lg', isOnline });
    $('np-name').textContent = u.fullName || u.username || 'Member';
    $('np-role').textContent = isAria ? 'Autonomous Research Intelligence Agent'
      : ([u.jobTitle || u.role, u.companyName].filter(Boolean).join(' · ') || 'ARIA network member');
    const pres = $('np-pres'), onNow = isOnline || isAria;
    pres.className = 'np-pres' + (onNow ? ' on' : '');
    $('np-pres-t').textContent = isAria ? 'Active now · always on'
      : (onNow ? 'Active now' : relSeen(u.lastSeenAt));
    // detail fields
    const fields = [];
    if (isAria) {
      fields.push(['Role', 'Intelligence analyst']);
      fields.push(['Availability', '24/7 autonomous']);
      fields.push(['Ask about', 'Sanctions · DD · adverse media · intel summaries']);
    } else {
      if (u.companyName) fields.push(['Company', u.companyName]);
      if (u.companyCountry) fields.push(['Country', u.companyCountry]);
      if (u.sector) fields.push(['Sector', sectorLabel(u.sector)]);
      if ((u.jobTitle || u.role)) fields.push(['Title', u.jobTitle || u.role]);
    }
    $('np-fields').innerHTML = fields.length
      ? fields.map(([k, v]) => `<div class="np-field"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join('')
      : `<div class="np-empty">This member hasn't added profile details yet.</div>`;
    // per-conversation notification preference (persisted locally)
    const nt = $('np-notif');
    if (nt) nt.setAttribute('aria-checked', localStorage.getItem('net_mute_' + u.id) === '1' ? 'false' : 'true');
  }
  async function loadHistory(id) {
    try {
      const isGroup = activeConversation?.type === 'group';
      const result = await API.get(isGroup ? '/api/chat/conversation/' + encodeURIComponent(id) : '/api/chat/messages/' + encodeURIComponent(id));
      const msgs = isGroup ? result?.messages : result;
      if (activeId !== id) return;
      renderMessages(Array.isArray(msgs) ? msgs : []);
      // mark read locally + notify peer
      const s = convos.find(c => c.userId === id || c.conversationId === id); if (s) s.unread = 0;
      renderChats(); updateCounts();
      if (socket && connected) socket.emit('mark_read', isGroup ? { conversationId: id } : { fromId: id });
    } catch (e) {
      $('net-messages').innerHTML = '<div class="net-hollow">Could not load this conversation.</div>';
    }
  }
  function bubbleHtml(m) {
    const mine = m.from === myId;
    // R-F2371 — double-tick read receipt on my messages (brightens once seen)
    const read = mine ? `<span class="rd${esc(m.read ? ' seen' : '')}"><i class="bi bi-check2-all"></i></span>` : '';
    // R-F2371 — incoming messages carry the peer's avatar (reference-style rows)
    const av = mine ? '' : miniAvatar(activePeer || { id: m.from, fullName: 'Member' });
    return `<div class="msg ${esc(mine ? 'me' : 'them')}">${esc(av)}` +
      `<div class="bubble ${esc(mine ? 'b-me' : 'b-them')}" data-mid="${esc(m.id || '')}">` +
      `${esc(m.text)}<span class="mt">${esc(fmtTime(m.ts))} ${read}</span></div></div>`;
  }
  function renderMessages(msgs) {
    const host = $('net-messages');
    host.classList.remove('typing');   // R-F2357 — fresh render shows the placeholder
    if (!msgs.length) {
      host.innerHTML = activeId === ARIA.id
        ? `<div class="net-hollow">👋 I'm <b>ARIA</b>. Ask me to screen a counterparty, check sanctions, or summarise intel — I'll run it and reply right here.</div>`
        : `<div class="net-hollow">No messages yet — say hello. 👋</div>`;
      return;
    }
    let html = '', lastDay = '';
    msgs.forEach(m => {
      const day = fmtDay(m.ts);
      if (day && day !== lastDay) { html += `<div class="day">${esc(day)}</div>`; lastDay = day; }
      html += bubbleHtml(m);
    });
    host.innerHTML = html;
    host.scrollTop = host.scrollHeight;
  }
  function appendMessage(m) {
    const host = $('net-messages');
    if (host.querySelector('.net-hollow')) host.innerHTML = '';
    if (m.id && host.querySelector(`[data-mid="${CSS.escape(String(m.id))}"]`)) return; // dedupe
    const nearBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 80;
    host.insertAdjacentHTML('beforeend', bubbleHtml(m));
    if (nearBottom) host.scrollTop = host.scrollHeight;
  }

  function highlightActive() {
    document.querySelectorAll('.net-scroll .row').forEach(r =>
      r.classList.toggle('active', r.getAttribute('data-open') === activeId));
  }

  // ---------- sending ----------
  function updateSendEnabled() {
    const txt = $('net-input').value.trim();
    $('net-send').disabled = !(connected && activeId && txt);
  }
  function sendMessage() {
    const input = $('net-input');
    const text = input.value.trim();
    if (!text || !activeId || !connected) return;
    const clientId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    const payload = activeConversation?.type === 'group' ? { conversationId: activeId, text, clientId } : { toId: activeId, text, clientId };
    socket.emit('send_message', payload, result => {
      if (!result?.ok) { input.value = text; $('net-typing').textContent = result?.error || 'Message was not sent.'; updateSendEnabled(); }
    });
    input.value = ''; input.style.height = 'auto';
    const _m = $('net-messages'); if (_m) _m.classList.remove('typing');  // R-F2357
    stopTyping();
    updateSendEnabled();
  }
  function onTyping() {
    updateSendEnabled();
    if (!activeId || !connected) return;
    if (!typingSent) { socket.emit('typing', { ...(activeConversation?.type === 'group' ? { conversationId: activeId } : { toId: activeId }), typing: true }); typingSent = true; }
    clearTimeout(typingTimer);
    typingTimer = setTimeout(stopTyping, 1800);
  }
  function stopTyping() {
    clearTimeout(typingTimer);
    if (typingSent && activeId && connected) socket.emit('typing', { ...(activeConversation?.type === 'group' ? { conversationId: activeId } : { toId: activeId }), typing: false });
    typingSent = false;
  }

  // ---------- socket wiring ----------
  // R-F2348 — the apex/proxy host (imaria.io) does NOT route /socket.io/ to the
  // gateway (it 308-strips the trailing slash → 404); only the app host serves
  // it. So when we're on a non-app host, connect the socket straight to the app
  // origin (already in the server's CORS allowlist). Same-origin on fly.dev/local.
  const SOCKET_URL = (/(^|\.)fly\.dev$/.test(location.hostname)
      || /^(localhost|127\.|\[?::1)/.test(location.hostname))
    ? undefined
    : 'https://aria-web.fly.dev';

  function connect() {
    if (typeof io !== 'function') { console.error('[network] socket.io client missing'); return; }
    socket = io(SOCKET_URL, { auth: { token: API.token() }, transports: ['websocket', 'polling'] });

    socket.on('connect', () => { connected = true; setConn(false); updateSendEnabled(); });
    socket.on('disconnect', () => { connected = false; setConn(true); updateSendEnabled(); });
    socket.on('connect_error', () => { connected = false; setConn(true); });

    socket.on('online_users', (ids) => {
      online.clear(); (ids || []).forEach(id => online.add(id));
      renderMembers(); renderChats(); updateCounts();
      if (activeId) setConvoPresence(online.has(activeId), (userInfo.get(activeId) || {}).lastSeenAt);
    });
    socket.on('presence', ({ userId, online: on }) => {
      if (userId === myId) return;
      if (on) online.add(userId); else online.delete(userId);
      renderMembers(); renderChats(); updateCounts();
      if (userId === activeId) setConvoPresence(on, (userInfo.get(userId) || {}).lastSeenAt);
    });
    socket.on('network_update', () => { loadDirectory(); });
    socket.on('new_message', (m) => {
      if (!m) return;
      const partner = m.from === myId ? m.to : m.from;
      const messageKey = m.conversationId?.startsWith('grp:') ? m.conversationId : partner;
      const summary = convos.find(c => c.conversationId === m.conversationId || c.userId === partner);
      if (messageKey === activeId) {
        appendMessage(m);
        if (summary) summary.lastMessage = m;   // keep preview fresh
        if (m.from !== myId && socket && connected) socket.emit('mark_read', m.conversationId?.startsWith('grp:') ? { conversationId: m.conversationId } : { fromId: partner });
      } else if (m.from !== myId) {
        if (summary) { summary.unread = (summary.unread || 0) + 1; summary.lastMessage = m; }
        else if (!m.conversationId?.startsWith('grp:')) convos.unshift({ userId: partner, conversationId: m.conversationId, type: 'direct', lastMessage: m, unread: 1 });
        else loadConversations();
        renderChats(); updateCounts();
      }
    });
    socket.on('messages_read', ({ conversationId }) => {
      if (conversationId && activeConversation?.conversationId === conversationId) loadHistory(activeId);
    });
    socket.on('conversation_created', () => loadConversations());
    socket.on('typing', ({ fromId, typing }) => {
      if (fromId !== activeId) return;
      const el = $('net-typing');
      clearTimeout(peerTypingTimer);
      if (typing) {
        const u = userInfo.get(fromId) || {};
        el.textContent = `${(u.fullName || u.username || 'They').split(' ')[0]} is typing…`;
        peerTypingTimer = setTimeout(() => { el.textContent = ''; }, 3000);
      } else el.textContent = '';
    });
  }
  function setConn(show) {
    const el = $('net-conn'); if (el) el.classList.toggle('show', !!show);
  }

  // ---------- visibility toggle ----------
  async function toggleVisibility() {
    const next = !iAmVisible;
    try {
      const r = await postJSON('/api/network/visibility', { visible: next });
      iAmVisible = r && typeof r.visible === 'boolean' ? r.visible : next;
    } catch (e) {
      iAmVisible = next; // optimistic; server emits network_update to reconcile
    }
    renderSelf();
    flashStatus(iAmVisible ? 'Presence on ✓' : 'Presence off');
    loadDirectory();
  }
  // Minimal POST helper (app.js API may not expose post()).
  async function postJSON(path, body) {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token() },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }

  // ---------- profile photo (R-F2349) ----------
  // The round self-avatar is the uploader. Resize to a 256² JPEG client-side
  // (keeps the payload tiny), POST it, then update every surface via the shared
  // avatarUrl + the localStorage cache the sidebar reads.
  function flashStatus(msg, isErr) {
    const st = $('self-status'); if (!st) return;
    st.textContent = msg;
    st.style.color = isErr ? 'var(--n-off)' : '';
    clearTimeout(flashStatus._t);
    flashStatus._t = setTimeout(() => { st.style.color = ''; renderSelf(); }, 2600);
  }
  function resizeSquare(file, size) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const s = Math.min(img.width, img.height);
        const sx = (img.width - s) / 2, sy = (img.height - s) / 2;
        const c = document.createElement('canvas'); c.width = size; c.height = size;
        c.getContext('2d').drawImage(img, sx, sy, s, s, 0, 0, size, size);
        resolve(c.toDataURL('image/jpeg', 0.85));
      };
      img.onerror = () => reject(new Error('decode'));
      const fr = new FileReader();
      fr.onload = () => { img.src = fr.result; };
      fr.onerror = () => reject(new Error('read'));
      fr.readAsDataURL(file);
    });
  }
  async function onPhotoChange() {
    const input = $('net-photo-input');
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    if (!/^image\/(png|jpe?g|webp)$/.test(file.type)) { flashStatus('Use a PNG, JPG or WebP', true); return; }
    flashStatus('Uploading…');
    try {
      const dataUrl = await resizeSquare(file, 256);
      const res = await fetch('/api/profile/photo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token() },
        body: JSON.stringify({ dataUrl }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { flashStatus(d.error || 'Upload failed', true); return; }
      myAvatarUrl = d.avatarUrl || null;
      // share it: update the cache the sidebar + other pages read
      try {
        const cu = JSON.parse(localStorage.getItem('crucix_user') || '{}');
        cu.avatarUrl = myAvatarUrl; cu.avatarUpdatedAt = new Date().toISOString();
        localStorage.setItem('crucix_user', JSON.stringify(cu));
        if (typeof Auth !== 'undefined') Auth.user = cu;
      } catch {}
      renderSelf();
      flashStatus('Photo updated ✓');
    } catch (e) { flashStatus('Could not process that image', true); }
  }

  // ---------- edit profile (R-F2356) ----------
  function openProfileModal() {
    const ov = $('pm-overlay'); if (!ov) return;
    $('pm-fullName').value      = me.fullName || '';
    $('pm-jobTitle').value      = me.jobTitle || '';
    $('pm-companyName').value   = me.companyName || '';
    $('pm-companyCountry').value = me.companyCountry || '';
    $('pm-sector').value        = me.sector || '';
    const msg = $('pm-msg'); msg.textContent = ''; msg.style.color = '';
    ov.classList.add('open');
    setTimeout(() => $('pm-fullName').focus(), 40);
  }
  function closeProfileModal() { const ov = $('pm-overlay'); if (ov) ov.classList.remove('open'); }
  async function saveProfile() {
    const btn = $('pm-save'), msg = $('pm-msg');
    const body = {
      fullName:       $('pm-fullName').value.trim(),
      jobTitle:       $('pm-jobTitle').value.trim(),
      companyName:    $('pm-companyName').value.trim(),
      companyCountry: $('pm-companyCountry').value.trim(),
      sector:         $('pm-sector').value,
    };
    if (!body.fullName) { msg.style.color = 'var(--n-off)'; msg.textContent = 'Display name is required.'; return; }
    btn.disabled = true; msg.style.color = ''; msg.textContent = 'Saving…';
    try {
      const res = await fetch('/api/auth/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API.token() },
        body: JSON.stringify(body),
      });
      const u = await res.json().catch(() => ({}));
      if (!res.ok) { msg.style.color = 'var(--n-off)'; msg.textContent = u.error || 'Could not save.'; btn.disabled = false; return; }
      // Guard a malformed 200 so we never clobber the cache with {} (review note).
      if (!u || !u.id) { msg.style.color = 'var(--n-off)'; msg.textContent = 'Could not save — try again.'; btn.disabled = false; return; }
      // Apply the authoritative response everywhere: self card, cache, sidebar.
      me = u; myName = u.fullName || u.username || 'You'; myAvatarUrl = u.avatarUrl || myAvatarUrl;
      try {
        localStorage.setItem('crucix_user', JSON.stringify(u));
        if (typeof Auth !== 'undefined') Auth.user = u;
      } catch (e) {}
      renderSelf();
      const nn = document.getElementById('nav-user-name'); if (nn) nn.textContent = myName;
      msg.style.color = 'var(--n-on)'; msg.textContent = 'Profile updated ✓';
      setTimeout(closeProfileModal, 750);
    } catch (e) {
      msg.style.color = 'var(--n-off)'; msg.textContent = 'Network error — please try again.';
    }
    btn.disabled = false;
  }

  // ---------- events ----------
  function wireDom() {
    // segmented control
    $('net-seg').addEventListener('click', (e) => {
      const b = e.target.closest('button[data-view]'); if (!b) return;
      view = b.dataset.view;
      $('net-seg').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      $('view-members').hidden = view !== 'members';
      $('view-chats').hidden = view !== 'chats';
    });
    // open a conversation (delegated, both lists)
    document.querySelector('.net-scroll').addEventListener('click', (e) => {
      const r = e.target.closest('[data-open]'); if (r) openConversation(r.getAttribute('data-open'));
    });
    document.querySelector('.net-scroll').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const r = e.target.closest('[data-open]'); if (r) { e.preventDefault(); openConversation(r.getAttribute('data-open')); }
    });
    // composer
    const input = $('net-input');
    input.addEventListener('input', () => {
      input.style.height = 'auto'; input.style.height = Math.min(120, input.scrollHeight) + 'px';
      // R-F2357 — collapse the "say hello" placeholder up as the user types
      const msgs = $('net-messages');
      if (msgs) msgs.classList.toggle('typing', input.value.trim().length > 0);
      onTyping();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    $('net-send').addEventListener('click', sendMessage);

    // R-F2732 — accessible multi-select group creation, persisted by the server.
    const groupOverlay = $('gm-overlay');
    const closeGroup = () => groupOverlay.classList.remove('open');
    $('net-new-group').addEventListener('click', () => {
      $('gm-name').value = ''; $('gm-msg').textContent = '';
      $('gm-members').innerHTML = members.map(m => `<label class="gm-member"><input type="checkbox" value="${esc(m.id)}"><span>${esc(m.fullName || m.username)}</span></label>`).join('') || '<div class="net-hollow">No available members.</div>';
      groupOverlay.classList.add('open'); setTimeout(() => $('gm-name').focus(), 30);
    });
    $('gm-close').addEventListener('click', closeGroup); $('gm-cancel').addEventListener('click', closeGroup);
    $('gm-create').addEventListener('click', async () => {
      const memberIds = [...$('gm-members').querySelectorAll('input:checked')].map(x => x.value);
      const name = $('gm-name').value.trim();
      if (!name || memberIds.length < 2) { $('gm-msg').textContent = 'Enter a name and select at least two people.'; return; }
      $('gm-create').disabled = true;
      try {
        await postJson('/api/chat/groups', { name, memberIds }); closeGroup(); await loadConversations();
        view = 'chats'; $('net-seg').querySelector('[data-view="chats"]').click();
      } catch (e) { $('gm-msg').textContent = 'Could not create the group. Check its members and try again.'; }
      $('gm-create').disabled = false;
    });

    // R-F2371 — left-list search filter
    const search = $('net-search-input');
    if (search) search.addEventListener('input', () => {
      filterText = search.value.trim().toLowerCase();
      renderMembers(); renderChats();
    });
    // R-F2371 — emoji palette (inserts at the caret)
    const emojiBtn = $('net-emoji-btn'), emojiBox = $('net-emoji');
    if (emojiBtn && emojiBox) {
      const EMOJI = ['👍', '🙏', '✅', '🔥', '👀', '⚠️', '📎', '📊', '🕵️', '🚩', '💡', '🤝',
        '🟢', '🟡', '🔴', '📈', '🧭', '✍️', '😊', '🎯', '⏱️', '🔒', '📄', '🌍'];
      emojiBox.innerHTML = EMOJI.map(e => `<button type="button" tabindex="-1" aria-label="${esc(e)}">${esc(e)}</button>`).join('');
      emojiBtn.addEventListener('click', (e) => { e.stopPropagation(); emojiBox.classList.toggle('open'); });
      emojiBox.addEventListener('click', (e) => {
        const b = e.target.closest('button'); if (!b) return;
        insertAtCaret($('net-input'), b.textContent);
        emojiBox.classList.remove('open');
      });
      document.addEventListener('click', (e) => {
        if (emojiBox.contains(e.target) || e.target === emojiBtn || emojiBtn.contains(e.target)) return;
        emojiBox.classList.remove('open');
      });
    }
    // R-F2371 — attach is on the roadmap: give honest feedback, never a dead control
    if ($('net-attach-btn')) $('net-attach-btn').addEventListener('click', () => {
      const t = $('net-typing'); if (!t) return;
      t.textContent = '📎 File sharing is coming soon — send text or ask ARIA to pull a document for now.';
      clearTimeout(attachTimer);
      attachTimer = setTimeout(() => { if (t.textContent.startsWith('📎')) t.textContent = ''; }, 3200);
    });
    // R-F2371 — info button toggles the profile drawer on ≤1200px
    if ($('net-info')) $('net-info').addEventListener('click', () =>
      document.getElementById('net-wrap').classList.toggle('profile-open'));
    // R-F2371 — per-conversation notification preference (persisted locally)
    if ($('np-notif')) $('np-notif').addEventListener('click', () => {
      if (!activePeer) return;
      const nt = $('np-notif'), on = nt.getAttribute('aria-checked') === 'true';
      nt.setAttribute('aria-checked', on ? 'false' : 'true');
      try { localStorage.setItem('net_mute_' + activePeer.id, on ? '1' : '0'); } catch (e) {}
    });

    // toggle + CTA + back
    $('self-toggle').addEventListener('click', toggleVisibility);
    // R-F2349 — click the round self-avatar to upload/change the profile photo.
    const photoInput = $('net-photo-input');
    const avEdit = $('self-av-edit');
    if (avEdit && photoInput) {
      avEdit.addEventListener('click', () => photoInput.click());
      avEdit.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); photoInput.click(); }
      });
      photoInput.addEventListener('change', onPhotoChange);
    }
    // R-F2356 — click the name (pencil) to edit profile; modal controls.
    const nameRow = $('self-name-row');
    if (nameRow) {
      nameRow.addEventListener('click', openProfileModal);
      nameRow.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openProfileModal(); }
      });
    }
    if ($('pm-close'))  $('pm-close').addEventListener('click', closeProfileModal);
    if ($('pm-cancel')) $('pm-cancel').addEventListener('click', closeProfileModal);
    if ($('pm-save'))   $('pm-save').addEventListener('click', saveProfile);
    if ($('pm-overlay')) $('pm-overlay').addEventListener('click', e => { if (e.target.id === 'pm-overlay') closeProfileModal(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeProfileModal(); });
    $('empty-cta').addEventListener('click', async () => {
      if (!iAmVisible) await toggleVisibility();
      // nudge to members view
      view = 'members';
      $('net-seg').querySelector('[data-view="members"]').click();
    });
    $('net-back').addEventListener('click', () => {
      const w = document.getElementById('net-wrap');
      w.classList.remove('showing-thread', 'has-profile', 'profile-open');   // R-F2371 — also drop the profile pane
      activeId = null; activePeer = null; activeConversation = null; highlightActive();
    });
  }

  // ---------- boot ----------
  async function boot() {
    // R-F2354 — if the cache wasn't ready, recover from /me before giving up
    // (never silently die → dead buttons). Only redirect if truly unauthenticated.
    if (!myId && typeof Auth !== 'undefined' && Auth.me) {
      try {
        const u = await Auth.me();
        if (u) { me = u; myId = u.id || u.userId || null; myName = u.fullName || u.username || 'You'; myAvatarUrl = u.avatarUrl || null; }
      } catch (e) {}
    }
    if (!myId) {
      console.error('[network] no authenticated user — redirecting to sign in');
      if (typeof Auth !== 'undefined' && Auth.requireAuth) Auth.requireAuth();
      else location.href = '/signin.html';
      return;
    }
    userInfo.set(ARIA.id, ARIA);   // ARIA renders by name in header / typing / previews
    renderSelf();
    // R-F2349 — refresh my photo from the authoritative /me (localStorage may be stale).
    if (typeof Auth !== 'undefined' && Auth.me) {
      Auth.me().then(u => { if (u) { myAvatarUrl = u.avatarUrl || null; renderSelf(); } }).catch(() => {});
    }
    wireDom();
    connect();
    loadDirectory();
    loadConversations();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
