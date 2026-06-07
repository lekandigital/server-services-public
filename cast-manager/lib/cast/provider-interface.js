/**
 * Provider contract used by Cast Manager.
 *
 * Implementations expose:
 * - discover(): Promise<CastDevice[]>
 * - selectDevice(deviceId): Promise<void>
 * - play(input): Promise<CastStartResult>
 * - pause/resume/stop/seek/volume/status(deviceId, ...): Promise<CastControlResult|CastStatus>
 */

const PROVIDERS = Object.freeze({
  CHROMECAST: 'chromecast',
  AIRPLAY: 'airplay',
});

function makeProviderDeviceId(provider, id) {
  return `${provider}:${String(id || '').trim()}`;
}

function stripProviderPrefix(provider, deviceId) {
  const value = String(deviceId || '').trim();
  const prefix = `${provider}:`;
  return value.startsWith(prefix) ? value.slice(prefix.length) : value;
}

module.exports = {
  PROVIDERS,
  makeProviderDeviceId,
  stripProviderPrefix,
};
