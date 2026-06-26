const FAILURE_MESSAGES = {
  NO_RECEIVER_FOUND: 'No Chromecast receiver was found on the network. Run Cast Doctor or scan for devices.',
  ADB_NOT_CONNECTED: 'ADB is not connected to the Android TV. Check USB/wireless ADB on the server.',
  SERVER_NOT_LAN_REACHABLE: 'The Cast Manager server is not reachable on the LAN. Bind to 0.0.0.0 and set CAST_PUBLIC_BASE_URL.',
  STREAM_URL_LOCALHOST: 'The stream URL uses localhost, which the TV cannot reach. Set CAST_PUBLIC_BASE_URL to your LAN IP.',
  TV_DID_NOT_REQUEST_STREAM: 'Cast command was accepted, but the TV never requested the video URL. This usually means the TV cannot reach the stream host. Run Cast Doctor.',
  STREAM_HTTP_404: 'The TV requested the stream but received HTTP 404.',
  STREAM_HTTP_403: 'The TV requested the stream but received HTTP 403.',
  STREAM_HTTP_500: 'The TV requested the stream but the server returned HTTP 500.',
  STREAM_RANGE_UNSUPPORTED: 'The stream endpoint does not support HTTP Range requests.',
  FFMPEG_EXITED_EARLY: 'FFmpeg exited before the receiver could start playback.',
  VLC_EXITED_EARLY: 'VLC relay exited before the receiver could start playback.',
  HLS_MANIFEST_NOT_REQUESTED: 'HLS started, but the TV never requested the manifest.',
  HLS_SEGMENTS_NOT_REQUESTED: 'The TV requested the HLS manifest but no segments were fetched.',
  SUBTITLE_URL_NOT_REQUESTED: 'Video may be playing, but the TV never requested the subtitle track. Try Burn In Subtitles.',
  SUBTITLE_CONVERSION_FAILED: 'Subtitle conversion to WebVTT failed.',
  RECEIVER_CODEC_REJECTED: 'The receiver rejected the media codec or container.',
  CATT_COMMAND_FAILED: 'The catt cast command failed.',
  STATUS_STUCK_IDLE: 'The receiver reports idle after cast start.',
  PLAYBACK_TIME_NOT_ADVANCING: 'Playback time is not advancing.',
  SEEK_FAILED: 'Seek failed on this backend.',
  CLEANUP_FAILED: 'Stop/cleanup did not complete cleanly.',
  PREFLIGHT_FAILED: 'Preflight checks failed before casting.',
  BACKEND_UNAVAILABLE: 'The selected casting backend is not available on this server.',
  UNKNOWN: 'Cast failed for an unknown reason.',
};

function classifyFailure(context = {}) {
  const codes = [];
  if (context.preflightFailed) codes.push('PREFLIGHT_FAILED');
  if (context.noReceiver) codes.push('NO_RECEIVER_FOUND');
  if (context.adbMissing) codes.push('ADB_NOT_CONNECTED');
  if (context.localhostUrl) codes.push('STREAM_URL_LOCALHOST');
  if (context.cattFailed) codes.push('CATT_COMMAND_FAILED');
  if (context.tvRequestedStream === false) codes.push('TV_DID_NOT_REQUEST_STREAM');
  if (context.streamHttpStatus === 404) codes.push('STREAM_HTTP_404');
  if (context.streamHttpStatus === 403) codes.push('STREAM_HTTP_403');
  if (context.streamHttpStatus >= 500) codes.push('STREAM_HTTP_500');
  if (context.rangeUnsupported) codes.push('STREAM_RANGE_UNSUPPORTED');
  if (context.ffmpegExited) codes.push('FFMPEG_EXITED_EARLY');
  if (context.vlcExited) codes.push('VLC_EXITED_EARLY');
  if (context.hlsManifestNotRequested) codes.push('HLS_MANIFEST_NOT_REQUESTED');
  if (context.hlsSegmentsNotRequested) codes.push('HLS_SEGMENTS_NOT_REQUESTED');
  if (context.subtitleNotRequested) codes.push('SUBTITLE_URL_NOT_REQUESTED');
  if (context.subtitleConversionFailed) codes.push('SUBTITLE_CONVERSION_FAILED');
  if (context.receiverCodecRejected) codes.push('RECEIVER_CODEC_REJECTED');
  if (context.statusStuckIdle) codes.push('STATUS_STUCK_IDLE');
  if (context.playbackNotAdvancing) codes.push('PLAYBACK_TIME_NOT_ADVANCING');
  if (context.seekFailed) codes.push('SEEK_FAILED');
  if (context.cleanupFailed) codes.push('CLEANUP_FAILED');
  if (context.backendUnavailable) codes.push('BACKEND_UNAVAILABLE');

  const primary = codes[0] || context.primaryCode || 'UNKNOWN';
  const secondary = codes.slice(1);
  return {
    primaryFailureCode: primary,
    secondaryFailureCodes: secondary,
    userMessage: FAILURE_MESSAGES[primary] || context.error || FAILURE_MESSAGES.UNKNOWN,
    failureMessages: codes.map((c) => ({ code: c, message: FAILURE_MESSAGES[c] })),
  };
}

module.exports = {
  FAILURE_MESSAGES,
  classifyFailure,
};
