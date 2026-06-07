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
  visibleFiles: [],
  starredPaths: new Set(),
  starredFolders: [],
  castDevices: [],
  selectedCastDevice: null,
  receiverStatus: null,
  currentPath: '',
  parentPath: '',
  fileRoot: '',
  fileRootLabel: 'Workspace',
  fileBreadcrumbs: [],
  fileSudoPwd: '',
  fileFilter: 'all',
  fileSearch: '',
  fileSort: 'name',
  showHidden: false,
  recursiveSearch: false,
  recursiveSearchResults: [],
  recursiveSearchLoading: false,
  recursiveSearchTimer: null,
  storageSummaryLoaded: false,
  libraryView: 'list',
  selectedFile: null,
  selectedFilePaths: new Set(),
  preview: {
    filePath: '',
    content: '',
    originalContent: '',
    unsaved: false,
    loading: false,
  },
  fileHistoryBack: [],
  fileHistoryForward: [],
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
    lastStatusAt: 0,
    consecutiveStatusFailures: 0,
    receiverReachable: true,
    lastNormalizedStatus: null,
    lastStopAt: 0,
    lastUserCommand: null, // "seek" | "pause" | "resume" | "stop" | "start"
    lastCommandId: 0,
    lastCommandAt: 0,
    lastSeekAt: 0,
  },
  castModalFile: null,
  settings: {
    autoTranscode: 'auto',
    autoAdvance: true,
    saveInterval: 30,
    defaultView: 'list',
    /** When true, Chromecast may use slow disk pre-transcode instead of live transcode (opt-in). Live full-transcode NVENC vs CPU is server-side (`CAST_LIVE_TRANSCODE_ENCODER` on cast-manager). */
    allowPretranscode: false,
  },
  pollingTimers: {},
  isDraggingScrubber: false,
  castControl: {
    pending: false,
    pendingAction: null,
    pendingStartedAt: 0,
    settleUntil: 0,
    lastCommandAt: 0,
    lastCommandAction: null,
    ignoreNextScrubberClickUntil: 0,
    idleStreak: 0,
    lastNonIdleAt: 0,
    lastKnownTime: 0,
    lastKnownDuration: 0,
    seekDebounceTimer: null,
    seekInFlight: false,
    seekQueued: false,
    seekSettleMs: 1200,
    lastSeekTarget: null,
  },
  fileLoadRequestId: 0,
  skipNextLibraryAutoLoad: false,
};

// ─── Init ───────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadPersistedData();
  state.showHidden = localStorage.getItem('cm_show_hidden') === 'true';
  state.recursiveSearch = localStorage.getItem('cm_recursive_search') === 'true';
  state.fileSort = localStorage.getItem('cm_file_sort') || state.fileSort;
  state.libraryView = localStorage.getItem('cm_file_view') || state.settings.defaultView || state.libraryView;
  showSection(window.location.pathname === '/file-manager' ? 'library' : 'home');
  startPolling();
  setupKeyboardShortcuts();
  loadDiskInfo();
  renderContinueWatching();
  loadTorrents();
  loadStarredFolders();
  loadCastDevices();
  loadReceiverStatus();
  checkInitialCastStatus();
});

window.addEventListener('beforeunload', (e) => {
  if (!state.preview.unsaved) return;
  e.preventDefault();
  e.returnValue = '';
});

// ─── Cast control transaction helpers ───────────────────────
function nowMs() { return Date.now(); }

function beginCastCommand(action, settleMs = 1200) {
  const t = nowMs();
  state.castControl.pending = true;
  state.castControl.pendingAction = action;
  state.castControl.pendingStartedAt = t;
  state.castControl.settleUntil = t + settleMs;
  state.castControl.lastCommandAt = t;
  state.castControl.lastCommandAction = action;
}

function endCastCommand() {
  state.castControl.pending = false;
  state.castControl.pendingAction = null;
  state.castControl.pendingStartedAt = 0;
}

function shouldIgnorePollUpdate() {
  const t = nowMs();
  if (state.castControl.pending) return true;
  if (t < state.castControl.settleUntil) return true;
  return false;
}

function markCastCommand(command) {
  state.casting.lastUserCommand = command;
  state.casting.lastCommandId = (state.casting.lastCommandId || 0) + 1;
  state.casting.lastCommandAt = nowMs();
  if (command === 'seek') state.casting.lastSeekAt = nowMs();
  if (command === 'stop') state.casting.lastStopAt = nowMs();
  return state.casting.lastCommandId;
}

function isCurrentCastCommand(commandId) {
  return commandId === state.casting.lastCommandId;
}

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
function showSection(name, options = {}) {
  if (state.currentSection === 'library' && name !== 'library' && state.preview.unsaved && !confirm('Discard unsaved changes?')) return;
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
  if (name === 'library') {
    if (options.skipLoad || state.skipNextLibraryAutoLoad) state.skipNextLibraryAutoLoad = false;
    else loadFiles();
  }
  if (name === 'queue') renderQueue();
  if (name === 'playlists') renderPlaylists();
  if (name === 'recent') loadRecent();
  if (name === 'starred') { loadStarred(); loadStarredFolders(); }
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
  btn.textContent = 'OK';
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
  const { body, headers, silent, ...fetchOptions } = options;
  try {
    const res = await fetch(url, {
      ...fetchOptions,
      headers: { 'Content-Type': 'application/json', ...(headers || {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) {
      const err = new Error(data.errorDetails?.message || data.error || `Request failed with ${res.status}`);
      err.code = data.errorDetails?.code || data.code || '';
      err.status = res.status;
      throw err;
    }
    return data;
  } catch (err) {
    if (!silent) toast(`Request failed: ${err.message}`, 'error');
    throw err;
  }
}

function sudoHeaders(sudoPwd = state.fileSudoPwd) {
  return sudoPwd ? { 'X-Sudo-Password': sudoPwd } : {};
}

function withSudoBody(body, sudoPwd = state.fileSudoPwd) {
  return sudoPwd ? { ...body, sudoPwd } : body;
}

function askForSudo(title, message) {
  return new Promise((resolve, reject) => {
    showPasswordPrompt(title, message, (sudoPwd) => {
      const value = String(sudoPwd || '');
      if (!value) {
        reject(new Error('Password is required to retry this operation.'));
        return;
      }
      state.fileSudoPwd = value;
      resolve(value);
    });
  });
}

async function withPermissionRetry(title, message, requestFn) {
  try {
    return await requestFn(state.fileSudoPwd);
  } catch (err) {
    if (err.code !== 'PERMISSION_DENIED') {
      toast(`Request failed: ${err.message}`, 'error');
      throw err;
    }
    try {
      const sudoPwd = await askForSudo(title, message);
      return await requestFn(sudoPwd);
    } catch (retryErr) {
      toast(`Request failed: ${retryErr.message}`, 'error');
      throw retryErr;
    }
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

  state.visibleFiles = filtered;
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

async function loadFiles(targetPath, options = {}) {
  const requestId = ++state.fileLoadRequestId;
  if (!targetPath && !state.currentPath) {
    targetPath = localStorage.getItem('cm_last_library_path') || state.fileRoot || '';
  }
  const pathChanged = targetPath && targetPath !== state.currentPath;
  if (pathChanged && state.preview.unsaved && !confirm('Discard unsaved changes?')) return;
  if (pathChanged && options.addHistory !== false && state.currentPath) {
    state.fileHistoryBack.push(state.currentPath);
    state.fileHistoryForward = [];
  }
  if (targetPath) state.currentPath = targetPath;

  const container = document.getElementById('file-list');
  if (container) container.innerHTML = '<div class="empty-state"><p>Loading files...</p></div>';

  try {
    const params = new URLSearchParams();
    if (state.currentPath) params.set('path', state.currentPath);
    params.set('showHidden', state.showHidden ? 'true' : 'false');
    const data = await withPermissionRetry(
      'Folder Access',
      `Enter the server sudo password to open ${state.currentPath || 'this folder'}.`,
      (sudoPwd) => api(`/api/files?${params.toString()}`, { headers: sudoHeaders(sudoPwd), silent: true })
    );
    if (requestId !== state.fileLoadRequestId) return;
    state.files = data.files || [];
    for (const f of state.files) {
      if (f.starred) state.starredPaths.add(f.path);
      else state.starredPaths.delete(f.path);
    }
    state.currentPath = data.currentPath || state.currentPath;
    state.parentPath = data.parentPath || state.currentPath;
    state.fileRoot = data.rootPath || state.fileRoot || state.currentPath;
    state.fileRootLabel = data.rootLabel || state.fileRootLabel || 'Workspace';
    state.fileBreadcrumbs = data.breadcrumbs || [];
    localStorage.setItem('cm_last_library_path', state.currentPath || '');

    if (state.selectedFile && !state.files.some(f => f.path === state.selectedFile.path)) {
      clearFileSelection();
    }
    if (state.selectedFilePaths.size) {
      const currentPaths = new Set(state.files.map(f => f.path));
      state.selectedFilePaths = new Set([...state.selectedFilePaths].filter(path => currentPaths.has(path)));
    }

    renderFileChrome();
    renderBreadcrumb();
    renderFiles();
    renderSelectionBar();
    loadFileStorageSummary();
  } catch (e) {
    if (requestId !== state.fileLoadRequestId) return;
    if (container) {
      container.innerHTML = `<div class="empty-state"><p>Could not load this folder.</p><p class="empty-hint">${esc(e.message)}</p></div>`;
    }
    renderFileChrome();
  }
}

function renderFileChrome() {
  const label = document.getElementById('file-current-label');
  if (label) {
    const rel = relativeToRoot(state.currentPath);
    label.textContent = rel
      ? `${state.fileRootLabel}: /${rel}`
      : `${state.fileRootLabel}: ${state.fileRoot || ''}`;
  }
  const hiddenToggle = document.getElementById('show-hidden-toggle');
  if (hiddenToggle) hiddenToggle.checked = state.showHidden;
  const recursiveToggle = document.getElementById('recursive-search-toggle');
  if (recursiveToggle) recursiveToggle.checked = state.recursiveSearch;
  const sortSelect = document.getElementById('file-sort-select');
  if (sortSelect) sortSelect.value = state.fileSort;
  const delBtn = document.getElementById('btn-delete-folder');
  const delNowBtn = document.getElementById('btn-delete-folder-now');
  if (delBtn) delBtn.style.display = state.currentPath && state.currentPath !== state.fileRoot ? 'inline-block' : 'none';
  if (delNowBtn) delNowBtn.style.display = state.currentPath && state.currentPath !== state.fileRoot ? 'inline-block' : 'none';
  const backBtn = document.getElementById('file-back-btn');
  const forwardBtn = document.getElementById('file-forward-btn');
  const upBtn = document.getElementById('file-up-btn');
  if (backBtn) backBtn.disabled = state.fileHistoryBack.length === 0;
  if (forwardBtn) forwardBtn.disabled = state.fileHistoryForward.length === 0;
  if (upBtn) upBtn.disabled = !state.currentPath || state.currentPath === state.fileRoot;
}

function renderBreadcrumb() {
  const container = document.getElementById('breadcrumb');
  if (!container) return;
  const crumbs = state.fileBreadcrumbs.length
    ? state.fileBreadcrumbs
    : [{ label: state.fileRootLabel, path: state.fileRoot || state.currentPath }];
  container.innerHTML = crumbs.map((crumb, i) => {
    const isLast = i === crumbs.length - 1;
    const p = jsArg(crumb.path);
    const label = esc(crumb.label || state.fileRootLabel);
    if (isLast) return `<span class="breadcrumb-item" style="color:var(--color-text-primary);font-weight:500">${label}</span>`;
    return `<button class="breadcrumb-item" onclick="loadFiles('${p}')">${label}</button><span class="breadcrumb-sep">/</span>`;
  }).join('');
}

function renderFiles() {
  const container = document.getElementById('file-list');
  if (!container) return;
  if (state.recursiveSearch && state.fileSearch && state.fileSearch.length >= 2 && state.recursiveSearchLoading) {
    container.innerHTML = '<div class="empty-state"><p>Searching all folders...</p></div>';
    return;
  }
  const usingRecursiveResults = state.recursiveSearch && state.fileSearch && state.fileSearch.length >= 2;
  let filtered = usingRecursiveResults ? [...state.recursiveSearchResults] : [...state.files];

  if (state.fileFilter !== 'all') {
    filtered = filtered.filter(f => {
      if (f.type === 'folder') return true;
      if (state.fileFilter === 'other') return !['video', 'audio'].includes(f.type);
      return f.type === state.fileFilter;
    });
  }
  if (state.fileSearch && !usingRecursiveResults) {
    const q = state.fileSearch.toLowerCase();
    filtered = filtered.filter(f => f.name.toLowerCase().includes(q) || (f.relativePath || '').toLowerCase().includes(q));
  }

  filtered.sort((a, b) => {
    if (a.type === 'folder' && b.type !== 'folder') return -1;
    if (a.type !== 'folder' && b.type === 'folder') return 1;
    switch (state.fileSort) {
      case 'size': return (b.size || 0) - (a.size || 0);
      case 'date': return (b.mtime || 0) - (a.mtime || 0);
      case 'type': return (a.ext || a.type || '').localeCompare(b.ext || b.type || '') || a.name.localeCompare(b.name);
      default: return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    }
  });

  const isGrid = state.libraryView === 'grid';
  container.className = `file-list ${isGrid ? 'grid-view' : 'list-view'}`;

  if (filtered.length === 0) {
    const message = state.fileSearch
      ? (usingRecursiveResults ? 'No matching files in storage.' : 'No matching files in this folder.')
      : 'This folder is empty.';
    container.innerHTML = `<div class="empty-state"><p>${message}</p><p class="empty-hint">${state.showHidden ? 'Hidden files are currently visible.' : 'Use Show hidden files to include dotfiles.'}</p></div>`;
    return;
  }

  container.innerHTML = filtered.map(renderFileItem).join('');
  if (isGrid) loadThumbnails(filtered);
}

function renderFileItem(f) {
  const icon = fileIcon(f);
  const pos = state.positions[fileHash(f.path)];
  const progressHtml = pos && pos.duration > 0
    ? `<div class="file-progress-bar" style="width:${Math.min(100, (pos.position / pos.duration) * 100)}%"></div>`
    : '';
  const escapedPath = jsArg(f.path);
  const escapedName = jsArg(f.name);
  const selected = state.selectedFile?.path === f.path || state.selectedFilePaths.has(f.path) ? ' selected' : '';
  const hidden = f.isHidden || f.hidden ? ' hidden-file' : '';
  const protectedClass = f.protected ? ' protected-file' : '';
  const modified = f.modifiedAt ? new Date(f.modifiedAt).toLocaleString() : (f.mtime ? new Date(f.mtime * 1000).toLocaleString() : '-');
  const typeLabel = f.type === 'folder' ? 'Folder' : (f.ext || f.type || 'File');
  const hiddenBadge = f.isHidden || f.hidden ? '<span class="badge">hidden</span>' : '';
  const protectedBadge = f.protected ? '<span class="badge warning">protected</span>' : '';
  const searchPath = f.searchResult && f.relativePath ? `<span title="${esc(f.relativePath)}">${esc(f.relativePath)}</span>` : '';
  const folderSize = f.sizeUnavailable ? 'Size unavailable' : formatBytes(f.size || 0);
  const folderItems = f.itemCount !== undefined ? `${f.itemCount} items` : 'Folder';
  const isStarredItem = !!f.starred || state.starredPaths.has(f.path);
  const starIcon = isStarredItem ? '&#9733;' : '&#9734;';
  const starTitle = isStarredItem ? 'Unstar' : 'Star';

  const mediaActions = (f.type === 'video' || f.type === 'audio') && !f.protected
    ? `<button onclick="event.stopPropagation(); openStreamPlayer('${escapedPath}', '${f.type}')" style="color:var(--color-accent)">&#9654; Play</button>
       <button onclick="event.stopPropagation(); showStreamUrlModal('${escapedPath}')" title="Generate shareable stream URL">Stream URL</button>
       <button onclick="event.stopPropagation(); openCastModal('${escapedPath}', '${f.type}')">Cast</button>
       <button onclick="event.stopPropagation(); addToQueue('${escapedPath}', '${escapedName}', '${f.type}')">Queue</button>
       <button onclick="event.stopPropagation(); showPlaylistDropdown(event, '${escapedPath}', '${escapedName}', '${f.type}')">Playlist</button>`
    : '';
  const downloadAction = f.type !== 'folder' && !f.protected
    ? `<button onclick="event.stopPropagation(); downloadFile('${escapedPath}')">Download</button>`
    : '';
  const shareAction = f.type !== 'folder' && !f.protected
    ? `<button onclick="event.stopPropagation(); openShareModal('${escapedPath}', '${escapedName}')" title="Share">Share</button>`
    : '';
  const openAction = f.type === 'folder'
    ? `<button onclick="event.stopPropagation(); openFolder('${escapedPath}', ${f.searchResult ? 'true' : 'false'})">Open</button>`
    : `<button onclick="event.stopPropagation(); selectFile('${escapedPath}')">Preview</button>`;
  const containingAction = f.searchResult && f.type !== 'folder'
    ? `<button onclick="event.stopPropagation(); navigateToFile('${escapedPath}')">Open Folder</button>`
    : '';

  const actions = `<div class="file-actions">
      ${openAction}
      ${containingAction}
      ${mediaActions}
      <button onclick="event.stopPropagation(); copyFileAddress('${escapedPath}')">Copy Address</button>
      <button onclick="event.stopPropagation(); toggleStar('${escapedPath}', '${f.type}')" title="${starTitle}">${starIcon}</button>
      ${shareAction}
      ${downloadAction}
      <button onclick="event.stopPropagation(); renameFile('${escapedPath}', '${escapedName}')">Rename</button>
      <button onclick="event.stopPropagation(); copyFile('${escapedPath}', '${escapedName}')">Copy</button>
      <button onclick="event.stopPropagation(); moveFile('${escapedPath}', '${escapedName}')">Move</button>
      <button onclick="event.stopPropagation(); deleteFile('${escapedPath}', '${escapedName}')" style="color:var(--color-danger)">Trash</button>
      <button onclick="event.stopPropagation(); deleteFileNow('${escapedPath}', '${escapedName}')" style="color:var(--color-danger)">Delete Now</button>
    </div>`;

  const clickAction = f.type === 'folder'
    ? `onclick="openFolder('${escapedPath}', ${f.searchResult ? 'true' : 'false'})" ondblclick="openFolder('${escapedPath}', ${f.searchResult ? 'true' : 'false'})"`
    : `onclick="selectFile('${escapedPath}')" ondblclick="openSelectedFile('${escapedPath}')"`;

  return `<div class="file-item${selected}${hidden}${protectedClass}" data-file-hash="${fileHash(f.path)}" ${clickAction}>
    <div class="file-item-cell">
      <div class="file-thumb-wrapper">
        <div class="file-thumb-placeholder">${icon}</div>
        <img class="file-thumb" data-src="" loading="lazy" onerror="this.style.display='none'">
        ${progressHtml}
      </div>
      <input type="checkbox" class="file-select-checkbox" ${state.selectedFilePaths.has(f.path) ? 'checked' : ''} onclick="event.stopPropagation(); toggleFileSelection('${escapedPath}', this.checked)" title="Select item">
      <div class="file-icon">${icon}</div>
      <div class="file-info">
        <div class="file-name${f.type === 'folder' ? ' folder' : ''}" title="${esc(f.name)}">${esc(f.name)}</div>
        <div class="file-meta">
          ${f.type === 'folder' ? `<span>${esc(folderSize)}</span><span>${esc(folderItems)}</span>` : `<span>${formatBytes(f.size)}</span>`}
          ${searchPath}
          ${hiddenBadge}${protectedBadge}
        </div>
      </div>
    </div>
    <div class="file-col">${esc(typeLabel)}</div>
    <div class="file-col">${f.type === 'folder' ? esc(folderSize) : formatBytes(f.size)}</div>
    <div class="file-col" title="${esc(modified)}">${esc(modified)}</div>
    ${actions}
  </div>`;
}

function jsArg(value) {
  return esc(String(value || '')).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function fileIcon(f) {
  if (f.type === 'folder') return '&#128193;';
  if (f.type === 'video') return '&#127916;';
  if (f.type === 'audio') return '&#127925;';
  if (f.type === 'image') return '&#128247;';
  if (f.type === 'subtitle') return '&#128196;';
  if (f.type === 'torrent') return '&#8681;';
  if (f.protected) return '&#128274;';
  return '&#128196;';
}

function relativeToRoot(filePath) {
  if (!state.fileRoot || !filePath) return '';
  if (filePath === state.fileRoot) return '';
  if (state.fileRoot === '/') return filePath.replace(/^\/+/, '');
  return filePath.startsWith(state.fileRoot + '/') ? filePath.slice(state.fileRoot.length + 1) : filePath;
}

function dirnamePath(filePath) {
  const value = String(filePath || '');
  if (!value || value === '/') return state.fileRoot || '/';
  const idx = value.lastIndexOf('/');
  if (idx <= 0) return value.startsWith('/') ? '/' : (state.fileRoot || '/');
  return value.slice(0, idx);
}

function pathHasHiddenSegment(filePath) {
  return relativeToRoot(filePath).split('/').some(part => part.startsWith('.') && part.length > 1);
}

function scrollFileIntoView(filePath) {
  requestAnimationFrame(() => {
    const item = document.querySelector(`[data-file-hash="${fileHash(filePath)}"]`);
    if (item) item.scrollIntoView({ block: 'nearest' });
  });
}

function pathIsSameOrChild(filePath, parentPath) {
  const parent = String(parentPath || '').replace(/\/+$/, '') || '/';
  const child = String(filePath || '');
  return child === parent || (parent === '/' ? child.startsWith('/') : child.startsWith(`${parent}/`));
}

function findFile(filePath) {
  return state.files.find(f => f.path === filePath) || state.recursiveSearchResults.find(f => f.path === filePath);
}

function validateItemNameInput(name) {
  const value = String(name || '').trim();
  if (!value) throw new Error('Name is required.');
  if (value === '.' || value === '..') throw new Error('Name cannot be "." or "..".');
  if (value.includes('/') || value.includes('\\')) throw new Error('Name cannot contain path separators.');
  return value;
}

function refreshFiles() {
  loadFiles(null, { addHistory: false });
}

function goFileRoot() {
  clearFileSearch();
  if (state.fileRoot) loadFiles(state.fileRoot);
}

function clearFileSearch() {
  state.fileSearch = '';
  state.recursiveSearchResults = [];
  state.recursiveSearchLoading = false;
  const input = document.getElementById('file-search');
  if (input) input.value = '';
}

function openFolder(filePath, clearSearch = false) {
  if (clearSearch) clearFileSearch();
  loadFiles(filePath);
}

function goFileUp() {
  if (state.parentPath && state.currentPath !== state.fileRoot) loadFiles(state.parentPath);
}

function goFileBack() {
  const previous = state.fileHistoryBack.pop();
  if (!previous) return;
  if (state.currentPath) state.fileHistoryForward.push(state.currentPath);
  loadFiles(previous, { addHistory: false });
}

function goFileForward() {
  const next = state.fileHistoryForward.pop();
  if (!next) return;
  if (state.currentPath) state.fileHistoryBack.push(state.currentPath);
  loadFiles(next, { addHistory: false });
}

function toggleShowHidden(checked) {
  state.showHidden = !!checked;
  localStorage.setItem('cm_show_hidden', String(state.showHidden));
  if (state.recursiveSearch && state.fileSearch.length >= 2) {
    scheduleRecursiveSearch();
  }
  loadFiles(null, { addHistory: false });
}

function toggleRecursiveSearch(checked) {
  state.recursiveSearch = !!checked;
  localStorage.setItem('cm_recursive_search', String(state.recursiveSearch));
  state.recursiveSearchResults = [];
  if (state.recursiveSearch && state.fileSearch.length >= 2) scheduleRecursiveSearch();
  else renderFiles();
}

function scheduleRecursiveSearch() {
  clearTimeout(state.recursiveSearchTimer);
  if (!state.recursiveSearch || state.fileSearch.length < 2) {
    state.recursiveSearchLoading = false;
    state.recursiveSearchResults = [];
    renderFiles();
    return;
  }
  state.recursiveSearchLoading = true;
  renderFiles();
  const query = state.fileSearch;
  state.recursiveSearchTimer = setTimeout(() => loadRecursiveFileSearch(query), 250);
}

async function loadRecursiveFileSearch(query) {
  try {
    const params = new URLSearchParams({ q: query, showHidden: state.showHidden ? 'true' : 'false' });
    const data = await api(`/api/search?${params.toString()}`);
    if (query !== state.fileSearch) return;
    state.recursiveSearchResults = (data.results || []).map((r) => {
      const p = r.path || r.file_path || '';
      const name = r.name || basename(p);
      const ext = r.extension || (name.includes('.') ? name.slice(name.lastIndexOf('.')) : '');
      return {
        name,
        path: p,
        relativePath: relativeToRoot(p),
        type: r.is_directory ? 'folder' : getFileType(ext || name),
        ext,
        size: r.size || 0,
        mtime: r.mtime || 0,
        modifiedAt: r.mtime ? new Date(r.mtime * 1000).toISOString() : '',
        isHidden: name.startsWith('.') || relativeToRoot(p).split('/').some(part => part.startsWith('.')),
        protected: !!r.protected,
        searchResult: true,
      };
    });
  } catch (e) {
    state.recursiveSearchResults = [];
  } finally {
    if (query === state.fileSearch) {
      state.recursiveSearchLoading = false;
      renderFiles();
    }
  }
}

async function loadFileStorageSummary(force = false) {
  const summary = document.getElementById('file-storage-summary');
  if (!summary || !state.fileRoot) return;
  const now = Date.now();
  if (!force && state.storageSummaryLoaded && now - state.storageSummaryLoaded < 30000) return;
  state.storageSummaryLoaded = now;
  summary.innerHTML = '<div class="empty-state small"><p>Loading storage summary...</p></div>';
  try {
    const [stats, dirs] = await Promise.all([
      api('/api/storage/stats'),
      api(`/api/storage/dirs?path=${encodeURIComponent(state.fileRoot)}`),
    ]);
    const usedPct = stats.totalSpace ? Math.round((stats.usedSpace / stats.totalSpace) * 100) : 0;
    const largestDirs = (dirs.dirs || []).slice(0, 5);
    const largestFiles = (stats.largestFiles || []).slice(0, 5);
    const largestRows = [
      ...largestDirs.map(d => ({ ...d, kind: 'Folder' })),
      ...largestFiles.map(f => ({ ...f, name: basename(f.path), kind: 'File' })),
    ].sort((a, b) => (b.size || 0) - (a.size || 0)).slice(0, 6);

    summary.innerHTML = `
      <div class="storage-overview">
        <div class="storage-meter">
          <div class="storage-meter-header">
            <div>
              <div class="storage-meter-title">${esc(state.fileRootLabel)}</div>
              <div class="storage-meter-path">${esc(state.fileRoot)}</div>
            </div>
            <button class="btn-secondary btn-sm" onclick="goFileRoot()">Go to Root</button>
          </div>
          <div class="storage-meter-bar"><div style="width:${Math.min(100, usedPct)}%"></div></div>
          <div class="storage-meter-meta">
            <span>${formatBytes(stats.usedSpace)} used</span>
            <span>${formatBytes(stats.freeSpace)} free</span>
            <span>${formatBytes(stats.totalSpace)} total</span>
          </div>
        </div>
        <div class="storage-quick-list">
          <div class="storage-quick-title">Largest items</div>
          ${largestRows.length ? largestRows.map(item => {
            const p = jsArg(item.path);
            return `<button class="storage-quick-row" onclick="openStorageItem('${p}', '${item.kind}')">
              <span>
                <strong>${esc(item.name || basename(item.path))}</strong>
                <small>${esc(item.kind)} · ${esc(relativeToRoot(item.path))}</small>
              </span>
              <em>${formatBytes(item.size || 0)}</em>
            </button>`;
          }).join('') : '<div class="empty-state small"><p>No storage data yet.</p></div>'}
        </div>
      </div>`;
  } catch (e) {
    summary.innerHTML = `<div class="empty-state small"><p>Could not load storage summary.</p><p class="empty-hint">${esc(e.message)}</p></div>`;
  }
}

async function openStorageItem(filePath, kind) {
  showSection('library');
  clearFileSearch();
  if (pathHasHiddenSegment(filePath) && !state.showHidden) {
    state.showHidden = true;
    localStorage.setItem('cm_show_hidden', 'true');
  }

  try {
    if (kind === 'Folder') {
      await loadFiles(filePath);
      toast(`Opened ${relativeToRoot(filePath) || filePath}`, 'info');
    } else {
      await navigateToFile(filePath);
    }
  } catch (e) {
    toast(`Could not open ${filePath}: ${e.message}`, 'error');
  }
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
  if (state.recursiveSearch && query.length >= 2) scheduleRecursiveSearch();
  else {
    state.recursiveSearchLoading = false;
    state.recursiveSearchResults = [];
    renderFiles();
  }
}

function filterFileType(type, btn) {
  state.fileFilter = type;
  document.querySelectorAll('#section-library .filter-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderFiles();
}

function toggleLibraryView() {
  state.libraryView = state.libraryView === 'list' ? 'grid' : 'list';
  localStorage.setItem('cm_file_view', state.libraryView);
  renderFiles();
}

async function selectFile(filePath) {
  let f = findFile(filePath);
  if (!f) {
    const name = basename(filePath);
    const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
    f = {
      name,
      path: filePath,
      relativePath: relativeToRoot(filePath),
      type: getFileType(ext || name),
      ext,
      size: 0,
      mtime: 0,
      modifiedAt: '',
      protected: false,
    };
  }
  if (f.type === 'folder') return;
  if (state.preview.unsaved && state.preview.filePath !== filePath && !confirm('Discard unsaved changes?')) return;
  state.selectedFilePaths.clear();
  state.selectedFile = f;
  state.preview = { filePath, content: '', originalContent: '', unsaved: false, loading: true };
  renderFiles();
  renderSelectionBar();
  renderPreviewLoading(f);

  if (f.protected) {
    state.preview.loading = false;
    renderPreviewUnavailable(f, 'This file is protected and cannot be previewed or edited from Cast Manager.');
    return;
  }

  try {
    const data = await withPermissionRetry(
      'File Access',
      `Enter the server sudo password to preview ${f.name}.`,
      (sudoPwd) => api(`/api/files/read?path=${encodeURIComponent(filePath)}`, { headers: sudoHeaders(sudoPwd), silent: true })
    );
    state.preview.loading = false;
    if (data.previewAvailable) {
      state.selectedFile = { ...f, ...(data.metadata || {}) };
      state.preview.content = data.content || '';
      state.preview.originalContent = data.content || '';
      state.preview.unsaved = false;
      renderPreviewEditor(f, data);
    } else {
      state.selectedFile = { ...f, ...(data.metadata || {}) };
      renderPreviewUnavailable(f, data.message || 'Preview unavailable for this file type.', data);
    }
    renderSelectionBar();
    renderFiles();
    scrollFileIntoView(filePath);
  } catch (e) {
    state.preview.loading = false;
    renderPreviewUnavailable(f, e.message || 'Could not read this file.');
  }
}

function openSelectedFile(filePath) {
  const f = findFile(filePath) || state.selectedFile;
  if (!f) return;
  if (f.type === 'video' || f.type === 'audio') openStreamPlayer(f.path, f.type);
  else selectFile(f.path);
}

function clearFileSelection() {
  state.selectedFile = null;
  state.selectedFilePaths.clear();
  state.preview = { filePath: '', content: '', originalContent: '', unsaved: false, loading: false };
  const panel = document.getElementById('file-preview');
  if (panel) {
    panel.innerHTML = '<div class="empty-state small"><p>No file selected.</p><p class="empty-hint">Select a file to preview details.</p></div>';
  }
  renderSelectionBar();
  renderFiles();
}

function getSelectedItems() {
  const byPath = new Map([
    ...state.files.map(f => [f.path, f]),
    ...state.recursiveSearchResults.map(f => [f.path, f]),
    ...state.visibleFiles.map(f => [f.path, f]),
  ]);
  return [...state.selectedFilePaths].map(path => byPath.get(path) || {
    name: basename(path),
    path,
    type: 'file',
  });
}

function toggleFileSelection(filePath, selected) {
  if (selected) state.selectedFilePaths.add(filePath);
  else state.selectedFilePaths.delete(filePath);
  if (state.selectedFilePaths.size) state.selectedFile = null;
  renderSelectionBar();
  renderFiles();
}

function selectAllVisibleFiles() {
  if (!state.visibleFiles.length) {
    toast('No visible items to select', 'info');
    return;
  }
  state.selectedFile = null;
  state.selectedFilePaths = new Set(state.visibleFiles.map(f => f.path));
  renderSelectionBar();
  renderFiles();
  toast(`Selected ${state.selectedFilePaths.size} item${state.selectedFilePaths.size === 1 ? '' : 's'}`, 'info');
}

function renderSelectionBar() {
  const bar = document.getElementById('file-selection-bar');
  if (!bar) return;
  const selectedItems = getSelectedItems();
  if (selectedItems.length) {
    const totalSize = selectedItems.reduce((sum, item) => sum + (item.size || 0), 0);
    bar.style.display = 'flex';
    bar.innerHTML = `
      <div class="selection-info">
        <strong>${selectedItems.length} selected</strong>
        <span>${formatBytes(totalSize)} total in current selection</span>
      </div>
      <div class="selection-actions">
        <button class="btn-secondary btn-sm" onclick="clearFileSelection()">Clear</button>
        <button class="btn-secondary btn-sm danger" onclick="deleteSelectedFiles(false)">Trash Selected</button>
        <button class="btn-secondary btn-sm danger" onclick="deleteSelectedFiles(true)">Delete Now</button>
      </div>`;
    return;
  }
  const f = state.selectedFile;
  if (!f) {
    bar.style.display = 'none';
    bar.innerHTML = '';
    return;
  }
  const p = jsArg(f.path);
  const n = jsArg(f.name);
  bar.style.display = 'flex';
  bar.innerHTML = `
    <div class="selection-info">
      <strong>${esc(f.name)}</strong>
      <span>${esc(relativeToRoot(f.path) || f.path)}</span>
    </div>
    <div class="selection-actions">
      <button class="btn-secondary btn-sm" onclick="openSelectedFile('${p}')">Open</button>
      <button class="btn-secondary btn-sm" onclick="renameFile('${p}', '${n}')">Rename</button>
      <button class="btn-secondary btn-sm" onclick="copyFile('${p}', '${n}')">Copy</button>
      <button class="btn-secondary btn-sm" onclick="moveFile('${p}', '${n}')">Move</button>
      ${f.type !== 'folder' && !f.protected ? `<button class="btn-secondary btn-sm" onclick="downloadFile('${p}')">Download</button>` : ''}
      <button class="btn-secondary btn-sm" onclick="copyFileAddress('${p}')">Copy Address</button>
      <button class="btn-secondary btn-sm danger" onclick="deleteFile('${p}', '${n}')">Trash</button>
      <button class="btn-secondary btn-sm danger" onclick="deleteFileNow('${p}', '${n}')">Delete Now</button>
    </div>`;
}

function renderPreviewLoading(f) {
  const panel = document.getElementById('file-preview');
  if (!panel) return;
  panel.innerHTML = `
    <div class="preview-header">
      <div class="preview-title">${esc(f.name)}</div>
      <div class="preview-path">${esc(relativeToRoot(f.path))}</div>
    </div>
    <div class="empty-state small"><p>Loading preview...</p></div>`;
}

function renderPreviewMetadata(f, data = {}) {
  const meta = data.metadata || f;
  const modified = meta.modifiedAt ? new Date(meta.modifiedAt).toLocaleString() : (meta.mtime ? new Date(meta.mtime * 1000).toLocaleString() : '-');
  return `<div class="preview-meta">
    <div class="preview-meta-row"><span>Type</span><strong>${esc(meta.mimeType || meta.type || f.type || 'file')}</strong></div>
    <div class="preview-meta-row"><span>Size</span><strong>${formatBytes(meta.size || f.size || 0)}</strong></div>
    <div class="preview-meta-row"><span>Modified</span><strong>${esc(modified)}</strong></div>
    <div class="preview-meta-row"><span>Path</span><strong>${esc(relativeToRoot(f.path))}</strong></div>
  </div>`;
}

function renderPreviewUnavailable(f, message, data = {}) {
  const panel = document.getElementById('file-preview');
  if (!panel) return;
  const mediaButtons = (f.type === 'video' || f.type === 'audio') && !f.protected
    ? `<button class="btn-primary btn-sm" onclick="openStreamPlayer('${jsArg(f.path)}', '${f.type}')">Play</button>
       <button class="btn-secondary btn-sm" onclick="openCastModal('${jsArg(f.path)}', '${f.type}')">Cast</button>`
    : '';
  const imagePreview = f.type === 'image' && !f.protected && !data.tooLarge
    ? `<img src="/api/files/stream?path=${encodeURIComponent(f.path)}&raw=1" alt="${esc(f.name)}" style="width:100%;border-radius:var(--radius-md);border:1px solid var(--color-border);margin-bottom:12px">`
    : '';
  panel.innerHTML = `
    <div class="preview-header">
      <div class="preview-title">${esc(f.name)}</div>
      <div class="preview-path">${esc(relativeToRoot(f.path))}</div>
    </div>
    <div class="preview-body">
      ${imagePreview}
      ${renderPreviewMetadata(f, data)}
      <p style="font-size:13px;color:var(--color-text-tertiary);line-height:1.5">${esc(message)}</p>
      <div class="preview-actions">
        ${mediaButtons}
        ${!f.protected ? `<button class="btn-secondary btn-sm" onclick="downloadFile('${jsArg(f.path)}')">Download</button>` : ''}
        <button class="btn-secondary btn-sm" onclick="copyFileAddress('${jsArg(f.path)}')">Copy Address</button>
      </div>
    </div>`;
}

function renderPreviewEditor(f, data) {
  const panel = document.getElementById('file-preview');
  if (!panel) return;
  panel.innerHTML = `
    <div class="preview-header">
      <div class="preview-title">${esc(f.name)}</div>
      <div class="preview-path">${esc(relativeToRoot(f.path))}</div>
    </div>
    <div class="preview-body">
      ${renderPreviewMetadata(f, data)}
      <textarea id="preview-editor" class="preview-editor" spellcheck="false" oninput="markPreviewUnsaved()">${esc(data.content || '')}</textarea>
      <div class="preview-actions">
        <button class="btn-primary btn-sm" id="preview-save-btn" onclick="savePreviewFile()" disabled>Save</button>
        <span class="unsaved-indicator" id="preview-unsaved" style="display:none">Unsaved changes</span>
        <button class="btn-secondary btn-sm" onclick="copyFileAddress('${jsArg(f.path)}')">Copy Address</button>
        <button class="btn-secondary btn-sm" onclick="downloadFile('${jsArg(f.path)}')">Download</button>
      </div>
    </div>`;
}

function markPreviewUnsaved() {
  const editor = document.getElementById('preview-editor');
  if (!editor) return;
  state.preview.content = editor.value;
  state.preview.unsaved = state.preview.content !== state.preview.originalContent;
  const saveBtn = document.getElementById('preview-save-btn');
  const indicator = document.getElementById('preview-unsaved');
  if (saveBtn) saveBtn.disabled = !state.preview.unsaved;
  if (indicator) indicator.style.display = state.preview.unsaved ? 'inline' : 'none';
}

async function savePreviewFile() {
  if (!state.preview.filePath || !state.preview.unsaved) return;
  const editor = document.getElementById('preview-editor');
  const content = editor ? editor.value : state.preview.content;
  try {
    await withPermissionRetry(
      'Save File',
      `Enter the server sudo password to save ${basename(state.preview.filePath)}.`,
      (sudoPwd) => api('/api/files/write', {
        method: 'POST',
        body: withSudoBody({ filePath: state.preview.filePath, content }, sudoPwd),
        silent: true,
      })
    );
    state.preview.originalContent = content;
    state.preview.content = content;
    state.preview.unsaved = false;
    markPreviewUnsaved();
    toast('File saved', 'success');
    loadFiles(null, { addHistory: false });
  } catch (e) { /* already toasted */ }
}

// ─── File Operations ─────────────────────────────────────────
function deleteFile(filePath, fileName) {
  const f = findFile(filePath);
  const kind = f?.type === 'folder' ? 'folder and its contents' : 'file';
  showConfirm('Move to Trash', `Move "${fileName}" to trash? This ${kind} will disappear from the current folder and can be restored from Trash.`, async () => {
    try {
      await withPermissionRetry(
        'Move to Trash',
        `Enter the server sudo password to move ${fileName} to trash.`,
        (sudoPwd) => api('/api/files/delete', {
          method: 'POST',
          body: withSudoBody({ filePath }, sudoPwd),
          silent: true,
        })
      );
      if (state.selectedFile?.path === filePath) clearFileSelection();
      toast(`Moved to trash: ${fileName}`, 'success');
      state.storageSummaryLoaded = 0;
      loadFiles(null, { addHistory: false });
    } catch (e) { /* already toasted */ }
  });
}

function deleteFileNow(filePath, fileName) {
  const f = findFile(filePath);
  const kind = f?.type === 'folder' ? 'folder and all of its contents' : 'file';
  showConfirm('Delete Immediately', `Permanently delete "${fileName}" now? This ${kind} will not go to Trash and cannot be restored from Cast Manager.`, async () => {
    try {
      await withPermissionRetry(
        'Delete Immediately',
        `Enter the server sudo password to permanently delete ${fileName}.`,
        (sudoPwd) => api('/api/files/delete', {
          method: 'POST',
          body: withSudoBody({ filePath, permanent: true }, sudoPwd),
          silent: true,
        })
      );
      if (state.selectedFile && pathIsSameOrChild(state.selectedFile.path, filePath)) clearFileSelection();
      state.selectedFilePaths.delete(filePath);
      toast(`Permanently deleted: ${fileName}`, 'success');
      state.storageSummaryLoaded = 0;
      loadFiles(null, { addHistory: false });
    } catch (e) { /* already toasted */ }
  });
}

async function deleteSelectedFiles(permanent = false) {
  const selectedItems = getSelectedItems();
  if (!selectedItems.length) return;
  const title = permanent ? 'Delete Selected Immediately' : 'Move Selected to Trash';
  const message = permanent
    ? `Permanently delete ${selectedItems.length} selected item${selectedItems.length === 1 ? '' : 's'} now? They will not go to Trash and cannot be restored from Cast Manager.`
    : `Move ${selectedItems.length} selected item${selectedItems.length === 1 ? '' : 's'} to Trash?`;
  showConfirm(title, message, async () => {
    let deleted = 0;
    const failures = [];
    for (const item of selectedItems) {
      try {
        await withPermissionRetry(
          permanent ? 'Delete Immediately' : 'Move to Trash',
          `Enter the server sudo password to ${permanent ? 'permanently delete' : 'move'} ${item.name}.`,
          (sudoPwd) => api('/api/files/delete', {
            method: 'POST',
            body: withSudoBody({ filePath: item.path, permanent }, sudoPwd),
            silent: true,
          })
        );
        deleted++;
        state.selectedFilePaths.delete(item.path);
      } catch (e) {
        failures.push(`${item.name}: ${e.message}`);
      }
    }
    if (state.selectedFile && selectedItems.some(item => pathIsSameOrChild(state.selectedFile.path, item.path))) {
      clearFileSelection();
    }
    state.storageSummaryLoaded = 0;
    await loadFiles(null, { addHistory: false });
    if (deleted) toast(`${permanent ? 'Deleted' : 'Moved to trash'} ${deleted} item${deleted === 1 ? '' : 's'}`, 'success');
    if (failures.length) toast(`${failures.length} item${failures.length === 1 ? '' : 's'} failed`, 'error');
  });
}

function renameFile(filePath, currentName) {
  const newName = prompt('Rename to:', currentName);
  if (!newName || newName === currentName) return;
  let safeName;
  try { safeName = validateItemNameInput(newName); }
  catch (e) { toast(e.message, 'error'); return; }
  (async () => {
    try {
      const data = await withPermissionRetry(
        'Rename Item',
        `Enter the server sudo password to rename ${currentName}.`,
        (sudoPwd) => api('/api/files/rename', {
          method: 'POST',
          body: withSudoBody({ oldPath: filePath, newName: safeName }, sudoPwd),
          silent: true,
        })
      );
      if (state.selectedFile?.path === filePath) state.selectedFile.path = data.newPath;
      toast(`Renamed to: ${safeName}`, 'success');
      loadFiles(null, { addHistory: false });
    } catch (e) { /* already toasted */ }
  })();
}

function copyFile(filePath, currentName) {
  const ext = currentName.lastIndexOf('.') > 0 ? currentName.slice(currentName.lastIndexOf('.')) : '';
  const base = currentName.lastIndexOf('.') > 0 ? currentName.slice(0, currentName.lastIndexOf('.')) : currentName;
  const destName = prompt('Copy as:', `${base} (copy)${ext}`);
  if (!destName) return;
  let safeName;
  try { safeName = validateItemNameInput(destName); }
  catch (e) { toast(e.message, 'error'); return; }
  (async () => {
    try {
      toast('Copying...', 'info');
      await withPermissionRetry(
        'Copy Item',
        `Enter the server sudo password to copy ${currentName}.`,
        (sudoPwd) => api('/api/files/copy', {
          method: 'POST',
          body: withSudoBody({ filePath, destName: safeName }, sudoPwd),
          silent: true,
        })
      );
      toast(`Copied as: ${safeName}`, 'success');
      state.storageSummaryLoaded = 0;
      loadFiles(null, { addHistory: false });
    } catch (e) { /* already toasted */ }
  })();
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

async function copyRelativePath(filePath) {
  const rel = relativeToRoot(filePath);
  try {
    await navigator.clipboard.writeText(rel || filePath);
    toast('Path copied', 'success');
  } catch (_) {
    toast(rel || filePath, 'info', 6000);
  }
}

async function copyFileAddress(filePath) {
  const address = String(filePath || '');
  try {
    await navigator.clipboard.writeText(address);
    toast('File address copied', 'success');
  } catch (_) {
    toast(address, 'info', 6000);
  }
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
    // Add a <track> if subtitles exist (served as WebVTT via /api/subtitles/:id.vtt).
    // This does not affect Chromecast; it's for the in-browser stream player only.
    try {
      // Remove prior tracks
      Array.from(video.querySelectorAll('track')).forEach(t => t.remove());
      api('/api/subtitles', { method: 'POST', body: { filePath } }).then((subData) => {
        const first = (subData?.subtitles || []).find(s => typeof s === 'object' && s.id) || null;
        if (!first?.id) return;
        const track = document.createElement('track');
        track.kind = 'subtitles';
        track.label = first.label || 'Subtitles';
        track.srclang = 'en';
        track.src = `/api/subtitles/${encodeURIComponent(first.id)}.vtt`;
        track.default = true;
        video.appendChild(track);
      }).catch(() => {});
    } catch (_) { /* ignore */ }
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
      toast('Browser player URL copied', 'success');
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
      const directInput = document.getElementById('stream-direct-url-input');
      const openLink = document.getElementById('stream-url-open');
      const expiresDiv = document.getElementById('stream-url-expires');
      panel.style.display = 'block';
      input.value = data.streamUrl;
      if (directInput) directInput.value = data.directUrl || data.streamUrl;
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

function copyDirectStreamUrlFromInput() {
  const input = document.getElementById('stream-direct-url-input');
  navigator.clipboard.writeText(input.value).then(() => {
    toast('Direct stream URL copied!', 'success');
  }).catch(() => {
    input.select();
    document.execCommand('copy');
    toast('Direct stream URL copied!', 'success');
  });
}

// Show standalone Stream URL modal from file list
async function showStreamUrlModal(filePath) {
  try {
    const data = await api('/api/stream/generate', { method: 'POST', body: { filePath, expiresIn: 24 } });
    const modal = document.getElementById('stream-url-modal');
    document.getElementById('stream-url-modal-filename').textContent = basename(filePath);
    document.getElementById('stream-url-modal-input').value = data.streamUrl;
    document.getElementById('stream-url-modal-direct-input').value = data.directUrl || data.streamUrl;
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
  let safeName;
  try { safeName = validateItemNameInput(name); }
  catch (e) { toast(e.message, 'error'); return; }
  (async () => {
    try {
      await withPermissionRetry(
        'Create Folder',
        `Enter the server sudo password to create a folder in ${state.currentPath || 'this location'}.`,
        (sudoPwd) => api('/api/files/mkdir', {
          method: 'POST',
          body: withSudoBody({ parentPath: state.currentPath, name: safeName }, sudoPwd),
          silent: true,
        })
      );
      toast(`Created folder: ${safeName}`, 'success');
      state.storageSummaryLoaded = 0;
      loadFiles(null, { addHistory: false });
    } catch (e) { /* already toasted */ }
  })();
}

function createNewFile() {
  const name = prompt('New file name:', 'untitled.txt');
  if (!name) return;
  let safeName;
  try { safeName = validateItemNameInput(name); }
  catch (e) { toast(e.message, 'error'); return; }
  (async () => {
    try {
      const data = await withPermissionRetry(
        'Create File',
        `Enter the server sudo password to create a file in ${state.currentPath || 'this location'}.`,
        (sudoPwd) => api('/api/files/create', {
          method: 'POST',
          body: withSudoBody({ parentPath: state.currentPath, name: safeName }, sudoPwd),
          silent: true,
        })
      );
      toast(`Created file: ${safeName}`, 'success');
      state.storageSummaryLoaded = 0;
      await loadFiles(null, { addHistory: false });
      if (data.path) selectFile(data.path);
    } catch (e) { /* already toasted */ }
  })();
}

// ─── Delete Current Folder ──────────────────────────────────
function deleteCurrentFolder() {
  if (!state.currentPath || state.currentPath === state.fileRoot) {
    return toast('Cannot delete the file-manager root.', 'warning');
  }
  const folderName = basename(state.currentPath);
  showConfirm('Move Folder to Trash', `Move the current folder "${folderName}" and all of its contents to trash?`, async () => {
    try {
      const parent = state.parentPath || state.fileRoot;
      await withPermissionRetry(
        'Move Folder to Trash',
        `Enter the server sudo password to move ${folderName} to trash.`,
        (sudoPwd) => api('/api/files/delete', {
          method: 'POST',
          body: withSudoBody({ filePath: state.currentPath }, sudoPwd),
          silent: true,
        })
      );
      clearFileSelection();
      toast(`Moved to trash: ${folderName}`, 'success');
      state.storageSummaryLoaded = 0;
      loadFiles(parent, { addHistory: false });
    } catch (e) { /* already toasted */ }
  });
}

function deleteCurrentFolderNow() {
  if (!state.currentPath || state.currentPath === state.fileRoot) {
    return toast('Cannot delete the file-manager root.', 'warning');
  }
  const folderName = basename(state.currentPath);
  showConfirm('Delete Folder Immediately', `Permanently delete the current folder "${folderName}" and all of its contents now? It will not go to Trash and cannot be restored from Cast Manager.`, async () => {
    try {
      const folderPath = state.currentPath;
      const parent = state.parentPath || state.fileRoot;
      await withPermissionRetry(
        'Delete Folder Immediately',
        `Enter the server sudo password to permanently delete ${folderName}.`,
        (sudoPwd) => api('/api/files/delete', {
          method: 'POST',
          body: withSudoBody({ filePath: folderPath, permanent: true }, sudoPwd),
          silent: true,
        })
      );
      clearFileSelection();
      toast(`Permanently deleted: ${folderName}`, 'success');
      state.storageSummaryLoaded = 0;
      loadFiles(parent, { addHistory: false });
    } catch (e) { /* already toasted */ }
  });
}

// ─── Move File ──────────────────────────────────────────────
function moveFile(filePath, fileName) {
  const dest = prompt(`Move "${fileName}" to directory:`, state.currentPath);
  if (!dest || dest === state.currentPath) return;
  (async () => {
    try {
      await withPermissionRetry(
        'Move Item',
        `Enter the server sudo password to move ${fileName}.`,
        (sudoPwd) => api('/api/files/move', {
          method: 'POST',
          body: withSudoBody({ sourcePath: filePath, destDir: dest }, sudoPwd),
          silent: true,
        })
      );
      if (state.selectedFile?.path === filePath) clearFileSelection();
      toast(`Moved: ${fileName}`, 'success');
      state.storageSummaryLoaded = 0;
      loadFiles(null, { addHistory: false });
    } catch (e) { /* already toasted */ }
  })();
}

// ─── Sort Files ─────────────────────────────────────────────
function sortFiles(sortBy) {
  state.fileSort = sortBy;
  localStorage.setItem('cm_file_sort', sortBy);
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
  renderCastDevices();
  updateCastPipelinePreview(filePath);
  const customSubtitleInput = document.getElementById('custom-subtitle-path');
  if (customSubtitleInput) customSubtitleInput.value = '';

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
      subSection.style.display = 'block';
      if (subData.subtitles && subData.subtitles.length > 0) {
        // subtitles can be strings (legacy) or objects { id, label, ... }
        subSelect.innerHTML = '<option value="">None</option>' +
          subData.subtitles.map((s) => {
            if (typeof s === 'string') return `<option value="${esc(s)}">${esc(basename(s))}</option>`;
            const val = s.id || '';
            const label = s.label || (s.sourcePath ? basename(s.sourcePath) : `Subtitle ${val}`);
            return `<option value="${esc(val)}">${esc(label)}</option>`;
          }).join('');
      } else {
        subSelect.innerHTML = '<option value="">None detected</option>';
      }
    } catch (e) {
      document.getElementById('subtitle-section').style.display = 'block';
      document.getElementById('subtitle-select').innerHTML = '<option value="">None detected</option>';
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

async function updateCastPipelinePreview(filePath) {
  const el = document.getElementById('cast-pipeline-label');
  if (!el) return;
  el.textContent = 'Analyzing...';
  try {
    const provider = state.selectedCastDevice?.provider || 'chromecast';
    const mode = document.getElementById('cast-mode-select')?.value || 'auto';
    const data = await api('/api/media/analyze', {
      method: 'POST',
      body: { filePath, target: provider, mode, autoTranscode: state.settings?.autoTranscode || 'auto' },
      silent: true,
    });
    const pipeline = data.pipelineMode || data.analysis?.recommendedPipeline || 'auto';
    const reasons = (data.analysis?.reasons || []).slice(0, 2).join(' ');
    el.textContent = `${pipeline}${reasons ? ` · ${reasons}` : ''}`;
  } catch (e) {
    el.textContent = 'Automatic compatibility mode';
  }
}

async function prepareCustomSubtitleForCast() {
  const file = state.castModalFile;
  const input = document.getElementById('custom-subtitle-path');
  const subtitlePath = input?.value.trim() || '';
  if (!file) return toast('Choose a video first.', 'warning');
  if (!subtitlePath) return toast('Enter a server subtitle path first.', 'warning');
  try {
    await api('/api/subtitles/prepare', { method: 'POST', body: { filePath: file.path, subtitlePath } });
    toast('Subtitle path is valid and can be cast.', 'success');
  } catch (e) {
    toast(`Subtitle check failed: ${e.message}`, 'error', 6000);
  }
}

async function confirmCast() {
  if (!state.castModalFile) return;
  const seekTo = document.getElementById('cast-seek-input').value;
  const subtitleChoice = document.getElementById('subtitle-select')?.value || '';
  const customSubtitlePath = document.getElementById('custom-subtitle-path')?.value.trim() || '';
  const filePath = state.castModalFile.path;
  const autoTranscode = state.settings?.autoTranscode || 'auto';
  const allowPretranscode = state.settings?.allowPretranscode === true;
  const selected = state.selectedCastDevice || {};
  const mode = document.getElementById('cast-mode-select')?.value || 'auto';

  closeCastModal();
  toast('Casting...', 'info');

  try {
    let endpoint = '/api/cast/start';
    let body = {
      filePath,
      seekTo: seekTo !== '00:00:00' ? seekTo : undefined,
      autoTranscode,
      mode,
      provider: selected.provider || 'chromecast',
      deviceId: selected.device_id || selected.id,
      ...(allowPretranscode ? { allowPretranscode: true } : {}),
    };

    if (customSubtitlePath || subtitleChoice) {
      if (customSubtitlePath) body.subtitlePath = customSubtitlePath;
      else body.subtitleId = subtitleChoice;
    }

    const data = await api(endpoint, { method: 'POST', body });

    if (data.transcoding) {
      toast(data.message, 'warning', 8000);
      // Poll for transcoding completion
      pollTranscode(filePath, seekTo, data.jobId);
      return;
    }

    state.casting.filePath = filePath;
    state.casting.title = basename(filePath);
    state.casting.state = 'playing';
    if (data.provider || data.pipelineMode) {
      state.casting.provider = data.provider;
      state.casting.pipelineMode = data.pipelineMode;
    }
    toast(`Casting started${data.pipelineMode ? ` (${data.pipelineMode})` : ''}`, 'success');
    showMiniPlayer();
    updatePositionEntry(filePath);
    startCastPolling();
  } catch (e) { /* already toasted */ }
}

async function pollTranscode(filePath, seekTo, jobId) {
  let attempts = 0;
  const maxAttempts = 200; // ~10 minutes at 3s intervals
  const check = async () => {
    attempts++;
    try {
      const body = jobId ? { filePath, jobId } : { filePath };
      const data = await api('/api/transcode/status', { method: 'POST', body });
      if (data.ready) {
        toast('Transcoding complete! Casting now...', 'success');
        try {
          await api('/api/cast', {
            method: 'POST',
            body: {
              filePath,
              seekTo,
              autoTranscode: state.settings?.autoTranscode || 'auto',
              ...(state.settings?.allowPretranscode === true ? { allowPretranscode: true } : {}),
            },
          });
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

function isConfirmedNaturalEnd(info) {
  const now = nowMs();
  const duration = Number(info?.duration || state.casting.duration || 0);
  const currentTime = Number(info?.currentTime || state.casting.currentTime || 0);

  const activeSession = Boolean(info?.activeSession);
  const fallbackAvailable = Boolean(info?.fallbackAvailable);
  const stateName = info?.state || state.casting.state;

  const nearEnd = duration > 0 && currentTime >= duration - 5;
  const recentCommand = now - (state.casting.lastCommandAt || 0) < 5000;
  const recentSeek = now - (state.casting.lastSeekAt || 0) < 5000;
  const recentStop = now - (state.casting.lastStopAt || 0) < 4000;
  const settling = now < (state.castControl?.settleUntil || 0);
  const transitionalState = ['starting', 'seeking', 'pausing', 'resuming', 'stopping', 'buffering'].includes(state.casting.state);

  return stateName === 'idle' &&
    !activeSession &&
    !fallbackAvailable &&
    nearEnd &&
    !recentCommand &&
    !recentSeek &&
    !recentStop &&
    !settling &&
    !state.isDraggingScrubber &&
    !transitionalState &&
    (state.castControl?.idleStreak || 0) >= 2;
}

async function pollCastStatus() {
  if (state.isDraggingScrubber) return;
  if (shouldIgnorePollUpdate()) return;
  try {
    const info = await api('/api/cast/status');
    state.casting.lastStatusAt = nowMs();

    const recentlyStopped = nowMs() - (state.casting.lastStopAt || 0) < 4000;
    if (recentlyStopped && info && info.activeSession) {
      // Treat as stale backend/receiver status immediately after explicit stop.
      return;
    }

    const receiverReachable = info && info.receiverReachable !== false;
    const success = !!(info && info.success !== false);

    if (!receiverReachable || !success) {
      state.casting.receiverReachable = false;
      state.casting.consecutiveStatusFailures++;
      // Never hide mini-player based on a failed/unreachable poll.
      if (info && info.activeSession) showMiniPlayer();
      return;
    }

    state.casting.receiverReachable = true;
    state.casting.consecutiveStatusFailures = 0;
    state.casting.lastNormalizedStatus = info;

    state.casting.state = info.state || 'unknown';
    state.casting.currentTime = Number.isFinite(info.currentTime) ? info.currentTime : 0;
    state.casting.duration = Number.isFinite(info.duration) ? info.duration : 0;
    if (info.title) state.casting.title = info.title;
    if (info.volumeLevel !== undefined) state.casting.volume = info.volumeLevel;

    updateMiniPlayer();
    updateDeviceIndicator();

    // Show mini-player when actively casting, hide when idle
    if (info.state === 'playing' || info.state === 'paused' || info.state === 'buffering') {
      showMiniPlayer();
    }

    // If backend thinks there's an active session, don't hide mini-player just because
    // receiver status momentarily reports idle (e.g. during seek fallback restart).
    if ((info.state === 'idle' || info.state === 'unknown') && info.activeSession) {
      showMiniPlayer();
    }

    // Track last "active" signal for robust idle handling
    if (info.state && info.state !== 'idle') {
      state.castControl.idleStreak = 0;
      state.castControl.lastNonIdleAt = nowMs();
      state.castControl.lastKnownTime = info.currentTime || state.castControl.lastKnownTime || 0;
      state.castControl.lastKnownDuration = info.duration || state.castControl.lastKnownDuration || 0;
    } else {
      state.castControl.idleStreak++;
    }

    // Natural-end gate (single source of truth)
    if (isConfirmedNaturalEnd(info) && state.casting.filePath) {
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
  const cmdId = markCastCommand(action === 'pause' ? 'pause' : 'resume');
  beginCastCommand(action, 1000);
  try {
    const result = await api('/api/cast/controls', { method: 'POST', body: { action } });
    if (!isCurrentCastCommand(cmdId)) return;
    if (result && result.state) state.casting.state = result.state;
    else state.casting.state = action === 'pause' ? 'paused' : 'playing';
    updateMiniPlayer();
  } finally {
    endCastCommand();
  }
}

async function stopPlayback() {
  const cmdId = markCastCommand('stop');
  beginCastCommand('stop', 1500);
  try {
    await api('/api/cast/controls', { method: 'POST', body: { action: 'stop' } });
  } finally {
    endCastCommand();
  }
  if (!isCurrentCastCommand(cmdId)) return;
  saveCurrentPosition();
  state.casting.state = 'idle';
  state.casting.currentTime = 0;
  state.casting.receiverReachable = true;
  state.casting.consecutiveStatusFailures = 0;
  state.castControl.idleStreak = 0;
  hideMiniPlayer();
  stopCastPolling();
}

async function seekRelative(delta) {
  const newTime = Math.max(0, state.casting.currentTime + delta);
  requestSeekTo(newTime, { settleMs: 1200 });
}

async function seekTo(seconds) {
  requestSeekTo(seconds, { settleMs: 1200 });
}

function scheduleSeekDispatch(delayMs = 180) {
  if (state.castControl.seekDebounceTimer) clearTimeout(state.castControl.seekDebounceTimer);
  state.castControl.seekDebounceTimer = setTimeout(dispatchQueuedSeek, delayMs);
}

async function dispatchQueuedSeek() {
  const val = state.castControl.lastSeekTarget;
  if (val == null) return;

  if (state.castControl.seekInFlight) {
    state.castControl.seekQueued = true;
    return;
  }

  const cmdId = state.casting.lastCommandId;
  const settleMs = state.castControl.seekSettleMs || 1200;
  state.castControl.seekQueued = false;
  state.castControl.seekInFlight = true;
  beginCastCommand('seek', settleMs);
  try {
    const res = await api('/api/cast/controls', { method: 'POST', body: { action: 'seek', value: val } });
    if (!isCurrentCastCommand(cmdId)) return;
    if (res && res.state) state.casting.state = res.state;
  } catch (e) {
    // If seek fails, let polling correct state once settle window passes.
  } finally {
    endCastCommand();
    state.castControl.seekInFlight = false;
    const latest = state.castControl.lastSeekTarget;
    if (state.casting.lastUserCommand === 'seek' && (state.castControl.seekQueued || latest !== val)) {
      state.castControl.seekQueued = false;
      scheduleSeekDispatch(0);
    }
  }
}

function requestSeekTo(seconds, { settleMs = 1200 } = {}) {
  if (!state.casting.duration || state.casting.duration <= 0) return;
  const clamped = Math.max(0, Math.min(state.casting.duration, seconds));
  const target = Math.floor(clamped);
  markCastCommand('seek');

  // Optimistic UI update immediately
  state.casting.currentTime = clamped;
  state.castControl.lastKnownTime = clamped;
  state.castControl.lastKnownDuration = state.casting.duration;
  updateMiniPlayer();

  // Debounce seeks (scrubber/keyboard can fire quickly)
  state.castControl.lastSeekTarget = target;
  state.castControl.seekSettleMs = settleMs;
  scheduleSeekDispatch(180);
}

async function setVolume(val) {
  val = Math.max(0, Math.min(100, parseInt(val)));
  beginCastCommand('volume', 600);
  try {
    await api('/api/cast/controls', { method: 'POST', body: { action: 'volume', value: val } });
    state.casting.volume = val;
    localStorage.setItem('cm_volume', String(val));
  } finally {
    endCastCommand();
  }
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
  // Avoid double-seeks: a drag release can still fire a click.
  if (state.isDraggingScrubber) return;
  if (nowMs() < (state.castControl.ignoreNextScrubberClickUntil || 0)) return;
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
    // Suppress the subsequent click event that would otherwise trigger a second seek.
    state.castControl.ignoreNextScrubberClickUntil = nowMs() + 400;
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
    await api('/api/cast', {
      method: 'POST',
      body: {
        filePath: item.filePath,
        seekTo,
        autoTranscode: state.settings?.autoTranscode || 'auto',
        ...(state.settings?.allowPretranscode === true ? { allowPretranscode: true } : {}),
      },
    });
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
    const provider = document.getElementById('setting-provider')?.value || 'all';
    const data = await api('/api/cast/devices/scan', { method: 'POST', body: { provider } });
    state.castDevices = data.devices || [];
    renderCastDevices();
    const errorText = (data.errors || []).map(e => `${e.provider}: ${e.error}`).join(' · ');
    toast(`Found ${state.castDevices.length} device(s)${errorText ? ` (${errorText})` : ''}`, data.errors?.length ? 'warning' : 'success', 7000);
  } catch (e) { /* toasted */ }
}

async function loadCastDevices() {
  try {
    const data = await api('/api/cast/devices?provider=all', { silent: true });
    state.castDevices = data.devices || [];
    state.selectedCastDevice = state.castDevices.find(d => d.selected) || state.castDevices[0] || null;
    renderCastDevices();
  } catch (e) { /* ignore */ }
}

function renderCastDevices() {
  const select = document.getElementById('setting-device');
  if (!select) return;
  const devices = state.castDevices || [];
  if (!devices.length) {
    select.innerHTML = '<option value="">No devices scanned</option>';
  } else {
    select.innerHTML = devices.map((d) => {
      const value = `${d.provider}|${d.device_id || d.id}`;
      const host = d.host ? ` · ${d.host}` : '';
      const paired = d.provider === 'airplay' ? (d.paired ? ' · paired' : ' · needs pairing') : '';
      return `<option value="${esc(value)}" ${d.selected ? 'selected' : ''}>${esc(d.name || d.device_id || d.id)} (${esc(d.provider)}${host}${paired})</option>`;
    }).join('');
  }
  state.selectedCastDevice = devices.find(d => d.selected) || state.selectedCastDevice || devices[0] || null;
  const label = document.getElementById('device-name');
  if (label && state.selectedCastDevice) label.textContent = `${state.selectedCastDevice.name || 'Device'} · ${state.selectedCastDevice.provider}`;
  const modalTarget = document.getElementById('cast-target-label');
  if (modalTarget && state.selectedCastDevice) modalTarget.textContent = `${state.selectedCastDevice.name || 'Device'} (${state.selectedCastDevice.provider})`;
}

async function selectDevice(value) {
  const [provider, deviceId] = String(value || '').split('|');
  if (!provider || !deviceId) return;
  const result = await api('/api/cast/devices/select', { method: 'POST', body: { provider, deviceId } });
  state.castDevices.forEach(d => { d.selected = (d.device_id || d.id) === result.deviceId; });
  state.selectedCastDevice = state.castDevices.find(d => d.selected) || { provider, device_id: result.deviceId, name: result.name || deviceId };
  renderCastDevices();
  toast(`Device: ${state.selectedCastDevice.name || result.deviceId}`, 'info');
}

async function startAirPlayPairing() {
  const selected = state.selectedCastDevice;
  if (!selected || selected.provider !== 'airplay') return toast('Select an AirPlay device first.', 'warning');
  try {
    const deviceId = selected.device_id || selected.id;
    const start = await api('/api/cast/airplay/pair/start', { method: 'POST', body: { deviceId, host: selected.host } });
    const pin = prompt(start.message || 'Enter the AirPlay pairing PIN:');
    if (!pin) return;
    await api('/api/cast/airplay/pair/finish', { method: 'POST', body: { deviceId: start.deviceId || deviceId, pin } });
    toast('AirPlay device paired', 'success');
    await scanDevices();
  } catch (e) { /* toasted */ }
}

async function loadReceiverStatus() {
  try {
    state.receiverStatus = await api('/api/receiver/status', { silent: true });
    renderReceiverStatus();
  } catch (e) { /* ignore */ }
}

function renderReceiverStatus() {
  const el = document.getElementById('receiver-status');
  if (!el) return;
  const s = state.receiverStatus;
  if (!s) {
    el.textContent = 'Receiver status unavailable';
    return;
  }
  const warnings = Array.isArray(s.warnings) ? s.warnings : [];
  el.innerHTML = `
    <div>${esc(s.backend)} · ${esc(s.status)}${s.avahi ? ` · Avahi ${esc(s.avahi)}` : ''}${s.localIp ? ` · ${esc(s.localIp)}` : ''}</div>
    <div class="setting-hint">${s.displayConnected ? 'Display detected' : 'No HDMI/DP display detected'} · ${s.audioSinkAvailable ? 'Audio sink detected' : 'No TV/audio sink detected'}</div>
    ${warnings.map((w) => `<div class="setting-warning">${esc(w)}</div>`).join('')}
  `;
}

async function controlReceiver(action) {
  try {
    state.receiverStatus = await api(`/api/receiver/${action}`, { method: 'POST' });
    renderReceiverStatus();
    toast(`Receiver ${action} requested`, 'info');
  } catch (e) { /* toasted */ }
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
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's' && state.currentSection === 'library') {
      e.preventDefault();
      savePreviewFile();
      return;
    }

    // Don't trigger when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    switch (e.key) {
      case 'Delete':
      case 'Backspace':
        if (state.currentSection === 'library' && state.selectedFile) {
          e.preventDefault();
          deleteFile(state.selectedFile.path, state.selectedFile.name);
        }
        break;
      case 'Enter':
        if (state.currentSection === 'library' && state.selectedFile) {
          e.preventDefault();
          openSelectedFile(state.selectedFile.path);
        }
        break;
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

async function navigateToFile(filePath, type = 'file') {
  const itemType = String(type || '').toLowerCase();
  showSection('library', { skipLoad: true });
  clearFileSearch();
  if (pathHasHiddenSegment(filePath) && !state.showHidden) {
    state.showHidden = true;
    localStorage.setItem('cm_show_hidden', 'true');
  }
  if (itemType === 'folder' || itemType === 'directory') {
    await loadFiles(filePath, { force: true });
    return;
  }
  const dir = dirnamePath(filePath);
  await loadFiles(dir);
  selectFile(filePath);
  scrollFileIntoView(filePath);
}

// ═══════════════════════════════════════════════════════════════
// STARRED FILES
// ═══════════════════════════════════════════════════════════════

async function loadStarred() {
  try {
    const data = await api('/api/files/starred');
    const container = document.getElementById('starred-list');
    state.starredPaths = new Set((data.files || []).map(f => f.file_path));
    if (!data.files || data.files.length === 0) {
      container.innerHTML = '<div class="empty-state"><p>No starred files or folders.</p><p class="empty-hint">Click the star icon on any item to add it here.</p></div>';
      return;
    }
    container.innerHTML = data.files.map(f => {
      const itemType = f.item_type || f.file_type || 'file';
      const name = f.name || basename(f.file_path);
      const pathArg = jsArg(f.file_path);
      const time = f.starred_at ? new Date(f.starred_at).toLocaleString() : '';
      const click = itemType === 'folder' ? `openStarredFolder('${pathArg}')` : `navigateToFile('${pathArg}')`;
      const icon = itemType === 'folder' ? '&#128193;' : '&#128196;';
      return `<div class="file-item" onclick="${click}">
        <div class="file-icon">${icon}</div>
        <div class="file-info">
          <div class="file-name">${esc(name)}</div>
          <div class="file-meta"><span>${itemType === 'folder' ? 'Folder' : 'File'}</span><span>Starred ${time}</span></div>
        </div>
        <div class="file-actions">
          <button onclick="event.stopPropagation(); setStar('${pathArg}', false, '${itemType}')">Unstar</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) { /* toasted */ }
}

async function loadStarredFolders() {
  try {
    const data = await api('/api/files/starred-folders', { silent: true });
    state.starredFolders = data.files || [];
    renderStarredFolders();
  } catch (e) { /* toasted */ }
}

function renderStarredFolders() {
  const panel = document.getElementById('starred-folder-panel');
  const list = document.getElementById('starred-folder-list');
  if (!panel || !list) return;
  if (!state.starredFolders.length) {
    panel.style.display = 'none';
    list.innerHTML = '';
    return;
  }
  panel.style.display = 'block';
  list.innerHTML = state.starredFolders.map((f) => {
    const pathArg = jsArg(f.file_path);
    const label = esc(f.name || basename(f.file_path));
    return `<button class="starred-folder-item" onclick="openStarredFolder('${pathArg}')" title="${esc(f.file_path)}">
      <span class="starred-folder-icon">&#9733;</span>
      <span class="starred-folder-name">${label}</span>
    </button>`;
  }).join('');
}

async function openStarredFolder(filePath) {
  state.skipNextLibraryAutoLoad = true;
  showSection('library', { skipLoad: true });
  clearFileSearch();
  await loadFiles(filePath, { force: true });
}

async function setStar(filePath, shouldStar, itemType = 'file') {
  const type = itemType === 'folder' ? 'folder' : 'file';
  if (shouldStar) {
    await api('/api/files/star', { method: 'POST', body: { path: filePath, type } });
    state.starredPaths.add(filePath);
    toast(type === 'folder' ? 'Folder added to starred' : 'Added to starred', 'success');
  } else {
    await api('/api/files/star', { method: 'DELETE', body: { path: filePath } });
    state.starredPaths.delete(filePath);
    toast(type === 'folder' ? 'Folder removed from starred' : 'Removed from starred', 'info');
  }

  for (const f of state.files) {
    if (f.path === filePath) f.starred = shouldStar;
  }
  for (const f of state.recursiveSearchResults) {
    if (f.path === filePath) f.starred = shouldStar;
  }
  if (type === 'folder') await loadStarredFolders();
  if (state.currentSection === 'starred') loadStarred();
  if (state.currentSection === 'library') renderFiles();
}

async function toggleStar(filePath, itemType = 'file') {
  try {
    const f = findFile(filePath);
    const currentlyStarred = !!f?.starred || state.starredPaths.has(filePath);
    await setStar(filePath, !currentlyStarred, itemType || f?.type || 'file');
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
