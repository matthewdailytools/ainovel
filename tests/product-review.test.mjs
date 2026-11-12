import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
import { buildData, featureRefs, replaceData, reviewPath, sourcePath } from '../scripts/build-product-review.mjs';

const html = await readFile(reviewPath, 'utf8');
const source = await readFile(sourcePath, 'utf8');
const data = buildData(source);
const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
const runtime = scripts.find(([, attrs]) => !attrs.includes('application/json'))[2];

// A small browser surface exercises the shipped runtime without installing a DOM framework.
function openPage(hash = '', stored = null, failStorage = false) {
  const nodes = new Map(), events = {}, downloads = [], blobs = [];
  let focused = '';
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      textContent: id === 'product-data' ? JSON.stringify(data) : '', innerHTML: '', value: '',
      hidden: true, scrollLeft: 0, dataset: {}, handlers: {},
      focus() { focused = id; },
      addEventListener(type, fn) { this.handlers[type] = fn; },
      querySelector(selector) { return node(selector); },
      setAttribute() {}, removeAttribute() {},
    });
    return nodes.get(id);
  };
  const sandbox = {
    console, Blob, Map, Set, URL: { createObjectURL(blob) { blobs.push(blob); return 'blob:review'; }, revokeObjectURL() {} },
    setTimeout(fn) { fn(); }, location: { hash },
    navigator: { locks: { request(key, callback) { return Promise.resolve().then(callback); } } },
    localStorage: {
      getItem() { if (failStorage) throw new Error('storage disabled'); return stored; },
      setItem(key, value) { if (failStorage) throw new Error('storage disabled'); stored = value; },
    },
    document: {
      getElementById: node, querySelector: node, querySelectorAll() { return []; },
      body: { append() {} },
      createElement() { return { href: '', download: '', remove() {}, click() { downloads.push(this.download); } }; },
    },
    window: { addEventListener(type, fn) { events[type] = fn; } },
  };
  vm.runInNewContext(runtime, sandbox, { filename: 'novel-product-review.html' });
  return {
    node, sandbox, downloads, blobs,
    get focused() { return focused; },
    get markup() { return node('main').innerHTML; },
    get stored() { return stored; },
    set stored(value) { stored = value; },
    navigate(nextHash) { sandbox.location.hash = nextHash; events.hashchange(); },
    fire(type, target) { return node('main').handlers[type]({ target }); },
  };
}

test('design text is embedded without drift, with valid references and all three modes', () => {
  assert.equal(replaceData(html, data), html);
  assert.equal(buildData(source.replace(/\r?\n/g, '\r\n')).sha256, data.sha256);
  assert.deepEqual(data.modes.map(x => x.name), ['建议', '半自动', '全自动']);
  assert.deepEqual(data.pages.map(x => x.name), ['作品', '资料书库', '任务与用量', '设置', '写作', '创作方向', '故事设计', '审稿与故事记录', '定稿']);
  assert.equal(data.scenarios[3].steps.at(-1).label, '对照采纳');
  assert.equal(data.acceptance.length, 16);
  assert.deepEqual(featureRefs('F02—F04、F09、F10'), ['F02', 'F03', 'F04', 'F09', 'F10']);
});

test('changed step counts stop generation rather than mislabeling a workflow', () => {
  assert.throws(() => buildData(source.replace('识别固定事件与结果 →', '识别固定事件与结果 → 新增作者步骤 →')), /U04 操作步骤已改变/);
  assert.throws(() => featureRefs('F16—F17'), /无效功能编号/);
});

test('embedded content cannot close the JSON script element', () => {
  const updated = replaceData(html, { message: '</script><script>bad()</script>' });
  assert.ok(updated.includes('\\u003c/script>'));
  assert.equal([...updated.matchAll(/<script\b/g)].length, 2);
});

test('every entry, scenario and feature renders a real destination', () => {
  const page = openPage();
  assert.match(page.markup, /正文始终在中心/);
  for (const [view, items] of [['pages', data.pages], ['flows', data.scenarios], ['features', data.features]]) {
    for (const item of items) {
      page.navigate(`#${view}/${item.id}/0`);
      assert.ok(page.markup.includes(item.name), `${view}/${item.id} title missing`);
      assert.ok(!page.markup.includes('undefined'), `${view}/${item.id} undefined output`);
      for (const [, destination] of page.markup.matchAll(/href="#(?:pages|flows|features)\/([A-Z]\d{2})/g)) {
        assert.ok([...data.pages, ...data.scenarios, ...data.features].some(x => x.id === destination), destination);
      }
    }
  }
  page.navigate('#delivery');
  assert.match(page.markup, /待实现验证/);
  assert.ok(page.markup.includes(data.acceptance.at(-1).expected));
});

test('workflow selection exposes the corresponding functions and clamps invalid steps', () => {
  const page = openPage('#flows/U04/0');
  assert.match(page.markup, /步骤 1 \/ 5/);
  page.navigate('#flows/U04/4');
  assert.match(page.markup, /步骤 5 \/ 5/);
  assert.match(page.markup, /data-step="5" disabled/);
  assert.match(page.markup, /F14.*版本与恢复/s);
  page.navigate('#flows/U04/999');
  assert.match(page.markup, /步骤 5 \/ 5/);
  page.navigate('#%E0%A4%A');
  assert.match(page.markup, /正文始终在中心/);
});

test('search shows an empty state and waits for Chinese composition to finish', () => {
  const page = openPage('#flows/U04/0');
  page.fire('compositionstart', { id: 'scene-query' });
  page.fire('input', { id: 'scene-query', value: '没有这样的场景' });
  assert.equal(page.node('scene-list').innerHTML, '');
  page.fire('compositionend', { id: 'scene-query', value: '没有这样的场景' });
  assert.match(page.node('scene-list').innerHTML, /没有匹配场景/);
  page.fire('input', { id: 'scene-query', value: '渡船' });
  assert.match(page.node('scene-list').innerHTML, /U08/);
  assert.ok(!page.node('scene-list').innerHTML.includes('U04'));
  assert.match(page.markup, /给定情节，直接补出正文/);
});

test('feature relationships include directed revision and version protection without requiring a full pipeline', () => {
  const page = openPage('#features/F08');
  assert.match(page.markup, /定向返修/);
  assert.match(page.markup, /版本保护/);
  assert.match(page.markup, /局部辅助可以直接开始/);
  assert.equal([...page.markup.matchAll(/class="graph-node /g)].length, 16);
  page.node('main').querySelector('.graph-scroll').scrollLeft = 360;
  page.navigate('#features/F14');
  assert.equal(page.node('main').querySelector('.graph-scroll').scrollLeft, 360);
});

test('review text is escaped, stays scoped to its item and can be exported', async () => {
  const payload = '</textarea><img src=x onerror="bad()">';
  const stored = JSON.stringify({ sha256: data.sha256, entries: { P05: { status: '需调整', note: payload } } });
  const page = openPage('', stored);
  assert.ok(page.markup.includes('&lt;/textarea&gt;'));
  assert.ok(!page.markup.includes('<img'));
  page.navigate('#flows/U04/0');
  assert.ok(!page.markup.includes(payload));
  page.node('export-notes').handlers.click();
  assert.deepEqual(page.downloads, ['ainovel-产品设计审阅意见.md']);
  const exported = await page.blobs[0].text();
  assert.match(exported, /P05 写作/);
  assert.ok(exported.includes(payload));
  assert.ok(exported.includes(data.sha256));
});

test('storage failure preserves current review text and reports that saving did not succeed', async () => {
  const page = openPage('', null, true);
  assert.equal(page.node('storage-notice').hidden, false);
  page.node('review-status').value = '待讨论';
  page.node('review-note').value = '需要核对写作入口';
  await page.fire('input', { id: 'review-note' });
  assert.match(page.node('note-feedback').textContent, /未能持久保存/);
  page.navigate('#pages/P01');
  page.navigate('#pages/P05');
  assert.match(page.markup, /需要核对写作入口/);
});

test('a stale window cannot overwrite a newer persisted review', async () => {
  const page = openPage();
  const newer = JSON.stringify({ version: 1, sha256: data.sha256, entries: { P05: { status: '认可', note: '另一窗口的意见' } } });
  page.stored = newer;
  page.node('review-status').value = '待讨论';
  page.node('review-note').value = '本窗口的意见';
  await page.fire('input', { id: 'review-note' });
  assert.equal(page.stored, newer);
  assert.match(page.node('storage-notice').textContent, /另一窗口已更新/);
  page.node('export-notes').handlers.click();
  assert.ok((await page.blobs[0].text()).includes('本窗口的意见'));
});

test('unsupported note formats remain untouched and lack of Web Locks uses memory only', async () => {
  const future = JSON.stringify({ version: 99, entries: {} });
  const page = openPage('', future);
  page.node('review-status').value = '认可';
  page.node('review-note').value = '新意见';
  await page.fire('input', { id: 'review-note' });
  assert.equal(page.stored, future);
  assert.match(page.node('storage-notice').textContent, /其他数据格式/);
  const noLocks = openPage();
  noLocks.sandbox.navigator.locks = null;
  noLocks.node('review-status').value = '认可';
  noLocks.node('review-note').value = '仅内存意见';
  await noLocks.fire('input', { id: 'review-note' });
  assert.equal(noLocks.stored, null);
  assert.match(noLocks.node('note-feedback').textContent, /未能持久保存/);
});

test('empty export and skip-to-content have useful behavior', () => {
  const page = openPage('#features/F08');
  page.node('export-notes').handlers.click();
  assert.equal(page.downloads.length, 0);
  assert.equal(page.node('export-feedback').hidden, false);
  assert.match(page.node('export-feedback').textContent, /尚无审阅意见/);
  let prevented = false;
  page.node('.skip').handlers.click({ preventDefault() { prevented = true; } });
  assert.ok(prevented);
  assert.equal(page.focused, 'main');
  assert.equal(page.sandbox.location.hash, '#features/F08');
});

test('text and control palette meets the applicable contrast thresholds', () => {
  const cssColor = name => {
    const result = html.match(new RegExp(`--${name}: (#[a-f0-9]{6})`, 'i'));
    assert.ok(result, `missing CSS token ${name}`);
    return result[1];
  };
  const luminance = color => {
    const rgb = color.match(/[a-f0-9]{2}/gi).map(hex => parseInt(hex, 16) / 255)
      .map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4);
    return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
  };
  const ratio = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
  for (const [foreground, background] of [
    [cssColor('ink'), cssColor('bg')], [cssColor('muted'), '#ffffff'], [cssColor('accent'), cssColor('soft')],
    ['#d0dfd7', cssColor('deep')], [cssColor('warm-ink'), cssColor('warm')], ['#ffffff', cssColor('accent')],
  ]) assert.ok(ratio(foreground, background) >= 4.5, `${foreground} / ${background}`);
  assert.ok(ratio(cssColor('control'), '#ffffff') >= 3);
  assert.ok(ratio(cssColor('focus'), cssColor('soft')) >= 3);
});
