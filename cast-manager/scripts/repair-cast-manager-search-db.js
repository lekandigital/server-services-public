#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const Database = require('better-sqlite3');

const dbPath = path.resolve(process.argv[2] || path.join(__dirname, '..', 'cast_manager.db'));
const dbDir = path.dirname(dbPath);
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const backupDir = path.join(dbDir, 'db-backups', `search-repair-${stamp}`);
const tempPath = `${dbPath}.repaired-${process.pid}`;

const preservedTables = [
  'stream_tokens',
  'activity',
  'starred',
  'cast_devices',
  'recent_files',
  'trash',
  'shares',
  'tags',
  'file_tags',
  'watch_progress',
];

function copyIfPresent(source, destination) {
  if (!fs.existsSync(source)) return;
  fs.copyFileSync(source, destination);
  fs.chmodSync(destination, 0o600);
}

function integrityOk(rows) {
  return rows.length === 1 && rows[0].integrity_check === 'ok';
}

if (!fs.existsSync(dbPath)) {
  throw new Error(`Database not found: ${dbPath}`);
}
if (fs.existsSync(tempPath)) fs.unlinkSync(tempPath);

fs.mkdirSync(backupDir, { recursive: true, mode: 0o700 });
copyIfPresent(dbPath, path.join(backupDir, 'cast_manager.db'));
copyIfPresent(`${dbPath}-wal`, path.join(backupDir, 'cast_manager.db-wal'));
copyIfPresent(`${dbPath}-shm`, path.join(backupDir, 'cast_manager.db-shm'));

const source = new Database(dbPath, { readonly: true, fileMustExist: true });
const before = source.pragma('integrity_check');
if (integrityOk(before)) {
  source.close();
  console.log(`Database is already healthy. Backup saved at ${backupDir}`);
  process.exit(0);
}

const schemas = source.prepare(`
  SELECT type, name, tbl_name, sql
  FROM sqlite_master
  WHERE sql IS NOT NULL
  ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, rootpage
`).all();

const exported = {};
for (const table of preservedTables) {
  try {
    exported[table] = source.prepare(`SELECT * FROM "${table}"`).all();
  } catch (error) {
    source.close();
    throw new Error(`Refusing repair because ${table} could not be exported: ${error.message}`);
  }
}

const exportPath = path.join(backupDir, 'preserved-data.json');
fs.writeFileSync(exportPath, JSON.stringify({ createdAt: new Date().toISOString(), tables: exported }, null, 2), { mode: 0o600 });

const rebuilt = new Database(tempPath);
rebuilt.pragma('journal_mode = DELETE');
rebuilt.pragma('foreign_keys = OFF');

for (const table of preservedTables) {
  const schema = schemas.find((row) => row.type === 'table' && row.name === table);
  if (!schema) throw new Error(`Missing schema for preserved table ${table}`);
  rebuilt.exec(schema.sql);
}

for (const row of schemas) {
  if (row.type === 'index' && preservedTables.includes(row.tbl_name) && !row.name.startsWith('sqlite_autoindex_')) {
    rebuilt.exec(row.sql);
  }
}

const fileIndexSchema = schemas.find((row) => row.type === 'table' && row.name === 'file_index');
if (!fileIndexSchema) throw new Error('Missing file_index schema');
rebuilt.exec(fileIndexSchema.sql);
for (const row of schemas) {
  if (row.type === 'index' && row.tbl_name === 'file_index' && !row.name.startsWith('sqlite_autoindex_')) rebuilt.exec(row.sql);
}

const searchSchema = schemas.find((row) => row.type === 'table' && row.name === 'file_search');
rebuilt.exec(searchSchema?.sql || 'CREATE VIRTUAL TABLE file_search USING fts5(name, path, extension)');

const importAll = rebuilt.transaction(() => {
  for (const table of preservedTables) {
    const rows = exported[table];
    if (!rows.length) continue;
    const columns = Object.keys(rows[0]);
    const quoted = columns.map((column) => `"${column}"`).join(', ');
    const values = columns.map(() => '?').join(', ');
    const insert = rebuilt.prepare(`INSERT INTO "${table}" (${quoted}) VALUES (${values})`);
    for (const row of rows) insert.run(...columns.map((column) => row[column]));
  }
});
importAll();

rebuilt.pragma('foreign_keys = ON');
const foreignKeyErrors = rebuilt.pragma('foreign_key_check');
if (foreignKeyErrors.length) throw new Error(`Foreign key check failed: ${JSON.stringify(foreignKeyErrors.slice(0, 10))}`);
rebuilt.exec('VACUUM');
const after = rebuilt.pragma('integrity_check');
if (!integrityOk(after)) throw new Error(`Rebuilt database failed integrity check: ${JSON.stringify(after.slice(0, 5))}`);
rebuilt.pragma('journal_mode = WAL');
rebuilt.close();
source.close();

const originalMode = fs.statSync(dbPath).mode;
fs.renameSync(dbPath, path.join(backupDir, 'cast_manager.db.live-original'));
for (const suffix of ['-wal', '-shm']) {
  const liveSidecar = `${dbPath}${suffix}`;
  if (fs.existsSync(liveSidecar)) fs.unlinkSync(liveSidecar);
}
fs.renameSync(tempPath, dbPath);
fs.chmodSync(dbPath, originalMode);

const counts = Object.fromEntries(preservedTables.map((table) => [table, exported[table].length]));
console.log(JSON.stringify({
  repaired: true,
  database: dbPath,
  backupDir,
  preservedRows: counts,
  searchIndexRows: 0,
  integrity: 'ok',
}, null, 2));
