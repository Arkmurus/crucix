/* ARIA Network (R-F2342) — opt-in presence directory + real-time 1:1 DM.
   Rides the existing Socket.IO gateway (server.mjs) + chat REST API.
   Self-contained; depends only on app.js (API, Auth) + the socket.io client. */
(function () {
  'use strict';

  const me = (window.Auth && Auth.user()) || {};
  const myId = me.id || me.userId || null;
  const myName = me.fullName || me.username || 'You';

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
    return `<span class="${cls}" style="${style}">${avatarInner(user)}</span>`;
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

  const ARIA = { id: 'aria', isAria: true, fullName: 'ARIA', username: 'aria',
    role: 'Intelligence', sector: 'always on' };

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
  }

  function memberRow(m, isOnline) {
    const sub = m.isAria ? 'Ask ARIA anything'
      : [m.jobTitle || m.role, m.companyName || m.sector].filter(Boolean).join(' · ')
        || (isOnline ? 'online' : relSeen(m.lastSeenAt));
    return `<div class="row ${isOnline ? '' : 'offl'} ${activeId === m.id ? 'active' : ''}"
                 data-open="${esc(m.id)}" role="button" tabindex="0">
        ${avatar(m, { isOnline })}
        <div class="bd"><div class="nm">${esc(m.fullName || m.username)}</div>
        <div class="sub">${esc(sub)}</div></div></div>`;
  }
  function renderMembers() {
    const host = $('view-members');
    const on = members.filter(m => online.has(m.id));
    const off = members.filter(m => !online.has(m.id));
    let html = memberRow(ARIA, true); // ARIA pinned, always available
    if (on.length) html += `<div class="net-grouplbl">Online · ${on.length}</div>`
      + on.map(m => memberRow(m, true)).join('');
    if (off.length) html += `<div class="net-grouplbl">Offline · ${off.length}</div>`
      + off.map(m => memberRow(m, false)).join('');
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
    host.innerHTML = convos.map(s => {
      const u = userInfo.get(s.userId) || s;
      const isOnline = online.has(s.userId);
      const last = s.lastMessage || {};
      const mine = last.from === myId;
      const prev = (mine ? 'You: ' : '') + (last.text || '');
      return `<div class="row ${activeId === s.userId ? 'active' : ''}" data-open="${esc(s.userId)}" role="button" tabindex="0">
        ${avatar({ id: s.userId, fullName: u.fullName || u.username, avatarUrl: u.avatarUrl }, { isOnline })}
        <div class="bd"><div class="nm">${esc(u.fullName || u.username)}</div>
        <div class="sub">${esc(prev.slice(0, 42))}</div></div>
        ${s.unread ? `<span class="unread">${s.unread}</span>` : `<span class="time">${fmtTime(last.ts)}</span>`}
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
    document.getElementById('net-wrap').classList.add('showing-thread');
    $('net-empty').hidden = true; $('net-convo').hidden = false;
    const isAria = id === ARIA.id;
    const u = isAria ? ARIA : (userInfo.get(id) || { id, fullName: 'Member' });
    const isOnline = isAria ? true : online.has(id);   // ARIA is always on
    paintAvatar($('convo-av'), { id, fullName: u.fullName || u.username, isAria, avatarUrl: u.avatarUrl }, { size: 'sm', isOnline });
    $('convo-who').textContent = u.fullName || u.username || 'Member';
    setConvoPresence(isOnline, u.lastSeenAt, isAria);
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
  async function loadHistory(id) {
    try {
      const msgs = await API.get('/api/chat/messages/' + encodeURIComponent(id));
      if (activeId !== id) return;
      renderMessages(Array.isArray(msgs) ? msgs : []);
      // mark read locally + notify peer
      const s = convos.find(c => c.userId === id); if (s) s.unread = 0;
      renderChats(); updateCounts();
      if (socket && connected) socket.emit('mark_read', { fromId: id });
    } catch (e) {
      $('net-messages').innerHTML = '<div class="net-hollow">Could not load this conversation.</div>';
    }
  }
  function bubbleHtml(m) {
    const mine = m.from === myId;
    const read = mine && m.read ? '<span class="rd">· Read</span>' : '';
    return `<div class="bubble ${mine ? 'b-me' : 'b-them'}" data-mid="${esc(m.id || '')}">
      ${esc(m.text)}<span class="mt">${fmtTime(m.ts)} ${read}</span></div>`;
  }
  function renderMessages(msgs) {
    const host = $('net-messages');
    if (!msgs.length) {
      host.innerHTML = activeId === ARIA.id
        ? `<div class="net-hollow">👋 I'm <b>ARIA</b>. Ask me to screen a counterparty, check sanctions, or summarise intel — I'll run it and reply right here.</div>`
        : `<div class="net-hollow">No messages yet — say hello. 👋</div>`;
      return;
    }
    let html = '', lastDay = '';
    msgs.forEach(m => {
      const day = fmtDay(m.ts);
      if (day && day !== lastDay) { html += `<div class="day">${day}</div>`; lastDay = day; }
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
    socket.emit('send_message', { toId: activeId, text });
    input.value = ''; input.style.height = 'auto';
    stopTyping();
    updateSendEnabled();
  }
  function onTyping() {
    updateSendEnabled();
    if (!activeId || !connected) return;
    if (!typingSent) { socket.emit('typing', { toId: activeId, typing: true }); typingSent = true; }
    clearTimeout(typingTimer);
    typingTimer = setTimeout(stopTyping, 1800);
  }
  function stopTyping() {
    clearTimeout(typingTimer);
    if (typingSent && activeId && connected) socket.emit('typing', { toId: activeId, typing: false });
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
      const summary = convos.find(c => c.userId === partner);
      if (partner === activeId) {
        appendMessage(m);
        if (summary) summary.lastMessage = m;   // keep preview fresh
        if (m.from !== myId && socket && connected) socket.emit('mark_read', { fromId: partner });
      } else if (m.from !== myId) {
        if (summary) { summary.unread = (summary.unread || 0) + 1; summary.lastMessage = m; }
        else convos.unshift({ userId: partner, lastMessage: m, unread: 1 });
        renderChats(); updateCounts();
      }
    });
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
        if (window.Auth) Auth.user = cu;
      } catch {}
      renderSelf();
      flashStatus('Photo updated ✓');
    } catch (e) { flashStatus('Could not process that image', true); }
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
      onTyping();
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    $('net-send').addEventListener('click', sendMessage);
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
    $('empty-cta').addEventListener('click', async () => {
      if (!iAmVisible) await toggleVisibility();
      // nudge to members view
      view = 'members';
      $('net-seg').querySelector('[data-view="members"]').click();
    });
    $('net-back').addEventListener('click', () => {
      document.getElementById('net-wrap').classList.remove('showing-thread');
      activeId = null; highlightActive();
    });
  }

  // ---------- boot ----------
  function boot() {
    if (!myId) { console.error('[network] no authenticated user'); return; }
    userInfo.set(ARIA.id, ARIA);   // ARIA renders by name in header / typing / previews
    renderSelf();
    // R-F2349 — refresh my photo from the authoritative /me (localStorage may be stale).
    if (window.Auth && Auth.me) {
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
