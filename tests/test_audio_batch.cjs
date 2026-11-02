// Exercise the actual browser batch function with a failed synthesis and retry.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('app/static/app.js', 'utf8');
const start = source.indexOf('async function generateAllChapterAudio(content, button) {');
const end = source.indexOf('\nfunction renderStudioHero(content)', start);
assert(start >= 0 && end > start);

const content = { id: 'book', status: 'confirmed', chapters: [
  { heading: '已完成', body: '一' }, { heading: '已有脚本', body: '二' }, { heading: '失败后重试', body: '三' },
] };
const records = {
  '已完成': { id: 'ready', status: 'ready', audio_available: true },
  '已有脚本': { id: 'existing', status: 'script_ready', audio_available: false },
};
const calls = [];
const messages = [];
let failLast = true;
const state = { selectedProject: { project: { id: 'project' } } };
const context = {
  appState: state,
  chapterAudio: (_, chapter) => records[chapter.heading],
  hasUnsavedChapterEdits: () => false,
  window: { confirm: () => true },
  $: () => null,
  setBusy: () => {},
  renderProjectDetail: () => {},
  showMessage: (message, error) => messages.push({ message, error }),
  request: async (url, options = {}) => {
    calls.push(url);
    if (url.endsWith('/audio-scripts')) {
      const heading = JSON.parse(options.body).section_heading;
      return (records[heading] = { id: 'created', status: 'script_ready', audio_available: false });
    }
    if (url.endsWith('/existing/synthesize')) {
      records['已有脚本'] = { id: 'existing', status: 'ready', audio_available: true };
      return {};
    }
    if (url.endsWith('/created/synthesize')) {
      if (failLast) throw new Error('模拟语音服务失败');
      records['失败后重试'] = { id: 'created', status: 'ready', audio_available: true };
      return {};
    }
    if (url === '/api/projects/project') return state.selectedProject;
    throw new Error('Unexpected request: ' + url);
  },
};

(async () => {
  const generate = vm.runInNewContext('(' + source.slice(start, end) + ')', context);
  await generate(content, null);
  assert.equal(calls.filter(url => url.endsWith('/audio-scripts')).length, 1);
  assert(!calls.some(url => url.includes('/ready/synthesize')));
  assert(messages.at(-1).error);
  assert.match(messages.at(-1).message, /已完成 1 \/ 2 章/);
  failLast = false;
  await generate(content, null);
  assert.equal(calls.filter(url => url.endsWith('/audio-scripts')).length, 1, 'retry must reuse the existing script');
  assert.equal(calls.filter(url => url.endsWith('/existing/synthesize')).length, 1, 'retry must skip the completed chapter');
  assert.match(messages.at(-1).message, /本次 1 章音频已生成/);
  console.log('audio batch failure and retry: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
