const assert = require('assert');
const { choosePipelineMode } = require('../lib/media/pipeline');
const { buildHlsFfmpegCommand } = require('../lib/media/jobs');
const { getReceiverBaseUrl, assertReceiverReachableUrl } = require('../lib/media/urls');

function testMkvChoosesHls() {
  const analysis = {
    playbackMode: 'audio-transcode',
    timestampRisk: true,
    videoCodec: 'h264',
    audioCodec: 'aac',
    container: 'matroska',
  };
  assert.equal(choosePipelineMode({ analysis, target: 'chromecast', requestedMode: 'auto' }), 'hls-audio-transcode');
  assert.equal(choosePipelineMode({ analysis, target: 'airplay', requestedMode: 'auto' }), 'hls-audio-transcode');
}

function testDirectSafeMp4() {
  const analysis = { playbackMode: 'direct', timestampRisk: false, container: 'mp4', videoCodec: 'h264', audioCodec: 'aac' };
  assert.equal(choosePipelineMode({ analysis, target: 'chromecast', requestedMode: 'auto' }), 'direct');
}

function testFamilyGuyStyleCommand() {
  const cmd = buildHlsFfmpegCommand({
    filePath: "/home/REDACTED_USER/watch_list/Family.Guy.S24E14.1080p.WEB.h264-EDITH[EZTVx.to].mkv",
    jobDir: '/tmp/cast_manager_cache/jobs/hls_test',
    startSeconds: 300,
    mode: 'audio-transcode',
    analysis: {
      playbackMode: 'audio-transcode',
      videoCodec: 'h264',
      videoStreamIndex: 0,
      audioStreamIndex: 1,
    },
  });
  assert.match(cmd, /-ss 300/);
  assert.match(cmd, /-c:v copy/);
  assert.match(cmd, /-c:a aac/);
  assert.match(cmd, /aresample=async=1:first_pts=0/);
  assert.match(cmd, /-avoid_negative_ts make_zero/);
  assert.match(cmd, /-f hls/);
}

function testReceiverUrlsAvoidLocalhost() {
  const req = { protocol: 'http', headers: { host: 'localhost:8004' } };
  const base = getReceiverBaseUrl(req, { sshHost: 'REDACTED_SERVER_IP', port: 8004 }, {});
  assert.equal(base, 'http://REDACTED_SERVER_IP:8004');
  assert.throws(() => assertReceiverReachableUrl('http://localhost:8004/x'), /localhost/);
}

testMkvChoosesHls();
testDirectSafeMp4();
testFamilyGuyStyleCommand();
testReceiverUrlsAvoidLocalhost();
console.log('media-pipeline tests passed');
