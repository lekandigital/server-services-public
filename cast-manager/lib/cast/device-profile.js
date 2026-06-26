const fs = require('fs');
const path = require('path');

function loadDeviceProfiles(profilePath) {
  try {
    if (fs.existsSync(profilePath)) {
      return JSON.parse(fs.readFileSync(profilePath, 'utf8'));
    }
  } catch (_) {}
  return {};
}

function saveDeviceProfiles(profilePath, profiles) {
  const dir = path.dirname(profilePath);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(profilePath, JSON.stringify(profiles, null, 2));
}

function getDeviceKey(deviceName, receiverIp = '') {
  return String(deviceName || receiverIp || 'unknown').trim();
}

function getOrCreateProfile(profiles, deviceName, defaults = {}) {
  const key = getDeviceKey(deviceName);
  if (!profiles[key]) {
    profiles[key] = {
      deviceName: key,
      receiverIp: defaults.receiverIp || null,
      androidModel: defaults.androidModel || null,
      androidVersion: defaults.androidVersion || null,
      receiverPackage: defaults.receiverPackage || null,
      defaultBackendOrder: defaults.defaultBackendOrder || ['direct', 'hls', 'ffmpeg-live', 'vlc', 'pretranscode'],
      knownWorkingBackends: [],
      knownFailingBackends: [],
      externalSubtitlesWork: null,
      srtConvertedToVttWorks: null,
      embeddedVttWorks: null,
      burnInSubtitlesReliable: null,
      nativeSeekWorks: null,
      seekStrategy: 'native-seek',
      lastKnownGood: null,
      lastSuccessfulCastAt: null,
      lastFailedCastReason: null,
    };
  }
  return profiles[key];
}

function recordSuccess(profiles, deviceName, { backend, analysis = {} } = {}) {
  const profile = getOrCreateProfile(profiles, deviceName);
  if (backend && !profile.knownWorkingBackends.includes(backend)) {
    profile.knownWorkingBackends.push(backend);
  }
  profile.knownFailingBackends = profile.knownFailingBackends.filter((b) => b !== backend);
  profile.lastSuccessfulCastAt = new Date().toISOString();
  profile.lastKnownGood = {
    backend,
    container: analysis.container || null,
    videoCodec: analysis.videoCodec || null,
    audioCodec: analysis.audioCodec || null,
  };
  profile.lastFailedCastReason = null;
  return profile;
}

function recordFailure(profiles, deviceName, { backend, reason } = {}) {
  const profile = getOrCreateProfile(profiles, deviceName);
  if (backend && !profile.knownFailingBackends.includes(backend)) {
    profile.knownFailingBackends.push(backend);
  }
  profile.lastFailedCastReason = reason || null;
  return profile;
}

function createDeviceProfileStore(profilePath) {
  let profiles = loadDeviceProfiles(profilePath);

  return {
    get(deviceName) {
      return getOrCreateProfile(profiles, deviceName);
    },
    getAll() {
      return { ...profiles };
    },
    recordSuccess(deviceName, data) {
      const p = recordSuccess(profiles, deviceName, data);
      saveDeviceProfiles(profilePath, profiles);
      return p;
    },
    recordFailure(deviceName, data) {
      const p = recordFailure(profiles, deviceName, data);
      saveDeviceProfiles(profilePath, profiles);
      return p;
    },
    update(deviceName, patch) {
      const p = getOrCreateProfile(profiles, deviceName);
      Object.assign(p, patch);
      saveDeviceProfiles(profilePath, profiles);
      return p;
    },
    reload() {
      profiles = loadDeviceProfiles(profilePath);
    },
  };
}

module.exports = {
  createDeviceProfileStore,
  getDeviceKey,
  getOrCreateProfile,
};
