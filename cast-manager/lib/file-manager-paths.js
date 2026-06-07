const path = require('path').posix;

const PROTECTED_NAMES = new Set([
  '.env',
  '.env.local',
  '.env.production',
  '.npmrc',
  '.ssh',
  'id_rsa',
  'id_dsa',
  'id_ecdsa',
  'id_ed25519',
  'authorized_keys',
  'known_hosts',
]);

const PROTECTED_EXTENSIONS = new Set([
  '.db',
  '.sqlite',
  '.sqlite3',
  '.key',
  '.pem',
  '.p12',
  '.pfx',
]);

function normalizeAbsolutePath(input) {
  const value = String(input || '').replace(/\0/g, '').trim();
  if (!value) throw filePathError('INVALID_PATH', 'Path is required');
  if (!path.isAbsolute(value)) throw filePathError('INVALID_PATH', 'Path must be absolute after resolution');
  const normalized = path.normalize(value);
  return normalized === '/' ? '/' : normalized.replace(/\/+$/, '');
}

function normalizeRoot(root) {
  return normalizeAbsolutePath(root);
}

function isInsideRoot(root, target) {
  const safeRoot = normalizeRoot(root);
  const safeTarget = normalizeAbsolutePath(target);
  if (safeRoot === '/') return safeTarget.startsWith('/');
  return safeTarget === safeRoot || safeTarget.startsWith(`${safeRoot}/`);
}

function resolveSafePath(root, inputPath = '') {
  const safeRoot = normalizeRoot(root);
  const raw = String(inputPath || '').replace(/\0/g, '').trim();
  if (!raw || raw === '.' || raw === '/') return safeRoot;
  if (raw.includes('\\')) throw filePathError('INVALID_PATH', 'Backslashes are not valid in remote paths');
  if (raw.split('/').filter(Boolean).includes('..')) {
    throw filePathError('INVALID_PATH', 'Path traversal segments are not allowed');
  }

  const candidate = path.isAbsolute(raw)
    ? path.normalize(raw)
    : path.normalize(path.join(safeRoot, raw));

  if (!isInsideRoot(safeRoot, candidate)) {
    throw filePathError('INVALID_PATH', 'Path is outside the configured file-manager root');
  }
  return normalizeAbsolutePath(candidate);
}

function toRelativePath(root, absolutePath) {
  const safeRoot = normalizeRoot(root);
  const safePath = resolveSafePath(safeRoot, absolutePath);
  if (safePath === safeRoot) return '';
  if (safeRoot === '/') return safePath.slice(1);
  return safePath.slice(safeRoot.length + 1);
}

function parentPath(root, absolutePath) {
  const safeRoot = normalizeRoot(root);
  const safePath = resolveSafePath(safeRoot, absolutePath);
  if (safePath === safeRoot) return safeRoot;
  const parent = path.dirname(safePath);
  return isInsideRoot(safeRoot, parent) ? parent : safeRoot;
}

function validateItemName(name) {
  const value = String(name || '').trim();
  if (!value) throw filePathError('INVALID_NAME', 'Name is required');
  if (value === '.' || value === '..') throw filePathError('INVALID_NAME', 'Name cannot be "." or ".."');
  if (value.includes('/') || value.includes('\\') || value.includes('\0')) {
    throw filePathError('INVALID_NAME', 'Name cannot contain path separators');
  }
  return value;
}

function joinChild(root, parent, name) {
  const safeParent = resolveSafePath(root, parent);
  const safeName = validateItemName(name);
  const child = path.join(safeParent, safeName);
  if (!isInsideRoot(root, child)) {
    throw filePathError('INVALID_PATH', 'Target path is outside the configured file-manager root');
  }
  return normalizeAbsolutePath(child);
}

function buildBreadcrumbs(root, absolutePath, rootLabel = 'Workspace') {
  const safeRoot = normalizeRoot(root);
  const safePath = resolveSafePath(safeRoot, absolutePath);
  const breadcrumbs = [{ label: rootLabel, path: safeRoot, relativePath: '' }];
  const rel = toRelativePath(safeRoot, safePath);
  if (!rel) return breadcrumbs;

  let current = safeRoot;
  for (const segment of rel.split('/').filter(Boolean)) {
    current = path.join(current, segment);
    breadcrumbs.push({ label: segment, path: current, relativePath: toRelativePath(safeRoot, current) });
  }
  return breadcrumbs;
}

function getRootLabel(root, fallback = 'Workspace') {
  const name = path.basename(normalizeRoot(root));
  return name || fallback;
}

function hasHiddenSegment(root, absolutePath) {
  const rel = toRelativePath(root, absolutePath);
  return rel.split('/').some((part) => part.startsWith('.') && part.length > 1);
}

function isProtectedPath(root, absolutePath) {
  const rel = toRelativePath(root, absolutePath);
  if (!rel) return false;
  const parts = rel.toLowerCase().split('/').filter(Boolean);
  return parts.some((part) => {
    if (PROTECTED_NAMES.has(part)) return true;
    const ext = path.extname(part);
    if (PROTECTED_EXTENSIONS.has(ext)) return true;
    return part.includes('token') || part.includes('secret') || part.includes('cookie') || part.includes('credential');
  });
}

function filePathError(code, message) {
  const err = new Error(message);
  err.code = code;
  return err;
}

module.exports = {
  buildBreadcrumbs,
  filePathError,
  getRootLabel,
  hasHiddenSegment,
  isInsideRoot,
  isProtectedPath,
  joinChild,
  normalizeAbsolutePath,
  normalizeRoot,
  parentPath,
  resolveSafePath,
  toRelativePath,
  validateItemName,
};
