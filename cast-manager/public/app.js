// ═══════════════════════════════════════════════════════════════
// Cast Manager v3 - Frontend Application
// ═══════════════════════════════════════════════════════════════

// ─── State ──────────────────────────────────────────────────
const state = {
  currentSection: 'home',
  torrents: [],
  torrentFilter: 'all',
  torrentSearch: '',
  files: [],
  currentPath: '/home/REDACTED_USER/watch_list',
  fileFilter: 'all',
  fileSearch: '',
  fileSort: 'name',
  libraryView: 'list',
  queue: [],
  queueIndex: -1,
  repeatMode: 'off', // off, queue, one
  playlists: [],
  positions: {},
  history: [],
  casting: {
    state: 'idle',
    title: '',
    currentTime: 0,
    duration: 0,
    volume: 80,
    filePath: '',
  },
  castModalFile: null,
  settings: {
    autoTranscode: 'auto',
    autoAdvance: true,
    saveInterval: 30,
    defaultView: 'list',
  },
  pollingTimers: {},
  isDraggingScrubber: false,
};

// ─── Init ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadPersistedData();
  showSection('home');
  startPolling();
  setupKeyboardShortcuts();
  loadDiskInfo();
  renderContinueWatching();
  loadTorrents();
  checkInitialCastStatus();
});

// Check if something is already casting on page load
async function checkInitialCastStatus() {
  try {
    const info = await api('/api/cast/status');
    if (info.state && info.state !== 'idle') {
      state.casting.state = info.state;
      state.casting.currentTime = info.currentTime || 0;
      state.casting.duration = info.duration || 0;
      if (info.title) state.casting.title = info.title;
      if (info.volumeLevel !== undefined) state.casting.volume = info.volumeLevel;
      showMiniPlayer();
      startCastPolling();
    }
  } catch (e) { /* silent - server might not be reachable */ }
}

// ─── Persistence ────────────────────────────────────────────
function loadPersistedData() {
  try {
    state.positions = JSON.parse(localStorage.getItem('cm_positions') || '{}');
    state.history = JSON.parse(localStorage.getItem('cm_history') || '[]');
    state.playlists = JSON.parse(localStorage.getItem('cm_playlists') || '[]');
    state.queue = JSON.parse(localStorage.getItem('cm_queue') || '[]');
    const settings = JSON.parse(localStorage.getItem('cm_settings') || '{}');
    Object.assign(state.settings, settings);
    state.libraryView = state.settings.defaultView || 'list';
    state.casting.volume = parseInt(localStorage.getItem('cm_volume') || '80');
  } catch (e) { /* ignore parse errors */ }

  // Apply settings to UI
  const els = {
    'setting-transcode': state.settings.autoTranscode,
    'setting-autoadvance': state.settings.autoAdvance,
    'setting-save-interval': String(state.settings.saveInterval),
    'setting-view': state.settings.defaultView,
  };
  for (const [id, val] of Object.entries(els)) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = val;
    else el.value = val;
  }
  document.getElementById('volume-slider').value = state.casting.volume;
}

function savePositions() { localStorage.setItem('cm_positions', JSON.stringify(state.positions)); }
function saveHistory() { localStorage.setItem('cm_history', JSON.stringify(state.history.slice(0, 100))); }
function savePlaylists() { localStorage.setItem('cm_playlists', JSON.stringify(state.playlists)); }
function saveQueue() { localStorage.setItem('cm_queue', JSON.stringify(state.queue)); }
function saveSetting(key, value) {
  state.settings[key] = value;
  localStorage.setItem('cm_settings', JSON.stringify(state.settings));
}

// ─── Navigation ─────────────────────────────────────────────
function showSection(name) {
  state.currentSection = name;
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item, .mobile-nav-item').forEach(b => {
    b.classList.toggle('active', b.dataset.section === name);
  });
  const section = document.getElementById(`section-${name}`);
  if (section) section.classList.add('active');

  // Load data for section
  if (name === 'home') { loadTorrents(); renderContinueWatching(); loadDiskInfo(); }
  if (name === 'torrents') loadTorrents();
  if (name === 'library') loadFiles();
  if (name === 'queue') renderQueue();
  if (name === 'playlists') renderPlaylists();
  if (name === 'recent') loadRecent();
  if (name === 'starred') loadStarred();
  if (name === 'shared') loadShared();
  if (name === 'trash') loadTrash();
  if (name === 'storage') loadStorage();
  if (name === 'activity') loadActivityLog();
}

// ─── Toast Notifications ───────────────────────────────────
function toast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add('removing');
    setTimeout(() => el.remove(), 200);
  }, duration);
}

// ─── Confirm Dialog ─────────────────────────────────────────
function showConfirm(title, message, onOk) {
  document.getElementById('confirm-title').textContent = title;
  document.getElementById('confirm-message').textContent = message;
  document.getElementById('confirm-modal').style.display = 'flex';
  const btn = document.getElementById('confirm-ok-btn');
  btn.onclick = () => { closeConfirm(); onOk(); };
}
function closeConfirm() { document.getElementById('confirm-modal').style.display = 'none'; }

// ─── Password Prompt Dialog ─────────────────────────────────
function showPasswordPrompt(title, message, onOk) {
  document.getElementById('password-title').textContent = title;
  document.getElementById('password-message').textContent = message;
  const input = document.getElementById('password-input');
  input.value = '';
  document.getElementById('password-modal').style.display = 'flex';
  const btn = document.getElementById('password-ok-btn');
  btn.onclick = () => { 
    closePasswordPrompt(); 
    onOk(input.value); 
  };
}
function closePasswordPrompt() { document.getElementById('password-modal').style.display = 'none'; }

// ─── API Helpers ────────────────────────────────────────────
async function api(url, options = {}) {
  try {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    return await res.json();
  } catch (err) {
    toast(`Request failed: ${err.message}`, 'error');
    throw err;
  }
}

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return (bytes / Math.pow(k, i)).toFixed(i > 1 ? 1 : 0) + ' ' + sizes[i];
}

function formatTime(seconds) {
  if (!seconds || isNaN(seconds)) return '0:00';
  seconds = Math.floor(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatTimeHMS(seconds) {
  if (!seconds || isNaN(seconds)) return '00:00:00';
  seconds = Math.floor(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function timeAgo(timestamp) {
  const diff = Date.now() - new Date(timestamp).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(timestamp).toLocaleDateString();
}

function fileHash(filePath) {
  let hash = 0;
  for (let i = 0; i < filePath.length; i++) {
    hash = ((hash << 5) - hash) + filePath.charCodeAt(i);
    hash |= 0;
  }
  return 'f' + Math.abs(hash).toString(36);
}

function basename(filePath) {
  return filePath.split('/').pop();
}

// ═══════════════════════════════════════════════════════════════
// TORRENT MANAGEMENT
// ═══════════════════════════════════════════════════════════════

async function loadTorrents() {
  try {
    const data = await api('/api/torrents');
    state.torrents = data.torrents || [];
    renderTorrents();
    updateHomeStats();
  } catch (e) { /* already toasted */ }
}

function renderTorrents() {
  const container = document.getElementById('torrent-list');
  let filtered = state.torrents;

  if (state.torrentFilter !== 'all') {
    if (state.torrentFilter === 'done') {
      filtered = filtered.filter(t => t.done === '100%');
    } else {
      filtered = filtered.filter(t => t.status.toLowerCase().includes(state.torrentFilter));
    }
  }

  if (state.torrentSearch) {
    const q = state.torrentSearch.toLowerCase();
    filtered = filtered.filter(t => t.name.toLowerCase().includes(q));
  }

  if (filtered.length === 0) {
    container.innerHTML = `<div class="empty-state"><p>${state.torrents.length === 0 ? 'No torrents yet.' : 'No matching torrents.'}</p><p class="empty-hint">Drop a magnet link or .torrent file above to get started.</p></div>`;
    return;
  }

  container.innerHTML = filtered.map(t => {
    const pct = t.done === 'n/a' ? 0 : parseInt(t.done);
    const statusClass = t.status === 'Downloading' ? 'downloading' :
      t.status === 'Seeding' ? 'seeding' :
        t.status === 'Stopped' ? 'stopped' : 'idle';
    const isPaused = t.status === 'Stopped';

    return `<div class="torrent-card" data-id="${t.id}">
      <div class="torrent-top">
        <div class="torrent-name" title="${esc(t.name)}">${esc(t.name)}</div>
        <span class="torrent-status ${statusClass}">${t.status}</span>
      </div>
      <div class="torrent-progress-row">
        <div class="torrent-progress-bar"><div class="torrent-progress-fill" style="width:${pct}%"></div></div>
        <div class="torrent-progress-text">${t.done}</div>
      </div>
      <div class="torrent-details">
        <span>Size: <span>${t.have}</span></span>
        <span>Down: <span>${t.down}</span></span>
        <span>Up: <span>${t.up}</span></span>
        <span>ETA: <span>${t.eta}</span></span>
        <span>Ratio: <span>${t.ratio}</span></span>
      </div>
      <div class="torrent-actions">
        <button onclick="event.stopPropagation(); ${isPaused ? `resumeTorrent(${t.id})` : `pauseTorrent(${t.id})`}">${isPaused ? 'Resume' : 'Pause'}</button>
        <button onclick="event.stopPropagation(); showTorrentInfo(${t.id})">Info</button>
        <button onclick="event.stopPropagation(); setPriority(${t.id}, 'high')">High</button>
        <button onclick="event.stopPropagation(); setPriority(${t.id}, 'normal')">Normal</button>
        <button onclick="event.stopPropagation(); setPriority(${t.id}, 'low')">Low</button>
        <button class="danger" onclick="event.stopPropagation(); removeTorrent(${t.id})">Remove</button>
        <button class="danger" onclick="event.stopPropagation(); removeTorrent(${t.id}, true)">Remove + Data</button>
      </div>
    </div>`;
  }).join('');
}

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function filterTorrents(filter, btn) {
  state.torrentFilter = filter;
  document.querySelectorAll('#section-torrents .filter-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderTorrents();
}

function filterTorrentsByName(query) {
  state.torrentSearch = query;
  renderTorrents();
}

async function addMagnet() {
  const input = document.getElementById('magnet-input');
  const val = input.value.trim();
  if (!val) return;

  // Support multiple magnets (newline separated)
  const magnets = val.split('\n').map(s => s.trim()).filter(Boolean);
  try {
    const data = await api('/api/torrents', { method: 'POST', body: { magnets } });
    const successes = data.results.filter(r => r.success).length;
    toast(`Added ${successes} torrent(s)`, 'success');
    input.value = '';
    loadTorrents();
  } catch (e) { /* already toasted */ }
}

function handleDragOver(e) {
  e.preventDefault();
  e.currentTarget.classList.add('dragover');
}
function handleDragLeave(e) {
  e.currentTarget.classList.remove('dragover');
}
async function handleDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('dragover');

  // Check for text (magnet link)
  const text = e.dataTransfer.getData('text/plain');
  if (text && text.startsWith('magnet:')) {
    document.getElementById('magnet-input').value = text;
    addMagnet();
    return;
  }

  // Check for file
  const files = e.dataTransfer.files;
  if (files.length > 0) {
    for (const file of files) {
      if (file.name.endsWith('.torrent')) {
        await uploadTorrentFile(file);
      }
    }
  }
}

async function uploadTorrent(input) {
  if (input.files.length > 0) await uploadTorrentFile(input.files[0]);
  input.value = '';
}

async function uploadTorrentFile(file) {
  const formData = new FormData();
  formData.append('torrent', file);
  try {
    const res = await fetch('/api/torrents/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.success) {
      toast('Torrent file added', 'success');
      loadTorrents();
    } else {
      toast(data.error || 'Upload failed', 'error');
    }
  } catch (e) {
    toast('Upload failed: ' + e.message, 'error');
  }
}

async function pasteFromClipboard() {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      document.getElementById('magnet-input').value = text;
      if (text.startsWith('magnet:')) addMagnet();
    }
  } catch (e) {
    toast('Could not read clipboard', 'warning');
  }
}

async function pauseTorrent(id) {
  await api(`/api/torrents/${id}/pause`, { method: 'POST' });
  toast('Torrent paused', 'info');
  loadTorrents();
}

async function resumeTorrent(id) {
  await api(`/api/torrents/${id}/resume`, { method: 'POST' });
  toast('Torrent resumed', 'info');
  loadTorrents();
}

async function removeTorrent(id, deleteData = false) {
  const msg = deleteData ? 'Remove torrent AND delete downloaded files?' : 'Remove torrent from list?';
  showConfirm('Remove Torrent', msg, async () => {
    await api(`/api/torrents/${id}?deleteData=${deleteData}`, { method: 'DELETE' });
    toast('Torrent removed', 'success');
    loadTorrents();
  });
}

async function setPriority(id, priority) {
  await api(`/api/torrents/${id}/priority`, { method: 'POST', body: { priority } });
  toast(`Priority set to ${priority}`, 'info');
}

async function pauseAllTorrents() {
  await api('/api/torrents/pause-all', { method: 'POST' });
  toast('All torrents paused', 'info');
  loadTorrents();
}

async function resumeAllTorrents() {
  await api('/api/torrents/resume-all', { method: 'POST' });
  toast('All torrents resumed', 'info');
  loadTorrents();
}

async function showTorrentInfo(id) {
  try {
    const data = await api(`/api/torrents/${id}/info`);
    showConfirm('Torrent Info', data.info || 'No info available', () => { });
    // Override the OK button text
    document.getElementById('confirm-ok-btn').textContent = 'Close';
  } catch (e) { /* already toasted */ }
}

function updateHomeStats() {
  const active = state.torrents.filter(t => t.status === 'Downloading').length;
  const completed = state.torrents.filter(t => t.done === '100%').length;
  const totalDown = state.torrents.reduce((s, t) => {
    const speed = parseFloat(t.down) || 0;
    return s + speed;
  }, 0);

  document.getElementById('stat-active').textContent = active;
  document.getElementById('stat-completed').textContent = completed;
  document.getElementById('stat-down-speed').textContent = totalDown > 0 ? totalDown.toFixed(1) + ' KB/s' : 'Idle';
}

async function loadDiskInfo() {
  try {
    const data = await api('/api/disk');
    document.getElementById('stat-disk').textContent = data.available || '--';
  } catch (e) {
    document.getElementById('stat-disk').textContent = '--';
  }
}

// ═══════════════════════════════════════════════════════════════
// FILE BROWSER
// ═══════════════════════════════════════════════════════════════

async function loadFiles(path) {
  if (path) state.currentPath = path;
  try {
    const data = await api(`/api/files?path=${encodeURIComponent(state.currentPath)}`);
    state.files = data.files || [];
    renderBreadcrumb(data.currentPath, data.parentPath);
    renderFiles();
    
    const delBtn = document.getElementById('btn-delete-folder');
    if (delBtn) {
      if (data.currentPath === '/home/REDACTED_USER/watch_list' || data.currentPath === '/home/REDACTED_USER/watch_list/') {
        delBtn.style.display = 'none';
      } else {
        delBtn.style.display = 'inline-block';
      }
    }
  } catch (e) { /* already toasted */ }
}

function renderBreadcrumb(currentPath, parentPath) {
  const container = document.getElementById('breadcrumb');
  const parts = currentPath.split('/').filter(Boolean);
  let html = '';
  let accumulated = '';
  for (let i = 0; i < parts.length; i++) {
    accumulated += '/' + parts[i];
    const isLast = i === parts.length - 1;
    if (isLast) {
      html += `<span class="breadcrumb-item" style="color:var(--color-text-primary);font-weight:500">${esc(parts[i])}</span>`;
    } else {
      const p = accumulated;
      html += `<button class="breadcrumb-item" onclick="loadFiles('${esc(p)}')">${esc(parts[i])}</button><span class="breadcrumb-sep">/</span>`;
    }
  }
  container.innerHTML = html;
}

function renderFiles() {
  const container = document.getElementById('file-list');
  let filtered = state.files;

  if (state.fileFilter !== 'all') {
    filtered = filtered.filter(f => f.type === state.fileFilter || f.type === 'folder');
  }
  if (state.fileSearch) {
    const q = state.fileSearch.toLowerCase();
    filtered = filtered.filter(f => f.name.toLowerCase().includes(q));
  }

  if (filtered.length === 0) {
    let emptyHtml = '<div class="empty-state"><p>This folder is empty.</p>';
    if (state.currentPath !== '/home/REDACTED_USER/watch_list' && state.currentPath !== '/home/REDACTED_USER/watch_list/') {
      emptyHtml += '<button class="btn-secondary btn-sm" onclick="deleteCurrentFolder()" style="margin-top:12px;color:var(--color-danger)">Delete this folder</button>';
    }
    emptyHtml += '</div>';
    container.innerHTML = emptyHtml;
    return;
  }

  // Sort files: folders always first, then by selected sort
  filtered.sort((a, b) => {
    if (a.type === 'folder' && b.type !== 'folder') return -1;
    if (a.type !== 'folder' && b.type === 'folder') return 1;
    switch (state.fileSort) {
      case 'size': return (b.size || 0) - (a.size || 0);
      case 'date': return (b.mtime || 0) - (a.mtime || 0);
      case 'type': return (a.ext || '').localeCompare(b.ext || '') || a.name.localeCompare(b.name);
      default: return a.name.localeCompare(b.name);
    }
  });

  const isGrid = state.libraryView === 'grid';
  container.className = `file-list ${isGrid ? 'grid-view' : 'list-view'}`;

  container.innerHTML = filtered.map(f => {
    const icon = f.type === 'folder' ? '&#128193;' :
      f.type === 'video' ? '&#127916;' :
        f.type === 'audio' ? '&#127925;' :
          f.type === 'subtitle' ? '&#128196;' : '&#128196;';

    const pos = state.positions[fileHash(f.path)];
    const progressHtml = pos && pos.duration > 0
      ? `<div class="file-progress-bar" style="width:${Math.min(100, (pos.position / pos.duration) * 100)}%"></div>`
      : '';

    const escapedPath = esc(f.path).replace(/'/g, "\\'");
    const escapedName = esc(f.name).replace(/'/g, "\\'");
    const mediaActions = f.type === 'video' || f.type === 'audio'
      ? `<button onclick="event.stopPropagation(); openStreamPlayer('${escapedPath}', '${f.type}')" style="color:var(--color-accent)">&#9654; Play</button>
         <button onclick="event.stopPropagation(); showStreamUrlModal('${escapedPath}')" title="Generate shareable stream URL">Stream URL</button>
         <button onclick="event.stopPropagation(); openCastModal('${escapedPath}', '${f.type}')">Cast</button>
         <button onclick="event.stopPropagation(); addToQueue('${escapedPath}', '${escapedName}', '${f.type}')">Queue</button>
         <button onclick="event.stopPropagation(); showPlaylistDropdown(event, '${escapedPath}', '${escapedName}', '${f.type}')">Playlist</button>`
      : '';
    const actions = `<div class="file-actions">
          ${mediaActions}
          <button onclick="event.stopPropagation(); toggleStar('${escapedPath}')" title="Star">&#11088;</button>
          <button onclick="event.stopPropagation(); openShareModal('${escapedPath}', '${escapedName}')" title="Share">Share</button>
          <button onclick="event.stopPropagation(); downloadFile('${escapedPath}')">Download</button>
          <button onclick="event.stopPropagation(); renameFile('${escapedPath}', '${escapedName}')">Rename</button>
          <button onclick="event.stopPropagation(); copyFile('${escapedPath}', '${escapedName}')">Copy</button>
          <button onclick="event.stopPropagation(); moveFile('${escapedPath}', '${escapedName}')">Move</button>
          <button onclick="event.stopPropagation(); deleteFile('${escapedPath}', '${escapedName}')" style="color:var(--color-danger)">Delete</button>
        </div>`;

    const clickAction = f.type === 'folder'
      ? `onclick="loadFiles('${esc(f.path)}')" `
      : (f.type === 'video' || f.type === 'audio')
        ? `onclick="openStreamPlayer('${esc(f.path)}', '${f.type}')" `
        : '';

    return `<div class="file-item" ${clickAction}>
      <div class="file-thumb-wrapper">
        <div class="file-thumb-placeholder">${icon}</div>
        <img class="file-thumb" data-src="" loading="lazy" onerror="this.style.display='none'">
        ${progressHtml}
      </div>
      <div class="file-icon">${icon}</div>
      <div class="file-info">
        <div class="file-name${f.type === 'folder' ? ' folder' : ''}">${esc(f.name)}</div>
        <div class="file-meta">
          <span>${formatBytes(f.size)}</span>
          ${f.type === 'folder' ? `<span>${f.itemCount !== undefined ? f.itemCount + ' items' : 'Folder'}</span>` : `<span>${f.ext}</span>`}
        </div>
      </div>
      ${actions}
    </div>`;
  }).join('');

  // Lazy load thumbnails for media files
  if (isGrid) loadThumbnails(filtered);
}

async function loadThumbnails(files) {
  for (const f of files) {
    if (f.type !== 'video' && f.type !== 'audio') continue;
    try {
      const data = await api('/api/thumbnail', { method: 'POST', body: { filePath: f.path, type: f.type } });
      if (data.thumbnail) {
        const items = document.querySelectorAll('.file-item');
        items.forEach(item => {
          const nameEl = item.querySelector('.file-name');
          if (nameEl && nameEl.textContent === f.name) {
            const img = item.querySelector('.file-thumb');
            if (img) {
              img.src = data.thumbnail;
              img.style.display = 'block';
              const placeholder = item.querySelector('.file-thumb-placeholder');
              if (placeholder) placeholder.style.display = 'none';
            }
          }
        });
      }
    } catch (e) { /* skip */ }
  }
}

function filterFiles(query) {
  state.fileSearch = query;
  renderFiles();
}

function filterFileType(type, btn) {
  state.fileFilter = type;
  document.querySelectorAll('#section-library .filter-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderFiles();
}

function toggleLibraryView() {
  state.libraryView = state.libraryView === 'list' ? 'grid' : 'list';
  renderFiles();
}

// ─── File Operations ─────────────────────────────────────────
function deleteFile(filePath, fileName) {
  showPasswordPrompt('Delete File', `Enter sudo password to permanently delete "${fileName}":`, async (sudoPwd) => {
    try {
      await api('/api/files/delete', { method: 'POST', body: { filePath, sudoPwd } });
      toast(`Deleted: ${fileName}`, 'success');
      loadFiles();
    } catch (e) { /* already toasted */ }
  });
}

function renameFile(filePath, currentName) {
  const newName = prompt('Rename to:', currentName);
  if (!newName || newName === currentName) return;
  showPasswordPrompt('Rename File', `Enter sudo password to rename to "${newName}":`, async (sudoPwd) => {
    try {
      await api('/api/files/rename', { method: 'POST', body: { oldPath: filePath, newName, sudoPwd } });
      toast(`Renamed to: ${newName}`, 'success');
      loadFiles();
    } catch (e) { /* already toasted */ }
  });
}

function copyFile(filePath, currentName) {
  const ext = currentName.lastIndexOf('.') > 0 ? currentName.slice(currentName.lastIndexOf('.')) : '';
  const base = currentName.lastIndexOf('.') > 0 ? currentName.slice(0, currentName.lastIndexOf('.')) : currentName;
  const destName = prompt('Copy as:', `${base} (copy)${ext}`);
  if (!destName) return;
  showPasswordPrompt('Copy File', `Enter sudo password to copy as "${destName}":`, async (sudoPwd) => {
    try {
      toast('Copying file...', 'info');
      await api('/api/files/copy', { method: 'POST', body: { filePath, destName, sudoPwd } });
      toast(`Copied as: ${destName}`, 'success');
      loadFiles();
    } catch (e) { /* already toasted */ }
  });
}

function downloadFile(filePath) {
  const a = document.createElement('a');
  a.href = `/api/files/download?path=${encodeURIComponent(filePath)}`;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  toast('Download started', 'info');
}

// ─── Stream Player ──────────────────────────────────────────
let currentStreamPath = '';

function getStreamUrl(filePath) {
  return `${window.location.origin}/api/files/stream?path=${encodeURIComponent(filePath)}`;
}

function openStreamPlayer(filePath, type) {
  currentStreamPath = filePath;
  const modal = document.getElementById('stream-player-modal');
  const videoContainer = document.getElementById('stream-video-container');
  const audioContainer = document.getElementById('stream-audio-container');
  const video = document.getElementById('stream-video');
  const audio = document.getElementById('stream-audio');
  const title = document.getElementById('stream-player-title');

  title.textContent = basename(filePath);
  const streamUrl = getStreamUrl(filePath);

  // Set saved volume
  const savedVol = parseFloat(localStorage.getItem('cm_stream_vol') || '1');
  video.volume = savedVol;
  audio.volume = savedVol;

  // Track recent
  api('/api/files/recent', { method: 'POST', body: { path: filePath, action: type === 'video' ? 'play_video' : 'play_audio' } }).catch(() => {});

  if (type === 'video') {
    videoContainer.style.display = 'block';
    audioContainer.style.display = 'none';
    video.src = streamUrl;
    // Handle autoplay restriction
    video.play().catch(() => {
      // Show click-to-play overlay
      let overlay = videoContainer.querySelector('.click-to-play');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'click-to-play';
        overlay.innerHTML = '<div style="cursor:pointer;padding:20px"><svg width="64" height="64" viewBox="0 0 24 24" fill="white"><polygon points="5 3 19 12 5 21"/></svg><div style="margin-top:8px;font-size:14px">Click to play</div></div>';
        overlay.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6);z-index:10;color:white;text-align:center';
        overlay.onclick = () => { video.play(); overlay.remove(); };
        videoContainer.style.position = 'relative';
        videoContainer.appendChild(overlay);
      }
    });
  } else {
    videoContainer.style.display = 'none';
    audioContainer.style.display = 'block';
    audio.src = streamUrl;
    audio.play().catch(() => { /* user will click play manually */ });
  }

  // Save volume on change
  video.onvolumechange = () => localStorage.setItem('cm_stream_vol', String(video.volume));
  audio.onvolumechange = () => localStorage.setItem('cm_stream_vol', String(audio.volume));

  modal.style.display = 'flex';

  // Load codec info
  loadCodecInfo(filePath);
}

function closeStreamPlayer() {
  const modal = document.getElementById('stream-player-modal');
  const video = document.getElementById('stream-video');
  const audio = document.getElementById('stream-audio');
  video.pause(); video.src = '';
  audio.pause(); audio.src = '';
  modal.style.display = 'none';
  currentStreamPath = '';
}

async function copyStreamUrl(filePath) {
  try {
    const data = await api('/api/stream/generate', { method: 'POST', body: { filePath, expiresIn: 24 } });
    if (data.streamUrl) {
      await navigator.clipboard.writeText(data.streamUrl);
      toast('Stream URL copied! Works in VLC, browser, or any player.', 'success');
    }
  } catch (e) {
    // Fallback to internal URL
    const url = getStreamUrl(filePath);
    navigator.clipboard.writeText(url).then(() => {
      toast('Internal stream URL copied (VPN only)', 'success');
    }).catch(() => {
      prompt('Copy this stream URL:', url);
    });
  }
}

function copyCurrentStreamUrl() {
  if (currentStreamPath) copyStreamUrl(currentStreamPath);
}

// Token-based stream URL generation — fixes the broken Stream URL button
async function generateStreamUrlForCurrent() {
  if (!currentStreamPath) return;
  try {
    const data = await api('/api/stream/generate', { method: 'POST', body: { filePath: currentStreamPath, expiresIn: 24 } });
    if (data.streamUrl) {
      // Show stream URL panel inside stream player modal
      const panel = document.getElementById('stream-url-panel');
      const input = document.getElementById('stream-url-input');
      const openLink = document.getElementById('stream-url-open');
      const expiresDiv = document.getElementById('stream-url-expires');
      panel.style.display = 'block';
      input.value = data.streamUrl;
      openLink.href = data.streamUrl;
      expiresDiv.textContent = `Expires: ${new Date(data.expiresAt).toLocaleString()}`;
      toast('Stream URL generated!', 'success');
    }
  } catch (e) {
    toast('Failed to generate stream URL', 'error');
  }
}

function copyStreamUrlFromInput() {
  const input = document.getElementById('stream-url-input');
  navigator.clipboard.writeText(input.value).then(() => {
    toast('Stream URL copied!', 'success');
  }).catch(() => {
    input.select();
    document.execCommand('copy');
    toast('Stream URL copied!', 'success');
  });
}

// Show standalone Stream URL modal from file list
async function showStreamUrlModal(filePath) {
  try {
    const data = await api('/api/stream/generate', { method: 'POST', body: { filePath, expiresIn: 24 } });
    const modal = document.getElementById('stream-url-modal');
    document.getElementById('stream-url-modal-filename').textContent = basename(filePath);
    document.getElementById('stream-url-modal-input').value = data.streamUrl;
    document.getElementById('stream-url-modal-open').href = data.streamUrl;
    document.getElementById('stream-url-modal-expires').textContent = `Expires: ${new Date(data.expiresAt).toLocaleString()}`;
    // Load QR code
    const qrContainer = document.getElementById('stream-url-modal-qr');
    qrContainer.innerHTML = '<span style="color:var(--color-text-muted);font-size:12px">Loading QR...</span>';
    try {
      const res = await fetch(`/api/qrcode?text=${encodeURIComponent(data.streamUrl)}`);
      const svg = await res.text();
      qrContainer.innerHTML = svg;
    } catch (e) { qrContainer.innerHTML = ''; }
    modal.style.display = 'flex';
  } catch (e) {
    toast('Failed to generate stream URL', 'error');
  }
}

function closeStreamUrlModal() {
  document.getElementById('stream-url-modal').style.display = 'none';
}

async function loadCodecInfo(filePath) {
  try {
    const info = await api(`/api/media/info?path=${encodeURIComponent(filePath)}`);
    const badge = document.getElementById('stream-codec-badge');
    if (info) {
      const parts = [];
      if (info.resolution) parts.push(info.resolution);
      if (info.videoCodec) parts.push(info.videoCodec.toUpperCase());
      if (info.audioCodec) {
        const codec = info.audioCodec.toUpperCase();
        parts.push(info.needsTranscode ? `${codec} → AAC (transcoding)` : codec);
      }
      if (info.duration) parts.push(formatTime(info.duration));
      if (info.size) parts.push(formatBytes(info.size));
      badge.textContent = parts.join(' · ');
      badge.style.display = 'block';
    }
  } catch (e) { /* skip */ }
}

// ─── New Folder ─────────────────────────────────────────────
function createNewFolder() {
  const name = prompt('New folder name:');
  if (!name) return;
  (async () => {
    try {
      await api('/api/files/mkdir', { method: 'POST', body: { parentPath: state.currentPath, name } });
      toast(`Created folder: ${name}`, 'success');
      loadFiles();
    } catch (e) { /* already toasted */ }
  })();
}

// ─── Delete Current Folder ──────────────────────────────────
function deleteCurrentFolder() {
  if (state.currentPath === '/home/REDACTED_USER/watch_list' || state.currentPath === '/home/REDACTED_USER/watch_list/') {
    return toast('Cannot delete the root download directory.', 'warning');
  }
  const folderName = basename(state.currentPath);
  showPasswordPrompt('Delete Folder', `Enter sudo password to permanently delete the current folder "${folderName}":`, async (sudoPwd) => {
    try {
      await api('/api/files/delete', { method: 'POST', body: { filePath: state.currentPath, sudoPwd } });
      toast(`Deleted: ${folderName}`, 'success');
      // Go up one directory level
      const parts = state.currentPath.split('/').filter(Boolean);
      parts.pop();
      const parent = parts.join('/') || '/home/REDACTED_USER/watch_list';
      // Ensure absolute path formatting
      loadFiles(parent.startsWith('/') ? parent : '/' + parent);
    } catch (e) { /* already toasted */ }
  });
}

// ─── Move File ──────────────────────────────────────────────
function moveFile(filePath, fileName) {
  const dest = prompt('Move to directory (full path):', state.currentPath);
  if (!dest || dest === state.currentPath) return;
  showPasswordPrompt('Move File', `Enter sudo password to move to "${dest}":`, async (sudoPwd) => {
    try {
      await api('/api/files/move', { method: 'POST', body: { sourcePath: filePath, destDir: dest, sudoPwd } });
      toast(`Moved: ${fileName}`, 'success');
      loadFiles();
    } catch (e) { /* already toasted */ }
  });
}

// ─── Sort Files ─────────────────────────────────────────────
function sortFiles(sortBy) {
  state.fileSort = sortBy;
  renderFiles();
}

// ═══════════════════════════════════════════════════════════════
// CHROMECAST CASTING
// ═══════════════════════════════════════════════════════════════

async function openCastModal(filePath, type) {
  state.castModalFile = { path: filePath, type };
  const modal = document.getElementById('cast-modal');
  const name = basename(filePath);

  document.getElementById('cast-modal-filename').textContent = name;
  document.getElementById('cast-seek-input').value = '00:00:00';

  // Get duration
  try {
    const data = await api('/api/files/duration', { method: 'POST', body: { filePath } });
    const dur = data.duration || 0;
    state.castModalFile.duration = dur;
    document.getElementById('cast-modal-duration').textContent = dur > 0 ? `Duration: ${formatTime(dur)}` : '';
  } catch (e) {
    document.getElementById('cast-modal-duration').textContent = '';
  }

  // Check for saved position
  const pos = state.positions[fileHash(filePath)];
  const resumeBtn = document.getElementById('cast-resume-btn');
  if (pos && pos.position > 180 && pos.percentage < 95) {
    resumeBtn.style.display = 'block';
    document.getElementById('cast-resume-time').textContent = formatTime(pos.position);
  } else {
    resumeBtn.style.display = 'none';
  }

  // Check for subtitles
  if (type === 'video') {
    try {
      const subData = await api('/api/subtitles', { method: 'POST', body: { filePath } });
      const subSection = document.getElementById('subtitle-section');
      const subSelect = document.getElementById('subtitle-select');
      if (subData.subtitles && subData.subtitles.length > 0) {
        subSection.style.display = 'block';
        subSelect.innerHTML = '<option value="">None</option>' +
          subData.subtitles.map(s => `<option value="${esc(s)}">${esc(basename(s))}</option>`).join('');
      } else {
        subSection.style.display = 'none';
      }
    } catch (e) {
      document.getElementById('subtitle-section').style.display = 'none';
    }
  } else {
    document.getElementById('subtitle-section').style.display = 'none';
  }

  modal.style.display = 'flex';
}

function closeCastModal() {
  document.getElementById('cast-modal').style.display = 'none';
  state.castModalFile = null;
}

function setSeek(time) {
  document.getElementById('cast-seek-input').value = time;
}

function resumeCast() {
  if (!state.castModalFile) return;
  const pos = state.positions[fileHash(state.castModalFile.path)];
  if (pos) {
    document.getElementById('cast-seek-input').value = formatTimeHMS(pos.position);
  }
  confirmCast();
}

async function confirmCast() {
  if (!state.castModalFile) return;
  const seekTo = document.getElementById('cast-seek-input').value;
  const subtitlePath = document.getElementById('subtitle-select')?.value || '';
  const filePath = state.castModalFile.path;

  closeCastModal();
  toast('Casting...', 'info');

  try {
    let endpoint = '/api/cast';
    let body = { filePath, seekTo: seekTo !== '00:00:00' ? seekTo : undefined };

    if (subtitlePath) {
      endpoint = '/api/cast/subtitles';
      body.subtitlePath = subtitlePath;
    }

    const data = await api(endpoint, { method: 'POST', body });

    if (data.transcoding) {
      toast(data.message, 'warning', 8000);
      // Poll for transcoding completion
      pollTranscode(filePath, seekTo);
      return;
    }

    state.casting.filePath = filePath;
    state.casting.title = basename(filePath);
    state.casting.state = 'playing';
    toast('Casting started', 'success');
    showMiniPlayer();
    updatePositionEntry(filePath);
    startCastPolling();
  } catch (e) { /* already toasted */ }
}

async function pollTranscode(filePath, seekTo) {
  let attempts = 0;
  const maxAttempts = 200; // ~10 minutes at 3s intervals
  const check = async () => {
    attempts++;
    try {
      const data = await api('/api/transcode/status', { method: 'POST', body: { filePath } });
      if (data.ready) {
        toast('Transcoding complete! Casting now...', 'success');
        try {
          await api('/api/cast', { method: 'POST', body: { filePath, seekTo } });
          state.casting.filePath = filePath;
          state.casting.title = basename(filePath);
          state.casting.state = 'playing';
          showMiniPlayer();
          updatePositionEntry(filePath);
          startCastPolling();
        } catch (e) {
          toast('Cast failed after transcoding: ' + (e.message || ''), 'error');
        }
      } else if (data.processing || attempts < 5) {
        // Keep polling - show progress for large files
        if (data.tmpSize > 0) {
          const mb = (data.tmpSize / 1048576).toFixed(0);
          toast(`Transcoding audio... (${mb} MB written)`, 'info', 3500);
        } else if (attempts % 5 === 0) {
          toast('Still transcoding audio...', 'info', 3500);
        }
        if (attempts < maxAttempts) {
          setTimeout(check, 3000);
        } else {
          toast('Transcoding timed out. Try casting again.', 'error');
        }
      } else {
        toast('Transcoding may have failed. Check server logs.', 'error');
      }
    } catch (e) {
      if (attempts < maxAttempts) {
        setTimeout(check, 5000);
      }
    }
  };
  toast('Transcoding audio for Chromecast compatibility...', 'info', 5000);
  setTimeout(check, 5000);
}

async function quickCast() {
  const input = document.getElementById('quick-cast-url');
  const url = input.value.trim();
  if (!url) return;

  if (url.startsWith('magnet:')) {
    try {
      await api('/api/torrents', { method: 'POST', body: { magnets: [url] } });
      toast('Torrent added', 'success');
      input.value = '';
      loadTorrents();
    } catch (e) { /* toasted */ }
    return;
  }

  toast('Casting stream...', 'info');
  try {
    await api('/api/stream', { method: 'POST', body: { url } });
    state.casting.state = 'playing';
    state.casting.title = url.length > 50 ? url.substring(0, 50) + '...' : url;
    showMiniPlayer();
    startCastPolling();
    toast('Stream started', 'success');
    // Save to recent streams
    const streams = JSON.parse(localStorage.getItem('cm_streams') || '[]');
    streams.unshift({ url, time: new Date().toISOString() });
    localStorage.setItem('cm_streams', JSON.stringify(streams.slice(0, 20)));
    input.value = '';
  } catch (e) { /* toasted */ }
}

// ─── Cast Status Polling ────────────────────────────────────
function startCastPolling() {
  stopCastPolling();
  pollCastStatus();
  state.pollingTimers.cast = setInterval(pollCastStatus, 2000);
  // Position save timer
  state.pollingTimers.positionSave = setInterval(saveCurrentPosition, (state.settings.saveInterval || 30) * 1000);
}

function stopCastPolling() {
  clearInterval(state.pollingTimers.cast);
  clearInterval(state.pollingTimers.positionSave);
}

async function pollCastStatus() {
  if (state.isDraggingScrubber) return;
  try {
    const info = await api('/api/cast/status');
    state.casting.state = info.state || 'idle';
    state.casting.currentTime = info.currentTime || 0;
    state.casting.duration = info.duration || 0;
    if (info.title) state.casting.title = info.title;
    if (info.volumeLevel !== undefined) state.casting.volume = info.volumeLevel;

    updateMiniPlayer();
    updateDeviceIndicator();

    // Show mini-player when actively casting, hide when idle
    if (info.state === 'playing' || info.state === 'paused' || info.state === 'buffering') {
      showMiniPlayer();
    }

    // Check if playback ended for auto-advance
    if (info.state === 'idle' && state.casting.filePath) {
      saveCurrentPosition();
      if (state.queueIndex >= 0) {
        handlePlaybackEnded();
      } else {
        hideMiniPlayer();
        stopCastPolling();
      }
    }
  } catch (e) { /* silent */ }
}

function updateDeviceIndicator() {
  const dot = document.querySelector('.device-dot');
  if (!dot) return;
  dot.className = 'device-dot ' + (state.casting.state === 'idle' ? 'idle' :
    state.casting.state === 'playing' ? 'playing' : 'idle');
}

// ─── Playback Controls ─────────────────────────────────────
async function togglePlayPause() {
  const action = state.casting.state === 'playing' ? 'pause' : 'play';
  await api('/api/cast/controls', { method: 'POST', body: { action } });
  state.casting.state = action === 'pause' ? 'paused' : 'playing';
  updateMiniPlayer();
}

async function stopPlayback() {
  await api('/api/cast/controls', { method: 'POST', body: { action: 'stop' } });
  saveCurrentPosition();
  state.casting.state = 'idle';
  state.casting.currentTime = 0;
  hideMiniPlayer();
  stopCastPolling();
}

async function seekRelative(delta) {
  const newTime = Math.max(0, state.casting.currentTime + delta);
  await api('/api/cast/controls', { method: 'POST', body: { action: 'seek', value: Math.floor(newTime) } });
  state.casting.currentTime = newTime;
  updateMiniPlayer();
}

async function seekTo(seconds) {
  await api('/api/cast/controls', { method: 'POST', body: { action: 'seek', value: Math.floor(seconds) } });
  state.casting.currentTime = seconds;
  updateMiniPlayer();
}

async function setVolume(val) {
  val = Math.max(0, Math.min(100, parseInt(val)));
  await api('/api/cast/controls', { method: 'POST', body: { action: 'volume', value: val } });
  state.casting.volume = val;
  localStorage.setItem('cm_volume', String(val));
}

async function toggleMute() {
  const newVol = state.casting.volume > 0 ? 0 : 80;
  document.getElementById('volume-slider').value = newVol;
  await setVolume(newVol);
}

// ─── Mini Player ────────────────────────────────────────────
function showMiniPlayer() {
  document.getElementById('mini-player').classList.remove('hidden');
  updateMiniPlayer();
}

function hideMiniPlayer() {
  document.getElementById('mini-player').classList.add('hidden');
}

function updateMiniPlayer() {
  const title = state.casting.title || 'Nothing playing';
  document.getElementById('mini-title').textContent = title;
  document.getElementById('mini-time-current').textContent = formatTime(state.casting.currentTime);
  document.getElementById('mini-time-duration').textContent = formatTime(state.casting.duration);

  // Update play/pause icons
  const isPlaying = state.casting.state === 'playing';
  document.getElementById('play-icon').style.display = isPlaying ? 'none' : 'block';
  document.getElementById('pause-icon').style.display = isPlaying ? 'block' : 'none';

  // Update scrubber
  if (!state.isDraggingScrubber && state.casting.duration > 0) {
    const pct = (state.casting.currentTime / state.casting.duration) * 100;
    document.getElementById('mini-scrubber-fill').style.width = pct + '%';
    document.getElementById('mini-scrubber-handle').style.left = pct + '%';
  }
}

// ─── Scrubber Interactions ──────────────────────────────────
function scrubberClick(e) {
  if (state.casting.duration <= 0) return;
  const scrubber = document.getElementById('mini-scrubber');
  const rect = scrubber.getBoundingClientRect();
  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const targetTime = pct * state.casting.duration;
  seekTo(targetTime);
}

function scrubberMouseDown(e) {
  if (state.casting.duration <= 0) return;
  e.preventDefault();
  state.isDraggingScrubber = true;
  const scrubber = document.getElementById('mini-scrubber');
  scrubber.classList.add('dragging');

  const onMove = (e) => {
    const rect = scrubber.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    document.getElementById('mini-scrubber-fill').style.width = (pct * 100) + '%';
    document.getElementById('mini-scrubber-handle').style.left = (pct * 100) + '%';
    const tooltip = document.getElementById('mini-scrubber-tooltip');
    tooltip.style.left = (pct * 100) + '%';
    tooltip.textContent = formatTime(pct * state.casting.duration);
  };

  const onUp = (e) => {
    state.isDraggingScrubber = false;
    scrubber.classList.remove('dragging');
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    const rect = scrubber.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    seekTo(pct * state.casting.duration);
  };

  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

// Scrubber hover tooltip
document.addEventListener('DOMContentLoaded', () => {
  const scrubber = document.getElementById('mini-scrubber');
  if (scrubber) {
    scrubber.addEventListener('mousemove', (e) => {
      if (state.casting.duration <= 0 || state.isDraggingScrubber) return;
      const rect = scrubber.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const tooltip = document.getElementById('mini-scrubber-tooltip');
      tooltip.style.left = (pct * 100) + '%';
      tooltip.textContent = formatTime(pct * state.casting.duration);
    });
  }
});

// ═══════════════════════════════════════════════════════════════
// POSITION TRACKING & RESUME
// ═══════════════════════════════════════════════════════════════

function updatePositionEntry(filePath) {
  const key = fileHash(filePath);
  if (!state.positions[key]) {
    state.positions[key] = {
      filePath,
      fileName: basename(filePath),
      type: filePath.match(/\.(mp3|flac|m4a|aac|ogg|wav|opus)$/i) ? 'audio' : 'video',
      duration: 0,
      position: 0,
      percentage: 0,
      lastPlayed: new Date().toISOString(),
      playCount: 0,
      completed: false,
    };
  }
  state.positions[key].lastPlayed = new Date().toISOString();
  state.positions[key].playCount++;

  // Update history
  state.history = state.history.filter(h => h.filePath !== filePath);
  state.history.unshift({ filePath, time: new Date().toISOString() });
  state.history = state.history.slice(0, 100);
  saveHistory();
}

function saveCurrentPosition() {
  if (!state.casting.filePath || state.casting.currentTime <= 0) return;
  const key = fileHash(state.casting.filePath);
  if (!state.positions[key]) {
    updatePositionEntry(state.casting.filePath);
  }
  const entry = state.positions[key];
  entry.position = state.casting.currentTime;
  entry.duration = state.casting.duration || entry.duration;
  entry.percentage = entry.duration > 0 ? (entry.position / entry.duration) * 100 : 0;
  entry.lastPlayed = new Date().toISOString();
  entry.completed = entry.percentage > 95;
  savePositions();
}

function renderContinueWatching() {
  const container = document.getElementById('continue-watching-list');
  const items = Object.values(state.positions)
    .filter(p => p.position > 0 && !p.completed)
    .sort((a, b) => new Date(b.lastPlayed) - new Date(a.lastPlayed))
    .slice(0, 15);

  if (items.length === 0) {
    container.innerHTML = '<div class="empty-state small">No watch history yet.</div>';
    return;
  }

  container.innerHTML = items.map(item => {
    const pct = item.duration > 0 ? Math.min(100, (item.position / item.duration) * 100) : 0;
    const remaining = item.duration > 0 ? Math.floor((item.duration - item.position) / 60) : 0;
    return `<div class="cw-item" onclick="openCastModal('${esc(item.filePath)}', '${item.type || 'video'}')">
      <div class="cw-thumb-container">
        <div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:2rem;color:var(--color-text-hint);background:var(--color-bg-tertiary)">
          ${item.type === 'audio' ? '&#127925;' : '&#127916;'}
        </div>
        <div class="cw-progress-bar" style="width:${pct}%"></div>
        <div class="cw-time-remaining">${remaining > 0 ? remaining + ' min left' : ''}</div>
      </div>
      <div class="cw-info">
        <div class="cw-title" title="${esc(item.fileName)}">${esc(item.fileName)}</div>
        <div class="cw-sub">${timeAgo(item.lastPlayed)}</div>
      </div>
    </div>`;
  }).join('');
}

// ═══════════════════════════════════════════════════════════════
// QUEUE SYSTEM
// ═══════════════════════════════════════════════════════════════

function addToQueue(filePath, fileName, type, playNext = false) {
  const item = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), filePath, fileName, type };
  if (playNext && state.queueIndex >= 0) {
    state.queue.splice(state.queueIndex + 1, 0, item);
  } else {
    state.queue.push(item);
  }
  saveQueue();
  toast(`Added to queue: ${fileName}`, 'success');
  if (state.currentSection === 'queue') renderQueue();
}

function removeFromQueue(index) {
  state.queue.splice(index, 1);
  if (state.queueIndex >= state.queue.length) state.queueIndex = state.queue.length - 1;
  saveQueue();
  renderQueue();
}

function clearQueue() {
  if (state.queue.length === 0) return;
  showConfirm('Clear Queue', 'Remove all items from the queue?', () => {
    state.queue = [];
    state.queueIndex = -1;
    saveQueue();
    renderQueue();
    toast('Queue cleared', 'info');
  });
}

function shuffleQueue() {
  for (let i = state.queue.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [state.queue[i], state.queue[j]] = [state.queue[j], state.queue[i]];
  }
  saveQueue();
  renderQueue();
  toast('Queue shuffled', 'info');
}

function toggleRepeat() {
  const modes = ['off', 'queue', 'one'];
  const idx = (modes.indexOf(state.repeatMode) + 1) % modes.length;
  state.repeatMode = modes[idx];
  const btn = document.getElementById('repeat-btn');
  btn.style.color = state.repeatMode !== 'off' ? 'var(--color-accent)' : '';
  btn.title = `Repeat: ${state.repeatMode}`;
  toast(`Repeat: ${state.repeatMode}`, 'info');
}

async function playQueueItem(index) {
  if (index < 0 || index >= state.queue.length) return;
  state.queueIndex = index;
  const item = state.queue[index];
  renderQueue();
  // Cast directly
  state.castModalFile = { path: item.filePath, type: item.type };
  const pos = state.positions[fileHash(item.filePath)];
  const seekTo = pos && pos.position > 180 && pos.percentage < 95 ? formatTimeHMS(pos.position) : undefined;

  try {
    await api('/api/cast', { method: 'POST', body: { filePath: item.filePath, seekTo } });
    state.casting.filePath = item.filePath;
    state.casting.title = item.fileName;
    state.casting.state = 'playing';
    showMiniPlayer();
    updatePositionEntry(item.filePath);
    startCastPolling();
  } catch (e) { /* toasted */ }
}

function handlePlaybackEnded() {
  saveCurrentPosition();

  if (state.repeatMode === 'one') {
    playQueueItem(state.queueIndex);
    return;
  }

  if (state.queueIndex >= 0 && state.queueIndex < state.queue.length - 1) {
    if (state.settings.autoAdvance) {
      playQueueItem(state.queueIndex + 1);
      toast('Playing next in queue', 'info');
    }
  } else if (state.repeatMode === 'queue' && state.queue.length > 0) {
    playQueueItem(0);
  } else {
    stopCastPolling();
  }
}

function renderQueue() {
  const container = document.getElementById('queue-list');
  if (state.queue.length === 0) {
    container.innerHTML = '<div class="empty-state"><p>Queue is empty.</p><p class="empty-hint">Browse the library to add items.</p></div>';
    return;
  }

  container.innerHTML = state.queue.map((item, i) => {
    const isActive = i === state.queueIndex;
    return `<div class="queue-item${isActive ? ' active' : ''}" draggable="true"
        ondragstart="queueDragStart(event, ${i})" ondragover="queueDragOver(event)" ondrop="queueDrop(event, ${i})">
      <div class="queue-drag-handle">&#8942;&#8942;</div>
      <div class="queue-item-info" onclick="playQueueItem(${i})">
        <div class="queue-item-title">${isActive ? '&#9654; ' : ''}${esc(item.fileName)}</div>
        <div class="queue-item-meta">${item.type}</div>
      </div>
      <button class="queue-item-remove" onclick="removeFromQueue(${i})">&times;</button>
    </div>`;
  }).join('');
}

let dragSrcIndex = -1;
function queueDragStart(e, index) {
  dragSrcIndex = index;
  e.dataTransfer.effectAllowed = 'move';
}
function queueDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
function queueDrop(e, targetIndex) {
  e.preventDefault();
  if (dragSrcIndex === targetIndex) return;
  const [item] = state.queue.splice(dragSrcIndex, 1);
  state.queue.splice(targetIndex, 0, item);
  // Adjust queueIndex
  if (state.queueIndex === dragSrcIndex) state.queueIndex = targetIndex;
  else if (dragSrcIndex < state.queueIndex && targetIndex >= state.queueIndex) state.queueIndex--;
  else if (dragSrcIndex > state.queueIndex && targetIndex <= state.queueIndex) state.queueIndex++;
  saveQueue();
  renderQueue();
}

// ═══════════════════════════════════════════════════════════════
// PLAYLISTS
// ═══════════════════════════════════════════════════════════════

function showCreatePlaylist() {
  document.getElementById('playlist-modal').style.display = 'flex';
  document.getElementById('playlist-name-input').value = '';
  document.getElementById('playlist-name-input').focus();
}

function closePlaylistModal() {
  document.getElementById('playlist-modal').style.display = 'none';
}

function createPlaylist() {
  const name = document.getElementById('playlist-name-input').value.trim();
  if (!name) return;
  const pl = {
    id: 'pl-' + Date.now().toString(36),
    name,
    items: [],
    created: new Date().toISOString(),
    lastPlayed: null,
  };
  state.playlists.push(pl);
  savePlaylists();
  closePlaylistModal();
  renderPlaylists();
  toast(`Playlist "${name}" created`, 'success');
}

function renderPlaylists() {
  const list = document.getElementById('playlist-list');
  const detail = document.getElementById('playlist-detail');

  if (detail.style.display !== 'none') return; // Don't re-render if viewing detail

  if (state.playlists.length === 0) {
    list.innerHTML = '<div class="empty-state"><p>No playlists yet.</p><p class="empty-hint">Create your first playlist to organize your media.</p></div>';
    return;
  }

  list.innerHTML = state.playlists.map(pl => {
    return `<div class="playlist-card" onclick="viewPlaylist('${pl.id}')">
      <div class="playlist-card-name">${esc(pl.name)}</div>
      <div class="playlist-card-meta">${pl.items.length} item${pl.items.length !== 1 ? 's' : ''}${pl.lastPlayed ? ' &middot; Last played ' + timeAgo(pl.lastPlayed) : ''}</div>
    </div>`;
  }).join('');
}

function viewPlaylist(id) {
  const pl = state.playlists.find(p => p.id === id);
  if (!pl) return;

  document.getElementById('playlist-list').style.display = 'none';
  const detail = document.getElementById('playlist-detail');
  detail.style.display = 'block';

  detail.innerHTML = `
    <div class="playlist-detail-header">
      <div>
        <button class="btn-secondary btn-sm" onclick="backToPlaylists()" style="margin-bottom:8px">&larr; Back</button>
        <div class="playlist-detail-name">${esc(pl.name)}</div>
        <div style="font-size:0.75rem;color:var(--color-text-tertiary)">${pl.items.length} items</div>
      </div>
      <div class="playlist-detail-actions">
        <button class="btn-primary btn-sm" onclick="playPlaylist('${pl.id}')">Play All</button>
        <button class="btn-secondary btn-sm" onclick="shufflePlaylist('${pl.id}')">Shuffle</button>
        <button class="btn-secondary btn-sm danger" onclick="deletePlaylist('${pl.id}')">Delete</button>
      </div>
    </div>
    <div class="playlist-items">
      ${pl.items.length === 0
      ? '<div class="empty-state small">No items. Browse the library to add media.</div>'
      : pl.items.map((item, i) => `
          <div class="queue-item">
            <div class="queue-item-info" onclick="openCastModal('${esc(item.path)}', '${item.type}')">
              <div class="queue-item-title">${esc(item.name)}</div>
              <div class="queue-item-meta">${item.type}</div>
            </div>
            <button class="queue-item-remove" onclick="removePlaylistItem('${pl.id}', ${i})">&times;</button>
          </div>`).join('')
    }
    </div>`;
}

function backToPlaylists() {
  document.getElementById('playlist-list').style.display = '';
  document.getElementById('playlist-detail').style.display = 'none';
  renderPlaylists();
}

function deletePlaylist(id) {
  showConfirm('Delete Playlist', 'Are you sure you want to delete this playlist?', () => {
    state.playlists = state.playlists.filter(p => p.id !== id);
    savePlaylists();
    backToPlaylists();
    toast('Playlist deleted', 'info');
  });
}

function removePlaylistItem(plId, index) {
  const pl = state.playlists.find(p => p.id === plId);
  if (!pl) return;
  pl.items.splice(index, 1);
  savePlaylists();
  viewPlaylist(plId);
}

function playPlaylist(id) {
  const pl = state.playlists.find(p => p.id === id);
  if (!pl || pl.items.length === 0) return;
  state.queue = pl.items.map(item => ({
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    filePath: item.path,
    fileName: item.name,
    type: item.type,
  }));
  state.queueIndex = -1;
  saveQueue();
  pl.lastPlayed = new Date().toISOString();
  savePlaylists();
  playQueueItem(0);
  toast(`Playing playlist: ${pl.name}`, 'success');
}

function shufflePlaylist(id) {
  const pl = state.playlists.find(p => p.id === id);
  if (!pl || pl.items.length === 0) return;
  const shuffled = [...pl.items];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  state.queue = shuffled.map(item => ({
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    filePath: item.path,
    fileName: item.name,
    type: item.type,
  }));
  state.queueIndex = -1;
  saveQueue();
  pl.lastPlayed = new Date().toISOString();
  savePlaylists();
  playQueueItem(0);
  toast(`Shuffling playlist: ${pl.name}`, 'success');
}

// Add to playlist dropdown
function showPlaylistDropdown(e, filePath, fileName, type) {
  e.stopPropagation();
  const dropdown = document.getElementById('add-to-playlist-dropdown');
  const items = document.getElementById('playlist-dropdown-items');

  items.innerHTML = state.playlists.map(pl =>
    `<button class="dropdown-item" onclick="addToPlaylist('${pl.id}', '${esc(filePath)}', '${esc(fileName)}', '${type}')">${esc(pl.name)}</button>`
  ).join('');

  dropdown.style.display = 'block';
  dropdown.style.left = Math.min(e.clientX, window.innerWidth - 200) + 'px';
  dropdown.style.top = Math.min(e.clientY, window.innerHeight - 200) + 'px';

  const close = (ev) => {
    if (!dropdown.contains(ev.target)) {
      dropdown.style.display = 'none';
      document.removeEventListener('click', close);
    }
  };
  setTimeout(() => document.addEventListener('click', close), 0);
}

function addToPlaylist(plId, filePath, fileName, type) {
  const pl = state.playlists.find(p => p.id === plId);
  if (!pl) return;
  pl.items.push({ path: filePath, name: fileName, type });
  savePlaylists();
  document.getElementById('add-to-playlist-dropdown').style.display = 'none';
  toast(`Added to "${pl.name}"`, 'success');
}

// ═══════════════════════════════════════════════════════════════
// DEVICES
// ═══════════════════════════════════════════════════════════════

async function scanDevices() {
  toast('Scanning for devices...', 'info');
  try {
    const data = await api('/api/devices');
    const select = document.getElementById('setting-device');
    select.innerHTML = (data.devices || []).map(d =>
      `<option value="${esc(d.name)}">${esc(d.name)} (${d.ip})</option>`
    ).join('');
    toast(`Found ${data.devices.length} device(s)`, 'success');
  } catch (e) { /* toasted */ }
}

async function selectDevice(name) {
  await api('/api/devices/select', { method: 'POST', body: { name } });
  document.getElementById('device-name').textContent = name;
  toast(`Device: ${name}`, 'info');
}

// ═══════════════════════════════════════════════════════════════
// SETTINGS & DATA
// ═══════════════════════════════════════════════════════════════

function clearHistory() {
  showConfirm('Clear History', 'Clear all watch history and saved positions?', () => {
    state.positions = {};
    state.history = [];
    savePositions();
    saveHistory();
    renderContinueWatching();
    toast('History cleared', 'info');
  });
}

function exportData() {
  const data = {
    positions: state.positions,
    history: state.history,
    playlists: state.playlists,
    queue: state.queue,
    settings: state.settings,
    exportedAt: new Date().toISOString(),
  };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `cast-manager-backup-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
  toast('Data exported', 'success');
}

function importData(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const data = JSON.parse(e.target.result);
      if (data.positions) { state.positions = data.positions; savePositions(); }
      if (data.history) { state.history = data.history; saveHistory(); }
      if (data.playlists) { state.playlists = data.playlists; savePlaylists(); }
      if (data.queue) { state.queue = data.queue; saveQueue(); }
      if (data.settings) { Object.assign(state.settings, data.settings); localStorage.setItem('cm_settings', JSON.stringify(state.settings)); }
      toast('Data imported successfully', 'success');
      renderContinueWatching();
    } catch (err) {
      toast('Invalid backup file', 'error');
    }
  };
  reader.readAsText(file);
  input.value = '';
}

// ═══════════════════════════════════════════════════════════════
// KEYBOARD SHORTCUTS
// ═══════════════════════════════════════════════════════════════

function setupKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    // Don't trigger when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    switch (e.key) {
      case ' ':
        e.preventDefault();
        togglePlayPause();
        break;
      case 'ArrowLeft':
        e.preventDefault();
        seekRelative(e.shiftKey ? -30 : -10);
        break;
      case 'ArrowRight':
        e.preventDefault();
        seekRelative(e.shiftKey ? 30 : 10);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setVolume(Math.min(100, state.casting.volume + 10));
        document.getElementById('volume-slider').value = Math.min(100, state.casting.volume + 10);
        break;
      case 'ArrowDown':
        e.preventDefault();
        setVolume(Math.max(0, state.casting.volume - 10));
        document.getElementById('volume-slider').value = Math.max(0, state.casting.volume - 10);
        break;
      case 'm':
      case 'M':
        toggleMute();
        break;
      case 'n':
      case 'N':
        if (state.queueIndex >= 0 && state.queueIndex < state.queue.length - 1) {
          playQueueItem(state.queueIndex + 1);
        }
        break;
      case 'p':
      case 'P':
        if (state.queueIndex > 0) {
          playQueueItem(state.queueIndex - 1);
        }
        break;
      case 'Escape':
        closeCastModal();
        closePlaylistModal();
        closeConfirm();
        break;
    }

    // Number keys 0-9 for seeking to percentage
    if (e.key >= '0' && e.key <= '9' && state.casting.duration > 0) {
      const pct = parseInt(e.key) * 10;
      seekTo(state.casting.duration * pct / 100);
    }
  });
}

// ═══════════════════════════════════════════════════════════════
// POLLING
// ═══════════════════════════════════════════════════════════════

function startPolling() {
  // Torrent polling when on torrents/home section
  setInterval(() => {
    if (state.currentSection === 'torrents' || state.currentSection === 'home') {
      loadTorrents();
    }
  }, 5000);

  // Page visibility - pause/resume polling
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopCastPolling();
    } else if (state.casting.state !== 'idle') {
      startCastPolling();
    }
  });
}

// ═══════════════════════════════════════════════════════════════
// RECENT FILES
// ═══════════════════════════════════════════════════════════════

async function loadRecent() {
  try {
    const data = await api('/api/files/recent?limit=50');
    const container = document.getElementById('recent-list');
    if (!data.files || data.files.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>No recent files yet.</p><p class="empty-hint">Files you open, stream, or upload will appear here.</p></div>';
      return;
    }
    container.innerHTML = data.files.map(f => {
      const name = f.filename || basename(f.file_path);
      const icon = f.file_type === 'video' ? '&#127916;' : f.file_type === 'audio' ? '&#127925;' : '&#128196;';
      const time = f.accessed_at ? new Date(f.accessed_at).toLocaleString() : '';
      const actionLabel = f.action === 'upload' ? 'Uploaded' : f.action === 'stream' ? 'Streamed' : 'Opened';
      return `<div class="file-item" onclick="navigateToFile('${esc(f.file_path)}')">
        <div class="file-icon">${icon}</div>
        <div class="file-info">
          <div class="file-name">${esc(name)}</div>
          <div class="file-meta"><span>${actionLabel}</span><span>${time}</span></div>
        </div>
      </div>`;
    }).join('');
  } catch (e) { /* toasted */ }
}

function navigateToFile(filePath) {
  const dir = filePath.substring(0, filePath.lastIndexOf('/'));
  showSection('library');
  loadFiles(dir);
}

// ═══════════════════════════════════════════════════════════════
// STARRED FILES
// ═══════════════════════════════════════════════════════════════

async function loadStarred() {
  try {
    const data = await api('/api/files/starred');
    const container = document.getElementById('starred-list');
    if (!data.files || data.files.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>No starred files.</p><p class="empty-hint">Click the ⭐ icon on any file to add it here.</p></div>';
      return;
    }
    container.innerHTML = data.files.map(f => {
      const name = basename(f.file_path);
      const time = f.starred_at ? new Date(f.starred_at).toLocaleString() : '';
      return `<div class="file-item" onclick="navigateToFile('${esc(f.file_path)}')">
        <div class="file-icon">⭐</div>
        <div class="file-info">
          <div class="file-name">${esc(name)}</div>
          <div class="file-meta"><span>Starred ${time}</span></div>
        </div>
        <div class="file-actions">
          <button onclick="event.stopPropagation(); toggleStar('${esc(f.file_path).replace(/'/g, "\\\\'")}')">Unstar</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) { /* toasted */ }
}

async function toggleStar(filePath) {
  try {
    // Try to unstar first; if that fails, star it
    try {
      await api('/api/files/star', { method: 'DELETE', body: { path: filePath } });
      toast('Removed from starred', 'info');
    } catch (e) {
      await api('/api/files/star', { method: 'POST', body: { path: filePath } });
      toast('Added to starred ⭐', 'success');
    }
    if (state.currentSection === 'starred') loadStarred();
  } catch (e) { /* toasted */ }
}

// ═══════════════════════════════════════════════════════════════
// SHARED FILES
// ═══════════════════════════════════════════════════════════════

let shareModalPath = '';

function openShareModal(filePath, fileName) {
  shareModalPath = filePath;
  document.getElementById('share-modal-filename').textContent = fileName || basename(filePath);
  document.getElementById('share-result').style.display = 'none';
  document.getElementById('share-password').value = '';
  document.getElementById('share-modal').style.display = 'flex';
}

function closeShareModal() {
  document.getElementById('share-modal').style.display = 'none';
  shareModalPath = '';
}

async function confirmShare() {
  if (!shareModalPath) return;
  const permissions = document.getElementById('share-permissions').value;
  const expiresIn = document.getElementById('share-expiry').value;
  const password = document.getElementById('share-password').value;

  try {
    const data = await api('/api/share', {
      method: 'POST',
      body: { path: shareModalPath, permissions, expiresIn: expiresIn ? parseInt(expiresIn) : null, password: password || null }
    });
    // Show result
    const result = document.getElementById('share-result');
    document.getElementById('share-url-input').value = data.shareUrl;
    result.style.display = 'block';
    // Load QR
    const qrDiv = document.getElementById('share-qr');
    try {
      const res = await fetch(`/api/qrcode?text=${encodeURIComponent(data.shareUrl)}`);
      qrDiv.innerHTML = await res.text();
    } catch (e) { qrDiv.innerHTML = ''; }
    toast('Share link created!', 'success');
  } catch (e) { toast('Failed to create share link', 'error'); }
}

async function loadShared() {
  try {
    const data = await api('/api/shares');
    const container = document.getElementById('shared-list');
    if (!data.shares || data.shares.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>No active shares.</p><p class="empty-hint">Share files using the Share button on any file.</p></div>';
      return;
    }
    container.innerHTML = data.shares.map(s => {
      const name = s.filename || basename(s.file_path);
      const expires = s.expires_at ? `Expires: ${new Date(s.expires_at).toLocaleString()}` : 'No expiration';
      const created = s.created_at ? new Date(s.created_at).toLocaleString() : '';
      return `<div class="file-item">
        <div class="file-icon">🔗</div>
        <div class="file-info">
          <div class="file-name">${esc(name)}</div>
          <div class="file-meta">
            <span>${s.permissions}</span>
            <span>${expires}</span>
            <span>${s.access_count} views</span>
            <span>Created ${created}</span>
          </div>
        </div>
        <div class="file-actions">
          <button onclick="navigator.clipboard.writeText(location.origin+'/s/${s.id}');toast('Link copied!','success')">Copy Link</button>
          <button onclick="revokeShareLink('${s.id}')" style="color:var(--color-danger)">Revoke</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) { /* toasted */ }
}

async function revokeShareLink(id) {
  showConfirm('Revoke Share', 'This will deactivate the share link. Continue?', async () => {
    try {
      await api(`/api/shares/${id}`, { method: 'DELETE' });
      toast('Share revoked', 'success');
      loadShared();
    } catch (e) { /* toasted */ }
  });
}

// ═══════════════════════════════════════════════════════════════
// TRASH
// ═══════════════════════════════════════════════════════════════

async function loadTrash() {
  try {
    const data = await api('/api/files/trash');
    const container = document.getElementById('trash-list');
    if (!data.files || data.files.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>Trash is empty.</p><p class="empty-hint">Deleted files will appear here for 30 days.</p></div>';
      return;
    }
    container.innerHTML = data.files.map(f => {
      const icon = f.file_type === 'video' ? '&#127916;' : f.file_type === 'audio' ? '&#127925;' : '&#128196;';
      const deleted = f.deleted_at ? new Date(f.deleted_at).toLocaleString() : '';
      const autoDelete = f.auto_delete_at ? new Date(f.auto_delete_at).toLocaleDateString() : '';
      return `<div class="file-item">
        <div class="file-icon">${icon}</div>
        <div class="file-info">
          <div class="file-name">${esc(f.filename)}</div>
          <div class="file-meta">
            <span>${formatBytes(f.size)}</span>
            <span>Deleted ${deleted}</span>
            ${autoDelete ? `<span>Auto-deletes ${autoDelete}</span>` : ''}
          </div>
          <div class="file-meta" style="font-size:10px;color:var(--color-text-muted)">${esc(f.original_path)}</div>
        </div>
        <div class="file-actions">
          <button onclick="restoreFromTrash(${f.id})" style="color:var(--color-accent)">Restore</button>
          <button onclick="permanentDeleteTrash(${f.id})" style="color:var(--color-danger)">Delete Forever</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) { /* toasted */ }
}

async function restoreFromTrash(id) {
  try {
    const data = await api('/api/files/restore', { method: 'POST', body: { id } });
    toast(`Restored to ${basename(data.restoredPath || '')}`, 'success');
    loadTrash();
  } catch (e) { /* toasted */ }
}

async function permanentDeleteTrash(id) {
  showPasswordPrompt('Delete Forever', 'Enter sudo password to permanently delete this file:', async (sudoPwd) => {
    try {
      await api(`/api/files/trash/${id}`, { method: 'DELETE', body: { sudoPwd } });
      toast('Permanently deleted', 'success');
      loadTrash();
    } catch (e) { /* toasted */ }
  });
}

function emptyTrash() {
  showPasswordPrompt('Empty Trash', 'Enter sudo password to empty all trash items:', async (sudoPwd) => {
    try {
      const data = await api('/api/files/trash/empty', { method: 'DELETE', body: { sudoPwd } });
      toast(`Trash emptied (${data.deleted} items deleted)`, 'success');
      loadTrash();
    } catch (e) { /* toasted */ }
  });
}

// ═══════════════════════════════════════════════════════════════
// STORAGE DASHBOARD
// ═══════════════════════════════════════════════════════════════

async function loadStorage() {
  try {
    const data = await api('/api/storage/stats');
    const container = document.getElementById('storage-info');

    const usedPct = data.totalSpace > 0 ? ((data.usedSpace / data.totalSpace) * 100).toFixed(1) : 0;
    const colors = {
      video: '#ef4444', audio: '#3b82f6', images: '#10b981',
      documents: '#f59e0b', archives: '#8b5cf6', other: '#6b7280'
    };

    let breakdownHtml = '';
    if (data.breakdown) {
      breakdownHtml = Object.entries(data.breakdown).map(([key, val]) => {
        if (val === 0) return '';
        const color = colors[key] || '#6b7280';
        const pct = data.usedSpace > 0 ? ((val / data.usedSpace) * 100).toFixed(1) : 0;
        return `<div style="display:flex;align-items:center;gap:8px;padding:6px 0">
          <div style="width:12px;height:12px;border-radius:3px;background:${color};flex-shrink:0"></div>
          <span style="flex:1;text-transform:capitalize">${key}</span>
          <span style="color:var(--color-text-muted);margin-right:8px">${pct}%</span>
          <span style="min-width:80px;text-align:right">${formatBytes(val)}</span>
        </div>`;
      }).join('');
    }

    container.innerHTML = `
      <div style="margin-bottom:24px">
        <div style="display:flex;justify-content:space-between;margin-bottom:8px">
          <span style="font-weight:600">Storage Usage</span>
          <span>${usedPct}% used</span>
        </div>
        <div style="height:24px;border-radius:12px;background:var(--color-bg-primary);overflow:hidden;position:relative">
          <div style="height:100%;width:${usedPct}%;background:linear-gradient(90deg,#3b82f6,#8b5cf6);border-radius:12px;transition:width 0.5s"></div>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:8px;font-size:13px;color:var(--color-text-muted)">
          <span>${formatBytes(data.usedSpace)} used</span>
          <span>${formatBytes(data.freeSpace)} free</span>
          <span>${formatBytes(data.totalSpace)} total</span>
        </div>
      </div>
      <h3 style="margin-bottom:12px;font-size:14px;font-weight:600">Breakdown by Type</h3>
      ${breakdownHtml}
      <div style="margin-top:24px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <h3 style="font-size:14px;font-weight:600">Directory Usage</h3>
          <span style="font-size:11px;color:var(--color-text-muted)">Click folders to drill down</span>
        </div>
        <div id="dir-usage-area">
          <div class="empty-state"><p>Loading directory sizes...</p></div>
        </div>
      </div>
    `;

    // Load directory usage
    loadDirUsage();
  } catch (e) {
    document.getElementById('storage-info').innerHTML = '<div class="empty-state"><p>Could not load storage info.</p></div>';
  }
}

let dirUsagePath = null;

async function loadDirUsage(dirPath) {
  const container = document.getElementById('dir-usage-area');
  if (!container) return;
  container.innerHTML = '<div style="padding:12px;color:var(--color-text-muted);font-size:13px">Scanning directories...</div>';

  try {
    const url = dirPath ? `/api/storage/dirs?path=${encodeURIComponent(dirPath)}` : '/api/storage/dirs';
    const data = await api(url);
    dirUsagePath = data.currentPath;

    if (!data.dirs || data.dirs.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>No subdirectories.</p></div>';
      return;
    }

    const maxSize = data.totalSize || data.dirs[0].size || 1;

    // Breadcrumb for navigation
    let breadcrumb = '';
    if (data.currentPath && data.parentPath) {
      const parts = data.currentPath.split('/').filter(Boolean);
      let acc = '';
      breadcrumb = '<div style="display:flex;align-items:center;gap:4px;margin-bottom:12px;font-size:12px;flex-wrap:wrap">';
      for (let i = 0; i < parts.length; i++) {
        acc += '/' + parts[i];
        const isLast = i === parts.length - 1;
        if (isLast) {
          breadcrumb += `<span style="color:var(--color-text-primary);font-weight:500">${esc(parts[i])}</span>`;
        } else {
          const p = acc;
          breadcrumb += `<button onclick="loadDirUsage('${esc(p)}')" style="background:none;border:none;color:var(--color-accent);cursor:pointer;font-size:12px;font-family:inherit;padding:0">${esc(parts[i])}</button><span style="color:var(--color-text-hint)">/</span>`;
        }
      }
      breadcrumb += '</div>';
    }

    let html = breadcrumb;

    // Total size header
    html += `<div style="padding:8px 0;margin-bottom:8px;border-bottom:1px solid var(--color-border);font-size:13px">
      <strong>${formatBytes(data.totalSize)}</strong> total in this directory`;
    if (data.filesSize > 0) {
      html += ` &middot; <span style="color:var(--color-text-muted)">${formatBytes(data.filesSize)} in files (not in subdirs)</span>`;
    }
    html += '</div>';

    // Directory list
    html += data.dirs.map(d => {
      const pct = maxSize > 0 ? ((d.size / maxSize) * 100).toFixed(1) : 0;
      const barWidth = Math.max(1, Math.min(100, parseFloat(pct)));
      const escapedPath = esc(d.path).replace(/'/g, "\\'");
      const escapedName = esc(d.name).replace(/'/g, "\\'");
      return `<div style="display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--color-border)">
        <div style="width:24px;text-align:center;font-size:16px;flex-shrink:0">📁</div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            <button onclick="loadDirUsage('${escapedPath}')" style="background:none;border:none;color:var(--color-text-primary);font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;padding:0;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="Drill into ${esc(d.name)}">${esc(d.name)}</button>
            <span style="font-size:11px;color:var(--color-text-muted);flex-shrink:0">${d.itemCount !== undefined ? d.itemCount + ' items' : ''}</span>
          </div>
          <div style="height:6px;border-radius:3px;background:var(--color-bg-primary);overflow:hidden">
            <div style="height:100%;width:${barWidth}%;background:${barWidth > 60 ? '#ef4444' : barWidth > 30 ? '#f59e0b' : '#3b82f6'};border-radius:3px;transition:width 0.3s"></div>
          </div>
        </div>
        <div style="min-width:80px;text-align:right;font-size:13px;font-weight:500;flex-shrink:0">${formatBytes(d.size)}</div>
        <div style="display:flex;gap:4px;flex-shrink:0">
          <button onclick="event.stopPropagation();showSection('library');loadFiles('${escapedPath}')" style="padding:3px 8px;font-size:11px;border:1px solid var(--color-border);background:var(--color-surface);color:var(--color-text-secondary);border-radius:4px;cursor:pointer;font-family:inherit" title="Open in file browser">Open</button>
          <button onclick="event.stopPropagation();deleteFile('${escapedPath}','${escapedName}')" style="padding:3px 8px;font-size:11px;border:1px solid var(--color-border);background:var(--color-surface);color:var(--color-danger);border-radius:4px;cursor:pointer;font-family:inherit" title="Delete this folder">Delete</button>
        </div>
      </div>`;
    }).join('');

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = '<div class="empty-state"><p>Could not load directory usage.</p></div>';
  }
}

// ═══════════════════════════════════════════════════════════════
// ACTIVITY LOG
// ═══════════════════════════════════════════════════════════════

async function loadActivityLog() {
  try {
    const data = await api('/api/activity');
    const container = document.getElementById('activity-list');
    if (!data.activities || data.activities.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>No activity yet.</p></div>';
      return;
    }
    const icons = {
      star: '⭐', unstar: '☆', trash: '🗑️', restore: '♻️',
      delete_permanent: '❌', share_created: '🔗', share_accessed: '👁️',
      share_revoked: '🚫', upload: '📤', search: '🔍', tag: '🏷️',
      stream_url_generated: '🔗', empty_trash: '🗑️',
    };
    container.innerHTML = data.activities.map(a => {
      const icon = icons[a.action] || '📋';
      const time = a.created_at ? new Date(a.created_at).toLocaleString() : '';
      const name = a.file_path ? basename(a.file_path) : '';
      let detail = '';
      if (a.details) {
        try { const d = JSON.parse(a.details); detail = Object.entries(d).map(([k,v]) => `${k}: ${v}`).join(', '); } catch(_) {}
      }
      return `<div style="display:flex;align-items:flex-start;gap:12px;padding:10px 0;border-bottom:1px solid var(--color-border)">
        <span style="font-size:18px">${icon}</span>
        <div style="flex:1">
          <div style="font-size:13px"><strong>${a.action.replace(/_/g, ' ')}</strong>${name ? ` — ${esc(name)}` : ''}</div>
          ${detail ? `<div style="font-size:11px;color:var(--color-text-muted)">${esc(detail)}</div>` : ''}
        </div>
        <span style="font-size:11px;color:var(--color-text-muted);white-space:nowrap">${time}</span>
      </div>`;
    }).join('');
  } catch (e) { /* toasted */ }
}

// ═══════════════════════════════════════════════════════════════
// FILE UPLOAD (Drag & Drop)
// ═══════════════════════════════════════════════════════════════

(function setupUpload() {
  let dragCounter = 0;

  document.addEventListener('dragenter', (e) => {
    e.preventDefault();
    dragCounter++;
    if (state.currentSection === 'library') {
      document.getElementById('upload-overlay').style.display = 'flex';
    }
  });

  document.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) {
      dragCounter = 0;
      document.getElementById('upload-overlay').style.display = 'none';
    }
  });

  document.addEventListener('dragover', (e) => e.preventDefault());

  document.addEventListener('drop', async (e) => {
    e.preventDefault();
    dragCounter = 0;
    document.getElementById('upload-overlay').style.display = 'none';

    if (state.currentSection !== 'library') return;

    const files = e.dataTransfer.files;
    if (!files || files.length === 0) return;

    // Check for torrent files first
    const torrentFiles = [...files].filter(f => f.name.endsWith('.torrent'));
    const regularFiles = [...files].filter(f => !f.name.endsWith('.torrent'));

    // Handle torrent files
    for (const tf of torrentFiles) {
      await uploadTorrentFile(tf);
    }

    // Handle regular file uploads
    if (regularFiles.length > 0) {
      await uploadFiles(regularFiles);
    }
  });
})();

async function uploadFiles(files) {
  const panel = document.getElementById('upload-progress');
  const list = document.getElementById('upload-progress-list');
  panel.style.display = 'block';

  list.innerHTML = [...files].map((f, i) => `
    <div id="upload-item-${i}" style="display:flex;align-items:center;gap:8px;padding:6px 12px;font-size:13px">
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(f.name)}</span>
      <span class="upload-status" style="color:var(--color-text-muted)">Uploading...</span>
    </div>
  `).join('');

  const formData = new FormData();
  formData.append('path', state.currentPath);
  for (const file of files) {
    formData.append('files', file);
  }

  try {
    const res = await fetch('/api/files/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.results) {
      data.results.forEach((r, i) => {
        const item = document.getElementById(`upload-item-${i}`);
        if (item) {
          const status = item.querySelector('.upload-status');
          status.textContent = r.success ? '✓' : '✗ ' + (r.error || 'Failed');
          status.style.color = r.success ? 'var(--color-accent)' : 'var(--color-danger)';
        }
      });
      const successes = data.results.filter(r => r.success).length;
      toast(`Uploaded ${successes}/${data.results.length} file(s)`, successes > 0 ? 'success' : 'error');
      loadFiles(); // Refresh file list
    }
  } catch (e) {
    toast('Upload failed: ' + e.message, 'error');
    list.innerHTML = '<div style="padding:8px 12px;color:var(--color-danger)">Upload failed</div>';
  }

  // Auto-hide panel after 5s
  setTimeout(() => { panel.style.display = 'none'; }, 5000);
}

// ═══════════════════════════════════════════════════════════════
// GLOBAL SEARCH
// ═══════════════════════════════════════════════════════════════

let searchTimeout = null;

async function globalSearch(query) {
  if (!query || query.length < 2) return;
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
    if (data.results && data.results.length > 0) {
      // Show results - for now, navigate to library with search results
      state.files = data.results.map(r => ({
        name: r.name,
        path: r.path,
        type: getFileType(r.extension || r.name),
        ext: r.extension || '',
        size: r.size || 0,
      }));
      showSection('library');
      renderFiles();
      toast(`Found ${data.results.length} result(s)`, 'info');
    } else {
      toast('No results found', 'info');
    }
  } catch (e) { /* toasted */ }
}

function getFileType(ext) {
  if (!ext) return 'other';
  ext = ext.toLowerCase();
  if (!ext.startsWith('.')) ext = '.' + ext;
  const videoExts = ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v', '.ts', '.flv', '.wmv'];
  const audioExts = ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus', '.wma'];
  if (videoExts.includes(ext)) return 'video';
  if (audioExts.includes(ext)) return 'audio';
  return 'other';
}

// ═══════════════════════════════════════════════════════════════
// WEBSOCKET NOTIFICATIONS
// ═══════════════════════════════════════════════════════════════

let ws = null;
let wsReconnectTimer = null;

function connectWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/api/ws/notifications`);

  ws.onopen = () => {
    console.log('[WS] Connected');
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleNotification(data);
    } catch (e) { /* ignore */ }
  };

  ws.onclose = () => {
    console.log('[WS] Disconnected, reconnecting in 5s...');
    wsReconnectTimer = setTimeout(connectWebSocket, 5000);
  };

  ws.onerror = () => {
    ws.close();
  };
}

function handleNotification(data) {
  switch (data.type) {
    case 'upload_complete':
      toast(`Upload complete (${data.count} files)`, 'success');
      if (state.currentSection === 'library') loadFiles();
      break;
    case 'transcode_complete':
      toast('Transcoding complete!', 'success');
      break;
    case 'share_accessed':
      toast(`Someone accessed your shared file`, 'info');
      break;
    case 'low_storage':
      toast('⚠️ Server storage is running low!', 'warning', 10000);
      break;
    default:
      if (data.message) toast(data.message, 'info');
  }
}

// Start WebSocket on load
connectWebSocket();

// ═══════════════════════════════════════════════════════════════
// HELPER: basename
// ═══════════════════════════════════════════════════════════════
// Ensure basename is available as a global (it may already be defined)
if (typeof basename === 'undefined') {
  function basename(p) { return p ? p.split('/').pop() : ''; }
}
