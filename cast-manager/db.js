// ═══════════════════════════════════════════════════════════════
// Cast Manager v4 — SQLite Database Layer
// ═══════════════════════════════════════════════════════════════

const Database = require('better-sqlite3');
const path = require('path');
const crypto = require('crypto');

const DB_PATH = path.join(__dirname, 'cast_manager.db');
let db;

function initDB() {
  db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');

  // ─── Stream Tokens ─────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS stream_tokens (
      token TEXT PRIMARY KEY,
      file_path TEXT NOT NULL,
      filename TEXT NOT NULL,
      expires_at DATETIME NOT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      access_count INTEGER DEFAULT 0,
      last_accessed DATETIME
    );
    CREATE INDEX IF NOT EXISTS idx_stream_tokens_expires ON stream_tokens(expires_at);
  `);

  // ─── Activity Log ──────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS activity (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      action TEXT NOT NULL,
      file_path TEXT,
      details TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_activity_date ON activity(created_at);
  `);

  // ─── Starred Files ────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS starred (
      file_path TEXT PRIMARY KEY,
      starred_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
  `);

  // ─── Recent Files ─────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS recent_files (
      file_path TEXT PRIMARY KEY,
      filename TEXT,
      file_type TEXT,
      action TEXT NOT NULL,
      accessed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_recent_accessed ON recent_files(accessed_at);
  `);

  // ─── Trash ────────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS trash (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      original_path TEXT NOT NULL,
      trash_path TEXT NOT NULL,
      filename TEXT,
      file_type TEXT,
      size INTEGER DEFAULT 0,
      deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      auto_delete_at DATETIME
    );
    CREATE INDEX IF NOT EXISTS idx_trash_auto_delete ON trash(auto_delete_at);
  `);

  // ─── Shares ───────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS shares (
      id TEXT PRIMARY KEY,
      file_path TEXT NOT NULL,
      filename TEXT,
      is_directory BOOLEAN DEFAULT 0,
      permissions TEXT DEFAULT 'view',
      password_hash TEXT,
      expires_at DATETIME,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      access_count INTEGER DEFAULT 0,
      is_active BOOLEAN DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_shares_active ON shares(is_active, expires_at);
  `);

  // ─── Tags ─────────────────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS tags (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL,
      color TEXT DEFAULT '#6366f1'
    );
    CREATE TABLE IF NOT EXISTS file_tags (
      file_path TEXT NOT NULL,
      tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
      PRIMARY KEY (file_path, tag_id)
    );
  `);

  // ─── Watch Progress ───────────────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS watch_progress (
      file_path TEXT PRIMARY KEY,
      position_seconds REAL,
      duration_seconds REAL,
      last_watched DATETIME DEFAULT CURRENT_TIMESTAMP,
      completed BOOLEAN DEFAULT 0
    );
  `);

  // ─── File Index (for search) ──────────────────────────────
  db.exec(`
    CREATE TABLE IF NOT EXISTS file_index (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      path TEXT UNIQUE NOT NULL,
      name TEXT NOT NULL,
      extension TEXT,
      size INTEGER DEFAULT 0,
      mime_type TEXT,
      is_directory BOOLEAN DEFAULT 0,
      parent_path TEXT,
      modified_at REAL,
      indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_file_index_parent ON file_index(parent_path);
    CREATE INDEX IF NOT EXISTS idx_file_index_ext ON file_index(extension);
  `);

  // FTS5 virtual table for full-text search
  try {
    db.exec(`
      CREATE VIRTUAL TABLE IF NOT EXISTS file_search USING fts5(name, path, extension);
    `);
  } catch (e) {
    console.log('[DB] FTS5 table already exists or not supported:', e.message);
  }

  // Clean expired tokens on startup
  cleanExpiredTokens();
  cleanExpiredShares();

  // Schedule cleanup every hour
  setInterval(() => {
    cleanExpiredTokens();
    cleanExpiredShares();
    cleanExpiredTrash();
  }, 60 * 60 * 1000);

  console.log('[DB] SQLite database initialized at', DB_PATH);
  return db;
}

function getDB() {
  if (!db) initDB();
  return db;
}

// ─── Token Helpers ───────────────────────────────────────────

function generateStreamToken(filePath, filename, expiresInHours = 24) {
  const d = getDB();
  const token = crypto.randomBytes(32).toString('hex');
  const expiresAt = new Date(Date.now() + expiresInHours * 60 * 60 * 1000).toISOString();

  d.prepare(`
    INSERT INTO stream_tokens (token, file_path, filename, expires_at)
    VALUES (?, ?, ?, ?)
  `).run(token, filePath, filename, expiresAt);

  return { token, expiresAt };
}

function validateStreamToken(token) {
  const d = getDB();
  const row = d.prepare(`
    SELECT * FROM stream_tokens WHERE token = ? AND expires_at > datetime('now')
  `).get(token);

  if (row) {
    d.prepare(`
      UPDATE stream_tokens SET access_count = access_count + 1, last_accessed = datetime('now')
      WHERE token = ?
    `).run(token);
  }

  return row || null;
}

function listStreamTokens() {
  const d = getDB();
  return d.prepare(`SELECT * FROM stream_tokens WHERE expires_at > datetime('now') ORDER BY created_at DESC`).all();
}

function revokeStreamToken(token) {
  const d = getDB();
  return d.prepare(`DELETE FROM stream_tokens WHERE token = ?`).run(token);
}

function cleanExpiredTokens() {
  const d = getDB();
  const result = d.prepare(`DELETE FROM stream_tokens WHERE expires_at <= datetime('now')`).run();
  if (result.changes > 0) console.log(`[DB] Cleaned ${result.changes} expired stream tokens`);
}

// ─── Activity Helpers ────────────────────────────────────────

function logActivity(action, filePath, details = null) {
  const d = getDB();
  d.prepare(`
    INSERT INTO activity (action, file_path, details) VALUES (?, ?, ?)
  `).run(action, filePath, details ? JSON.stringify(details) : null);
}

function getActivity(page = 1, limit = 50, type = null) {
  const d = getDB();
  const offset = (page - 1) * limit;
  if (type) {
    return d.prepare(`SELECT * FROM activity WHERE action = ? ORDER BY created_at DESC LIMIT ? OFFSET ?`).all(type, limit, offset);
  }
  return d.prepare(`SELECT * FROM activity ORDER BY created_at DESC LIMIT ? OFFSET ?`).all(limit, offset);
}

// ─── Recent Files ────────────────────────────────────────────

function trackRecent(filePath, action = 'open', filename = null, fileType = null) {
  const d = getDB();
  d.prepare(`
    INSERT OR REPLACE INTO recent_files (file_path, filename, file_type, action, accessed_at)
    VALUES (?, ?, ?, ?, datetime('now'))
  `).run(filePath, filename || require('path').basename(filePath), fileType, action);
}

function getRecent(limit = 50) {
  const d = getDB();
  return d.prepare(`SELECT * FROM recent_files ORDER BY accessed_at DESC LIMIT ?`).all(limit);
}

// ─── Starred ─────────────────────────────────────────────────

function starFile(filePath) {
  const d = getDB();
  d.prepare(`INSERT OR IGNORE INTO starred (file_path) VALUES (?)`).run(filePath);
}

function unstarFile(filePath) {
  const d = getDB();
  d.prepare(`DELETE FROM starred WHERE file_path = ?`).run(filePath);
}

function isStarred(filePath) {
  const d = getDB();
  return !!d.prepare(`SELECT 1 FROM starred WHERE file_path = ?`).get(filePath);
}

function getStarred() {
  const d = getDB();
  return d.prepare(`SELECT * FROM starred ORDER BY starred_at DESC`).all();
}

// ─── Trash ───────────────────────────────────────────────────

function addToTrash(originalPath, trashPath, filename, fileType, size) {
  const d = getDB();
  const autoDeleteAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
  d.prepare(`
    INSERT INTO trash (original_path, trash_path, filename, file_type, size, auto_delete_at)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(originalPath, trashPath, filename, fileType, size, autoDeleteAt);
}

function getTrash() {
  const d = getDB();
  return d.prepare(`SELECT * FROM trash ORDER BY deleted_at DESC`).all();
}

function getTrashItem(id) {
  const d = getDB();
  return d.prepare(`SELECT * FROM trash WHERE id = ?`).get(id);
}

function removeFromTrash(id) {
  const d = getDB();
  return d.prepare(`DELETE FROM trash WHERE id = ?`).run(id);
}

function cleanExpiredTrash() {
  const d = getDB();
  const expired = d.prepare(`SELECT * FROM trash WHERE auto_delete_at <= datetime('now')`).all();
  if (expired.length > 0) {
    d.prepare(`DELETE FROM trash WHERE auto_delete_at <= datetime('now')`).run();
    console.log(`[DB] Cleaned ${expired.length} expired trash items`);
  }
  return expired;
}

// ─── Shares ──────────────────────────────────────────────────

function createShare(id, filePath, filename, isDirectory, permissions, passwordHash, expiresAt) {
  const d = getDB();
  d.prepare(`
    INSERT INTO shares (id, file_path, filename, is_directory, permissions, password_hash, expires_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(id, filePath, filename, isDirectory ? 1 : 0, permissions, passwordHash, expiresAt);
}

function getShare(id) {
  const d = getDB();
  const share = d.prepare(`SELECT * FROM shares WHERE id = ? AND is_active = 1`).get(id);
  if (share && share.expires_at && new Date(share.expires_at) < new Date()) return null;
  if (share) {
    d.prepare(`UPDATE shares SET access_count = access_count + 1 WHERE id = ?`).run(id);
  }
  return share || null;
}

function listShares() {
  const d = getDB();
  return d.prepare(`SELECT * FROM shares WHERE is_active = 1 ORDER BY created_at DESC`).all();
}

function revokeShare(id) {
  const d = getDB();
  return d.prepare(`UPDATE shares SET is_active = 0 WHERE id = ?`).run(id);
}

function updateShare(id, updates) {
  const d = getDB();
  const fields = [];
  const values = [];
  if (updates.permissions !== undefined) { fields.push('permissions = ?'); values.push(updates.permissions); }
  if (updates.expires_at !== undefined) { fields.push('expires_at = ?'); values.push(updates.expires_at); }
  if (updates.password_hash !== undefined) { fields.push('password_hash = ?'); values.push(updates.password_hash); }
  if (fields.length === 0) return;
  values.push(id);
  d.prepare(`UPDATE shares SET ${fields.join(', ')} WHERE id = ?`).run(...values);
}

function cleanExpiredShares() {
  const d = getDB();
  const result = d.prepare(`UPDATE shares SET is_active = 0 WHERE expires_at IS NOT NULL AND expires_at <= datetime('now') AND is_active = 1`).run();
  if (result.changes > 0) console.log(`[DB] Deactivated ${result.changes} expired shares`);
}

// ─── Tags ────────────────────────────────────────────────────

function createTag(name, color = '#6366f1') {
  const d = getDB();
  try {
    d.prepare(`INSERT INTO tags (name, color) VALUES (?, ?)`).run(name, color);
    return d.prepare(`SELECT * FROM tags WHERE name = ?`).get(name);
  } catch (e) {
    return d.prepare(`SELECT * FROM tags WHERE name = ?`).get(name);
  }
}

function getTags() {
  const d = getDB();
  return d.prepare(`
    SELECT t.*, COUNT(ft.file_path) as file_count 
    FROM tags t LEFT JOIN file_tags ft ON t.id = ft.tag_id 
    GROUP BY t.id ORDER BY t.name
  `).all();
}

function tagFile(filePath, tagId) {
  const d = getDB();
  d.prepare(`INSERT OR IGNORE INTO file_tags (file_path, tag_id) VALUES (?, ?)`).run(filePath, tagId);
}

function untagFile(filePath, tagId) {
  const d = getDB();
  d.prepare(`DELETE FROM file_tags WHERE file_path = ? AND tag_id = ?`).run(filePath, tagId);
}

function getFileTags(filePath) {
  const d = getDB();
  return d.prepare(`
    SELECT t.* FROM tags t 
    JOIN file_tags ft ON t.id = ft.tag_id 
    WHERE ft.file_path = ? ORDER BY t.name
  `).all(filePath);
}

function getFilesByTag(tagId) {
  const d = getDB();
  return d.prepare(`SELECT file_path FROM file_tags WHERE tag_id = ?`).all(tagId);
}

// ─── Search Index ────────────────────────────────────────────

function indexFile(filePath, name, extension, size, isDirectory, parentPath, modifiedAt) {
  const d = getDB();
  d.prepare(`
    INSERT OR REPLACE INTO file_index (path, name, extension, size, is_directory, parent_path, modified_at, indexed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
  `).run(filePath, name, extension, size, isDirectory ? 1 : 0, parentPath, modifiedAt);

  // Update FTS index
  try {
    d.prepare(`INSERT OR REPLACE INTO file_search (rowid, name, path, extension) VALUES ((SELECT id FROM file_index WHERE path = ?), ?, ?, ?)`).run(filePath, name, filePath, extension);
  } catch (e) { /* FTS may not be available */ }
}

function searchFiles(query, limit = 50) {
  const d = getDB();
  try {
    // Try FTS5 search first
    return d.prepare(`
      SELECT fi.* FROM file_index fi
      JOIN file_search fs ON fi.id = fs.rowid
      WHERE file_search MATCH ?
      ORDER BY rank LIMIT ?
    `).all(query + '*', limit);
  } catch (e) {
    // Fallback to LIKE search
    return d.prepare(`
      SELECT * FROM file_index WHERE name LIKE ? ORDER BY name LIMIT ?
    `).all(`%${query}%`, limit);
  }
}

function clearFileIndex() {
  const d = getDB();
  d.prepare(`DELETE FROM file_index`).run();
  try { d.prepare(`DELETE FROM file_search`).run(); } catch (e) { /* ok */ }
}

module.exports = {
  initDB, getDB,
  // Tokens
  generateStreamToken, validateStreamToken, listStreamTokens, revokeStreamToken, cleanExpiredTokens,
  // Activity
  logActivity, getActivity,
  // Recent
  trackRecent, getRecent,
  // Starred
  starFile, unstarFile, isStarred, getStarred,
  // Trash
  addToTrash, getTrash, getTrashItem, removeFromTrash, cleanExpiredTrash,
  // Shares
  createShare, getShare, listShares, revokeShare, updateShare, cleanExpiredShares,
  // Tags
  createTag, getTags, tagFile, untagFile, getFileTags, getFilesByTag,
  // Search
  indexFile, searchFiles, clearFileIndex,
};
