import { createHash } from 'node:crypto';
import { readFile, rename, rm, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = new URL('../', import.meta.url);
export const sourcePath = fileURLToPath(new URL('13-产品使用场景与功能设计.md', root));
export const reviewPath = fileURLToPath(new URL('novel-product-review.html', root));

function section(text, start, end) {
  const from = text.indexOf(start);
  const to = end ? text.indexOf(end, from + start.length) : text.length;
  if (from < 0 || to < 0) throw new Error(`设计稿缺少章节：${start}`);
  return text.slice(from + start.length, to);
}

function rows(text) {
  return text.split('\n').filter(line => line.startsWith('|'))
    .map(line => line.split('|').slice(1, -1).map(cell => cell.trim()))
    .filter(row => !row.every(cell => /^:?-+:?$/.test(cell))).slice(1);
}

export function featureRefs(text) {
  const found = new Set();
  for (const match of text.matchAll(/F(\d{2})(?:[—–-]F(\d{2}))?/g)) {
    const first = Number(match[1]);
    const last = Number(match[2] || match[1]);
    if (first > last || last > 16 || first < 1) throw new Error(`无效功能编号：${match[0]}`);
    for (let n = first; n <= last; n++) found.add(`F${String(n).padStart(2, '0')}`);
  }
  return [...found];
}

// Only the presentation mappings live here. Scenario wording and tables come from the design draft.
const scenarioMeta = {
  U01: { pages: ['P01', 'P05'], modes: ['建议'], steps: ['F08', 'F08', 'F08', 'F08', 'F14', 'F08'] },
  U02: { pages: ['P01', 'P06', 'P07'], modes: ['建议', '全自动'], steps: ['F02', 'F02', 'F03 F06', 'F02', 'F04 F05'] },
  U03: { pages: ['P06', 'P07'], modes: ['建议', '半自动', '全自动'], steps: ['F03', 'F03', 'F03 F04', 'F02', 'F09 F08'] },
  U04: { pages: ['P05'], modes: ['半自动'], steps: ['F02', 'F05 F04', 'F07 F08', 'F10', 'F14 F11'] },
  U05: { pages: ['P04', 'P05'], modes: ['建议', '半自动', '全自动'], steps: ['F09', 'F05 F02', 'F08', 'F10', 'F09'] },
  U06: { pages: ['P05', 'P07', 'P08'], modes: ['建议', '半自动', '全自动'], steps: ['F06', 'F05 F11', 'F05', 'F11', 'F08'] },
  U07: { pages: ['P07', 'P08'], modes: ['建议', '半自动', '全自动'], steps: ['F06', 'F06', 'F07', 'F06 F11', 'F11 F10'] },
  U08: { pages: ['P02', 'P05'], modes: ['独立使用', '建议', '半自动', '全自动'], steps: ['F12', 'F12', 'F12', 'F12', 'F12', 'F12', 'F12 F08'] },
  U09: { pages: ['P01', 'P08'], modes: ['建议', '半自动', '全自动'], steps: ['F01', 'F01', 'F14', 'F05 F11', 'F11', 'F08 F10'] },
  U10: { pages: ['P05', 'P08'], modes: ['建议', '半自动', '全自动'], steps: ['F14', 'F11', 'F11', 'F10', 'F11 F10 F14'] },
  U11: { pages: ['P03', 'P05'], modes: ['全自动'], steps: ['F14', 'F04 F05', 'F06 F07', 'F08', 'F10', 'F14 F11', 'F10', 'F13 F15'] },
  U12: { pages: ['P08', 'P09'], modes: ['建议', '半自动', '全自动'], steps: ['F10', 'F10', 'F10 F08', 'F14', 'F10', 'F15', 'F15'] },
  U13: { pages: ['P03', 'P04'], modes: ['独立使用', '建议', '半自动', '全自动'], steps: ['F16', 'F16', 'F16', 'F16', 'F16 F13', 'F16 F13'] },
};

const pageMeta = [
  { id: 'P01', features: ['F01', 'F14'], actions: [['直接开始写', 'U01'], ['从想法开始', 'U02'], ['导入已有稿件', 'U09']], layout: '作品选择', hint: '近期作品与继续写作优先；构思和导入均为可选入口。' },
  { id: 'P02', features: ['F12'], actions: [['查看找书与取材流程', 'U08']], layout: '资料阅读', hint: '独立于作品与模型。找电子书和搜已入库内容分别表达。' },
  { id: 'P03', features: ['F13', 'F14', 'F16'], actions: [['查看整书自动创作', 'U11'], ['查看模型与用量', 'U13']], layout: '任务监控', hint: '呈现可读成果、当前步骤、停止原因与恢复动作。' },
  { id: 'P04', features: ['F09', 'F14', 'F16'], actions: [['查看模型连接流程', 'U13'], ['查看写法试验', 'U05']], layout: '设置', hint: '低频设置集中管理；已有预设可沿用，密钥不进入正文。' },
  { id: 'P05', features: ['F01', 'F02', 'F07', 'F08', 'F09', 'F10', 'F12', 'F14'], actions: [['按情节补写', 'U04'], ['选区辅助', 'U01'], ['查资料', 'U08'], ['启动自动创作', 'U11']], layout: '正文编辑', hint: '正文空间优先，目录和辅助面板按需收起；没有大纲也能直接写。' },
  { id: 'P06', features: ['F02', 'F03'], actions: [['从三类意图开始', 'U02'], ['查看男女频与题材装配', 'U03']], layout: '方向编排', hint: '情绪、钩子和方向可用一段话表达；作者锁定项优先于频道预设。' },
  { id: 'P07', features: ['F04', 'F05', 'F06', 'F07'], actions: [['查看人物时点状态', 'U06'], ['查看多线与伏笔', 'U07'], ['查看题材装配', 'U03']], layout: '故事设计', hint: '只展开当前阶段需要的设定，不把覆盖清单变为必填百科。' },
  { id: 'P08', features: ['F05', 'F06', 'F10', 'F11', 'F14'], actions: [['查看审稿与修订', 'U12'], ['查看前文改动影响', 'U10'], ['回顾人物所知', 'U06']], layout: '审稿记录', hint: '问题要有位置和证据；人物认知、读者所知和作者真相分开。' },
  { id: 'P09', features: ['F10', 'F14', 'F15'], actions: [['查看定稿与导出流程', 'U12']], layout: '整书交付', hint: '分别核对叙事闭合、稿件齐全和导出成功。' },
];

const edges = [
  ['F02', 'F03', 'reference', '约束题材'], ['F03', 'F04', 'sequence', '装配规则'],
  ['F03', 'F05', 'reference', '适配人物'], ['F02', 'F06', 'reference', '锁定终局'],
  ['F04', 'F07', 'reference', '规则边界'], ['F05', 'F07', 'reference', '时点处境'],
  ['F06', 'F07', 'sequence', '拆章排场'], ['F07', 'F08', 'sequence', '提供情节'],
  ['F09', 'F08', 'reference', '表达预设'], ['F12', 'F08', 'reference', '选定资料'],
  ['F08', 'F10', 'sequence', '正文候选'], ['F10', 'F08', 'feedback', '定向返修'],
  ['F08', 'F14', 'sequence', '提交候选'], ['F14', 'F11', 'sequence', '采纳后更新'],
  ['F11', 'F07', 'reference', '有效状态'], ['F11', 'F05', 'reference', '查询时点'],
  ['F11', 'F06', 'reference', '追踪承诺'], ['F10', 'F15', 'sequence', '复检交付'],
  ['F06', 'F15', 'reference', '核对终局'], ['F01', 'F08', 'support', '作品归属'],
  ['F13', 'F07', 'support', '滚动规划'], ['F13', 'F15', 'support', '推进完结'],
  ['F14', 'F08', 'support', '版本保护'], ['F16', 'F13', 'support', '预算与恢复'],
  ['F16', 'F08', 'support', '模型调用'],
].map(([from, to, type, label]) => ({ from, to, type, label }));

export function buildData(source) {
  const text = source.replace(/\r\n/g, '\n');
  const features = rows(section(text, '### 5.1 功能分组', '### 5.2')).map(([label, action, output, priority]) => {
    const [, id, name] = label.match(/^(F\d{2}) (.+)$/) || [];
    return { id, name, action, output, priority, tier: priority.slice(0, 2) };
  });
  const scenarios = [...section(text, '## 4. 使用场景设计', '## 5. 功能设计').matchAll(/^### (U\d{2}) ([^\n]+)\n([\s\S]*?)(?=^### |$(?![\s\S]))/gm)].map(([, id, name, body]) => {
    const fields = Object.fromEntries([...body.matchAll(/^- \*\*([^*]+)\*\*：([^\n]+)/gm)].map(([, label, value]) => [label, value]));
    const meta = scenarioMeta[id];
    if (!meta) throw new Error(`缺少场景展示映射：${id}`);
    const steps = fields['操作路径'].replace(/[。.]$/, '').split(/\s*→\s*/);
    if (steps.length !== meta.steps.length) throw new Error(`${id} 操作步骤已改变，请更新步骤映射`);
    const refs = featureRefs(fields['成功判断'].split('对应 ')[1] || '');
    return { id, name, fields, features: refs, pages: meta.pages, modes: meta.modes,
      steps: steps.map((label, index) => ({ label, features: featureRefs(meta.steps[index]) })) };
  });
  const pages = rows(section(text, '### 6.1 工作区层与作品层', '### 6.2')).map(([label, question, content], index) => {
    const [scope, name] = label.split('：');
    return { ...pageMeta[index], scope, name, question, content };
  });
  const data = {
    schema: 'ainovel-product-review/1', date: '2026-11-12',
    source: '13-产品使用场景与功能设计.md', sha256: createHash('sha256').update(text).digest('hex'),
    scenarios, features, pages, edges,
    modules: rows(section(text, '### 5.2 与两套已有模块编号的对应', '### 5.3')).map(([feature, author, detail]) => ({ features: featureRefs(feature), author, detail })),
    modes: rows('| 分工 | 作者掌握的创作决定 |' + section(text, '| 分工 | 作者掌握的创作决定 |', '## 4.')).map(([name, human, ai, missing]) => ({ name, human, ai, missing })),
    phases: rows(section(text, '### 10.1 建议顺序', '### 10.2')).map(([name, deliverable, scope, gate]) => ({ name, deliverable, scope, gate })),
    acceptance: rows(section(text, '### 11.1 功能验收清单', '### 11.2')).map(([id, action, expected]) => ({ id, action, expected })),
    decisions: rows(section(text, '## 12. 待确定事项与推荐选择', '## 13.')).map(([name, recommendation, when]) => ({ name, recommendation, when })),
    states: rows(section(text, '## 8. 关键状态与失败恢复', '## 9.')).map(([name, meaning, recovery]) => ({ name, meaning, recovery })),
  };
  validateData(data);
  return data;
}

export function validateData(data) {
  for (const [name, count] of [['scenarios', 13], ['features', 16], ['pages', 9], ['acceptance', 16], ['phases', 7], ['modes', 3]]) {
    if (data[name].length !== count) throw new Error(`${name} 数量不符：${data[name].length}`);
  }
  const fs = new Set(data.features.map(f => f.id));
  const ps = new Set(data.pages.map(p => p.id));
  const us = new Set(data.scenarios.map(u => u.id));
  if (fs.size !== 16 || ps.size !== 9 || us.size !== 13) throw new Error('编号重复或缺失');
  const requireFeature = id => { if (!fs.has(id)) throw new Error(`未知功能 ${id}`); };
  for (const u of data.scenarios) {
    if (Object.keys(u.fields).length !== 7) throw new Error(`${u.id} 场景字段缺失`);
    u.features.forEach(requireFeature);
    u.steps.flatMap(s => s.features).forEach(requireFeature);
    if (!u.pages.every(id => ps.has(id))) throw new Error(`${u.id} 页面引用无效`);
  }
  for (const p of data.pages) {
    p.features.forEach(requireFeature);
    if (!p.actions.every(([, id]) => us.has(id))) throw new Error(`${p.id} 场景动作无效`);
  }
  for (const edge of data.edges) [edge.from, edge.to].forEach(requireFeature);
  for (const f of data.features) {
    if (!data.scenarios.some(u => u.features.includes(f.id))) throw new Error(`${f.id} 没有场景覆盖`);
    if (!data.modules.some(m => m.features.includes(f.id))) throw new Error(`${f.id} 缺少已有模块映射`);
  }
}

export function replaceData(html, data) {
  const pattern = /(<script type="application\/json" id="product-data">)[\s\S]*?(<\/script>)/;
  if (!pattern.test(html)) throw new Error('HTML 缺少产品数据区域');
  const json = JSON.stringify(data, null, 2).replace(/</g, '\\u003c');
  return html.replace(/\r\n/g, '\n').replace(pattern, (_, start, end) => `${start}\n${json}\n${end}`);
}

async function main() {
  const source = await readFile(sourcePath, 'utf8');
  const html = (await readFile(reviewPath, 'utf8')).replace(/\r\n/g, '\n');
  const output = replaceData(html, buildData(source));
  if (process.argv.includes('--check')) {
    if (output !== html) throw new Error('审阅页与设计稿不同步，请运行 node scripts/build-product-review.mjs');
    console.log('设计稿与审阅页同步检查通过。');
  } else {
    const temporary = `${reviewPath}.${process.pid}.tmp`;
    let created = false;
    try {
      await writeFile(temporary, output, { encoding: 'utf8', flag: 'wx' });
      created = true;
      await rename(temporary, reviewPath);
    } finally {
      if (created) await rm(temporary, { force: true });
    }
    console.log('已同步 9 个页面入口、13 个场景、16 组功能与 16 个验收例子。');
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
