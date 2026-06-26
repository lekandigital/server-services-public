const fs = require('fs');
const path = require('path');
const archiver = require('archiver');

async function createDebugBundle({ diagnosticsDir, outDir, sessionId, extras = {} } = {}) {
  fs.mkdirSync(outDir, { recursive: true });
  const bundlePath = path.join(outDir, `cast-debug-${sessionId || Date.now()}.zip`);
  const output = fs.createWriteStream(bundlePath);
  const archive = archiver('zip', { zlib: { level: 9 } });

  const done = new Promise((resolve, reject) => {
    output.on('close', () => resolve(bundlePath));
    archive.on('error', reject);
  });

  archive.pipe(output);

  const addJson = (name, data) => {
    archive.append(JSON.stringify(data, null, 2), { name });
  };

  if (extras.summaryMarkdown) archive.append(extras.summaryMarkdown, { name: 'summary.md' });
  if (extras.envReport) archive.append(extras.envReport, { name: 'env-report.txt' });
  if (extras.diagnostics) addJson('diagnostics.json', extras.diagnostics);
  if (extras.doctor) addJson('doctor.json', extras.doctor);
  if (extras.gitStatus) archive.append(extras.gitStatus, { name: 'git-status.txt' });

  const latestPath = path.join(diagnosticsDir, 'latest.json');
  if (fs.existsSync(latestPath)) archive.file(latestPath, { name: 'latest.json' });
  const doctorPath = path.join(diagnosticsDir, 'doctor-latest.json');
  if (fs.existsSync(doctorPath)) archive.file(doctorPath, { name: 'doctor-latest.json' });

  for (const [name, content] of Object.entries(extras.textFiles || {})) {
    archive.append(String(content), { name });
  }

  await archive.finalize();
  return done;
}

module.exports = {
  createDebugBundle,
};
