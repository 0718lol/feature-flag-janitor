const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const state = {
  files: [],
  analysis: null,
  sourceMeta: { kind: 'manual' },
};

const icons = {
  spark: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M11 2 8.8 8.2 3 11l5.8 2.8L11 20l2.2-6.2L19 11l-5.8-2.8z"/></svg>',
  scan: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M4 7V4h3M17 4h3v3M4 17v3h3M20 17v3h-3M7 12h10"/></svg>',
  upload: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 16V4m0 0-4 4m4-4 4 4M5 20h14"/></svg>',
  download: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" d="M12 3v12m0 0 4-4m-4 4-4-4M4 20h16"/></svg>',
};

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 2400);
}

function wireIcons() {
  $$('[data-icon]').forEach((node) => {
    node.innerHTML = icons[node.dataset.icon] || '';
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function renderFiles() {
  const list = $('#fileList');
  $('#fileCount')?.remove?.();
  list.innerHTML = state.files.length
    ? state.files.map((file) => `
      <div class="file-item">
        <div>
          <strong>${escapeHtml(file.path)}</strong>
          <small>${file.lines} 行</small>
        </div>
        <code>${escapeHtml(file.kind)}</code>
      </div>
    `).join('')
    : '<div class="file-item"><div><strong>未载入代码文件</strong><small>拖入仓库文件或直接载入样例</small></div><code>0</code></div>';
}

function metricCard(title, value, note, tone = '') {
  return `<div class="metric ${tone}"><span>${escapeHtml(title)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></div>`;
}

function renderSummary(data) {
  const s = data.summary;
  $('#statFlags').textContent = s.total_flags;
  $('#statDead').textContent = s.dead_branches;
  $('#statRisk').textContent = s.risk_score;
  $('#generatedAt').textContent = `更新于 ${new Date(data.generated_at).toLocaleString('zh-CN', { hour12: false })}`;
  const check = data.input_check || { warnings: [] };
  $('#inputCheck').innerHTML = `
    <span class="status-dot"></span><strong>输入已检验</strong>
    <span>${check.flags || 0} flags · ${check.code_files || 0} 文件 · ${check.experiments || 0} 实验 · ${check.releases || 0} 发布记录</span>
    ${(check.warnings || []).map((warning) => `<small>${escapeHtml(warning)}</small>`).join('')}
  `;
  $('#summaryGrid').innerHTML = [
    metricCard('过期 flag', s.expired_flags, '应尽快处理', s.expired_flags ? 'amber' : ''),
    metricCard('死分支', s.dead_branches, '代码可收尾', s.dead_branches ? 'rose' : ''),
    metricCard('实验残留', s.experimental_residue, '实验代码仍在', s.experimental_residue ? 'blue' : ''),
    metricCard('孤儿 flag', s.orphan_flags, '仓库未再引用', s.orphan_flags ? 'amber' : ''),
    metricCard('高优先级项', s.clean_candidates, 'P0 / P1 候选', s.clean_candidates ? 'rose' : ''),
    metricCard('风险分', s.risk_score, '越低越安全', s.risk_score < 70 ? 'rose' : 'green'),
  ].join('');
}

function renderFlagTable(flags) {
  $('#flagCount').textContent = flags.length;
  const rows = flags.map((row) => `
    <tr class="clickable-row" data-flag-key="${escapeHtml(row.key)}" tabindex="0">
      <td>
        <strong>${escapeHtml(row.key)}</strong>
        <small>${escapeHtml(row.description || '无描述')}</small>
      </td>
      <td>${escapeHtml(row.owner)}</td>
      <td><span class="tag ${row.expired ? 'rose' : row.archived ? 'amber' : 'green'}">${row.expired ? '已过期' : row.archived ? '已归档' : '观察中'}</span></td>
      <td>${row.rollout}%</td>
      <td>${escapeHtml(row.expires_at || '-')}</td>
      <td>${row.reference_count}</td>
      <td><span class="tag ${row.confidence === 'high' ? 'green' : row.confidence === 'medium' ? 'amber' : 'blue'}">${row.confidence === 'high' ? '高' : row.confidence === 'medium' ? '中' : '低'}</span><small>${escapeHtml(row.evidence)}</small></td>
    </tr>
  `).join('');
  $('#flagTable').innerHTML = `
    <thead>
      <tr><th>Flag</th><th>Owner</th><th>Status</th><th>Rollout</th><th>Expires</th><th>Refs</th><th>Confidence</th></tr>
    </thead>
    <tbody>${rows || '<tr><td colspan="7">暂无结果</td></tr>'}</tbody>
  `;
}

function openFlagDetail(flagKey) {
  const row = state.analysis?.flags.find((flag) => flag.key === flagKey);
  if (!row) return;
  $('#detailTitle').textContent = row.key;
  $('#detailContent').innerHTML = `
    <div class="detail-facts">
      ${metricCard('负责人', row.owner, row.kind)}
      ${metricCard('优先级', row.cleanup_priority, row.cleanup_action, row.cleanup_priority === 'P0' ? 'rose' : 'amber')}
      ${metricCard('风险', row.risk === 'high' ? '高' : row.risk === 'medium' ? '中' : '低', `置信度 ${row.confidence}`)}
      ${metricCard('Rollout', `${row.rollout}%`, row.expires_at || '无过期日期')}
    </div>
    <section class="detail-section"><h3>判断依据</h3><div class="meta-row">${row.reasons.map((reason) => `<span class="tag amber">${escapeHtml(reason)}</span>`).join('')}</div></section>
    <section class="detail-section"><h3>代码证据</h3>${row.references.length ? row.references.map((ref) => `
      <div class="evidence-item"><strong>${escapeHtml(ref.file)}:${ref.line}</strong><span class="tag blue">${escapeHtml(ref.reference_type)}</span><code>${escapeHtml(ref.snippet)}</code></div>
    `).join('') : '<p>代码仓库中未检出引用。删除配置前需要确认外部消费者。</p>'}</section>
    <section class="detail-section"><h3>受影响测试</h3><p>${escapeHtml((row.test_candidates || []).join(', ') || '未找到直接关联测试，建议补充回归测试后再清理。')}</p></section>
    <button class="primary-btn" id="downloadPatchBtn" data-finding-key="${escapeHtml(row.key)}">下载清理草案</button>
  `;
  $('#detailBackdrop').hidden = false;
  $('#detailDrawer').hidden = false;
  $('#downloadPatchBtn').addEventListener('click', () => downloadPatch(row.key));
}

function closeFlagDetail() {
  $('#detailBackdrop').hidden = true;
  $('#detailDrawer').hidden = true;
}

async function downloadPatch(findingKey) {
  if (!state.analysis?.scan_id) return toast('当前结果缺少扫描标识');
  try {
    const result = await api('/api/patch', { method: 'POST', body: JSON.stringify({ scan_id: state.analysis.scan_id, finding_keys: [findingKey] }) });
    downloadBlob(result.patch, 'text/plain;charset=utf-8', `cleanup-draft-${findingKey}.patch`);
    toast('清理草案已下载，请人工审查');
  } catch (error) { toast(error.message); }
}

function renderBranchTable(items) {
  $('#branchCount').textContent = items.length;
  const rows = items.map((row) => `
    <tr>
      <td>
        <strong>${escapeHtml(row.flag)}</strong>
        <small>${escapeHtml(row.reason)}</small>
      </td>
      <td>${escapeHtml(row.file)}:${row.line}</td>
      <td><span class="tag ${row.branch === 'else' ? 'amber' : 'rose'}">${row.branch} 分支</span></td>
      <td>${escapeHtml(row.removal_hint)}</td>
      <td>${escapeHtml((row.tests || []).join(', ') || '建议补充回归测试')}</td>
    </tr>
  `).join('');
  $('#branchTable').innerHTML = `
    <thead>
      <tr><th>Flag</th><th>Location</th><th>Branch</th><th>Hint</th><th>Tests</th></tr>
    </thead>
    <tbody>${rows || '<tr><td colspan="5">暂无死分支</td></tr>'}</tbody>
  `;
}

function renderCleanupList(items) {
  const list = $('#cleanupList');
  list.innerHTML = items.map((item) => {
    const savedAction = state.analysis?.actions?.[item.finding_key]?.action;
    const actionState = savedAction === 'ignore' ? '已忽略' : savedAction === 'defer' ? '已延期' : savedAction === 'resolve' ? '已完成' : '';
    return `
    <div class="cleanup-item ${(state.analysis?.actions?.[item.finding_key]?.action || '') === 'resolve' ? 'is-dismissed' : ''}" data-task="${escapeHtml(item.task)}" data-finding-key="${escapeHtml(item.finding_key || '')}">
      <strong>${escapeHtml(item.priority)} · ${escapeHtml(item.task)}</strong>
      <p>${escapeHtml(item.detail)}</p>
      <div class="meta-row">
        ${item.files.map((file) => `<span class="tag blue">${escapeHtml(file)}</span>`).join('')}
        ${(item.tests || []).map((test) => `<span class="tag amber">${escapeHtml(test)}</span>`).join('')}
      </div>
      <div class="item-actions">${actionState ? `<span class="task-state">${actionState}</span>` : ''}<button class="text-btn" data-task-action="defer">稍后处理</button><button class="text-btn" data-task-action="ignore">忽略</button><button class="text-btn" data-task-action="resolve">标记完成</button></div>
    </div>
  `;
  }).join('');
}

function renderResidue(items) {
  $('#residueCount').textContent = items.length;
  const list = $('#residueList');
  list.innerHTML = items.length ? items.map((item) => `
    <div class="stack-item">
      <strong>${escapeHtml(item.flag)}</strong>
      <p>${escapeHtml(item.reason)}</p>
      <small>${escapeHtml(item.file)}:${item.line} · ${escapeHtml(item.hint)}</small>
    </div>
  `).join('') : '<div class="stack-item"><strong>没有明显实验残留</strong><p>当前输入里未找到实验完成后仍保留的代码块。</p></div>';
}

function renderReminders(items) {
  const list = $('#reminderList');
  list.innerHTML = items.map((item) => `
    <div class="stack-item">
      <strong>${escapeHtml(item)}</strong>
    </div>
  `).join('');
}

function renderGraph(data) {
  const svg = $('#graph');
  const { nodes, edges } = data.graph;
  const lines = edges.map((edge) => {
    const from = nodes.find((node) => node.id === edge.from);
    const to = nodes.find((node) => node.id === edge.to);
    if (!from || !to) return '';
    const color = edge.polarity === 'negative' ? '#d99a32' : '#168eae';
    return `<line x1="${from.x + 110}" y1="${from.y + 20}" x2="${to.x}" y2="${to.y + 20}" stroke="${color}" stroke-width="2" stroke-linecap="round" opacity="0.72" />`;
  }).join('');
  const dots = nodes.map((node) => {
    const fill = node.kind === 'flag' ? (node.risk === 'high' ? '#f3c9d2' : node.risk === 'medium' ? '#f7e5be' : '#ccecf4') : '#f1f8fb';
    const stroke = node.kind === 'flag' ? '#a8cfdb' : '#b7d7e2';
    const label = node.kind === 'flag' ? `<text x="${node.x + 18}" y="${node.y + 16}" class="node-label">${escapeHtml(node.label)}</text><text x="${node.x + 18}" y="${node.y + 34}" class="node-sub">priority ${escapeHtml(String(node.score))}</text>` : `<text x="${node.x - 10}" y="${node.y + 16}" text-anchor="end" class="node-label">${escapeHtml(node.label)}</text>`;
    return `
      <g>
        <rect x="${node.x}" y="${node.y}" rx="16" ry="16" width="${node.kind === 'flag' ? 210 : 250}" height="42" fill="${fill}" stroke="${stroke}" />
        ${label}
      </g>
    `;
  }).join('');
  svg.innerHTML = lines + dots;
}

function renderAnalysis(data) {
  state.analysis = data;
  renderSummary(data);
  renderFlagTable(data.flags);
  renderBranchTable(data.dead_branches);
  renderCleanupList(data.cleanup_list);
  renderResidue(data.experimental_residue);
  renderReminders(data.reminders);
  renderGraph(data);
}

function exportReport() {
  if (!state.analysis) return toast('请先完成一次扫描');
  const data = state.analysis;
  const lines = [
    '# Feature Flag Janitor 扫描报告',
    `生成时间：${data.generated_at}`,
    '',
    '## 概览',
    `- 总 flags：${data.summary.total_flags}`,
    `- 过期 flags：${data.summary.expired_flags}`,
    `- 死分支：${data.summary.dead_branches}`,
    `- 实验残留：${data.summary.experimental_residue}`,
    `- 风险分：${data.summary.risk_score}`,
    '',
    '## 清理候选',
    ...data.cleanup_list.map((item) => `- ${item.priority} ${item.task}：${item.detail}（文件：${item.files.join(', ') || 'manifest'}）`),
    '',
    '## 安全提醒',
    ...data.reminders.map((item) => `- ${item}`),
  ];
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `feature-flag-report-${new Date().toISOString().slice(0, 10)}.md`;
  link.click();
  URL.revokeObjectURL(url);
  toast('报告已导出');
}

function downloadBlob(content, type, filename) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function exportJson() {
  if (!state.analysis) return toast('请先完成一次扫描');
  downloadBlob(JSON.stringify(state.analysis, null, 2), 'application/json;charset=utf-8', `feature-flag-report-${new Date().toISOString().slice(0, 10)}.json`);
  toast('JSON 数据已导出');
}

async function analyze() {
  const payload = {
    manifest_text: $('#manifestText').value,
    experiments_text: $('#experimentsText').value,
    releases_text: $('#releasesText').value,
    code_files: state.files,
    today: new Date().toISOString().slice(0, 10),
    source_meta: state.sourceMeta,
  };
  $('#analyzeBtn').disabled = true;
  $('#analyzeBtn').innerHTML = `${icons.scan}扫描中`;
  try {
    const result = await api('/api/analyze', { method: 'POST', body: JSON.stringify(payload) });
    renderAnalysis(result);
    toast(`扫描完成，发现 ${result.summary.clean_candidates} 个清理候选`);
  } catch (error) {
    toast(error.message);
  } finally {
    $('#analyzeBtn').disabled = false;
    $('#analyzeBtn').innerHTML = `${icons.scan}开始扫描`;
  }
}

async function saveAction(item, action) {
  if (!state.analysis?.scan_id || !item.dataset.findingKey) throw new Error('当前结果缺少扫描标识');
  return api('/api/actions', {
    method: 'POST',
    body: JSON.stringify({ scan_id: state.analysis.scan_id, finding_key: item.dataset.findingKey, action }),
  });
}

async function loadHistory() {
  const result = await api('/api/scans');
  const list = $('#historyList');
  list.innerHTML = result.scans.length ? result.scans.map((scan) => `
    <button class="history-item" data-scan-id="${escapeHtml(scan.scan_id)}">
      <strong>${escapeHtml(scan.scan_id)}</strong>
      <small>${escapeHtml(new Date(scan.created_at).toLocaleString('zh-CN', { hour12: false }))} · ${scan.summary.clean_candidates} 个高优先级项 · 风险 ${scan.summary.risk_score}</small>
    </button>
  `).join('') : '<div class="stack-item"><strong>还没有扫描记录</strong><p>完成一次扫描后会自动保存。</p></div>';
}

async function openHistory() {
  $('#historyDrawer').hidden = false;
  try { await loadHistory(); } catch (error) { toast(error.message); }
}

function loadPayload(payload) {
  $('#manifestText').value = payload.manifest_text;
  $('#experimentsText').value = payload.experiments_text;
  $('#releasesText').value = payload.releases_text;
  state.files = payload.code_files.map((file) => ({
    path: file.path,
    content: file.content,
    lines: file.content.split(/\r?\n/).length,
    kind: file.path.split('.').pop().toUpperCase(),
  }));
  state.sourceMeta = payload.source_meta || { kind: 'sample' };
  renderFiles();
}

function readFiles(list) {
  return Promise.all([...list].map(async (file) => ({
    path: file.webkitRelativePath || file.name,
    content: await file.text(),
  })));
}

async function importZip(file) {
  const response = await fetch('/api/import-zip', { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'ZIP 导入失败');
  state.files = data.code_files.map((item) => ({ ...item, lines: item.content.split(/\r?\n/).length, kind: item.path.split('.').pop().toUpperCase() }));
  state.sourceMeta = { kind: 'zip', loaded_files: state.files.length };
  renderFiles();
  toast(`已导入 ${state.files.length} 个文本文件`);
  await analyze();
}

async function importGithub() {
  const repoUrl = $('#githubUrl').value.trim();
  if (!repoUrl) return toast('请先填写 GitHub 仓库地址');
  $('#githubBtn').disabled = true;
  try {
    const data = await api('/api/import-github', { method: 'POST', body: JSON.stringify({ repo_url: repoUrl }) });
    state.files = data.code_files.map((item) => ({ ...item, lines: item.content.split(/\r?\n/).length, kind: item.path.split('.').pop().toUpperCase() }));
    state.sourceMeta = data.source_meta;
    renderFiles();
    toast(`已读取 ${data.repo.owner}/${data.repo.name} · ${data.repo.branch}`);
    await analyze();
  } catch (error) { toast(error.message); }
  finally { $('#githubBtn').disabled = false; }
}

async function loadSample() {
  const payload = await api('/api/sample');
  loadPayload(payload);
  await analyze();
}

$('#analyzeBtn').addEventListener('click', analyze);
$('#loadSampleBtn').addEventListener('click', loadSample);
$('#exportBtn').addEventListener('click', exportReport);
$('#exportJsonBtn').addEventListener('click', exportJson);
$('#historyBtn').addEventListener('click', openHistory);
$('#closeHistoryBtn').addEventListener('click', () => { $('#historyDrawer').hidden = true; });
$('#closeDetailBtn').addEventListener('click', closeFlagDetail);
$('#detailBackdrop').addEventListener('click', closeFlagDetail);

$('#flagTable').addEventListener('click', (event) => {
  const row = event.target.closest('[data-flag-key]');
  if (row) openFlagDetail(row.dataset.flagKey);
});
$('#flagTable').addEventListener('keydown', (event) => {
  if (!['Enter', ' '].includes(event.key)) return;
  const row = event.target.closest('[data-flag-key]');
  if (row) { event.preventDefault(); openFlagDetail(row.dataset.flagKey); }
});

$$('.side-nav-item').forEach((button) => {
  button.addEventListener('click', () => {
    const target = document.getElementById(button.dataset.target);
    if (!target) return;
    $$('.side-nav-item').forEach((item) => item.classList.remove('is-active'));
    button.classList.add('is-active');
    const view = button.dataset.target.replace('Section', '');
    $('.layout').className = `layout view-${view}`;
  });
});

$('#cleanupList').addEventListener('click', (event) => {
  const button = event.target.closest('[data-task-action]');
  if (!button) return;
  const item = button.closest('.cleanup-item');
  const action = button.dataset.taskAction;
  saveAction(item, action).then(() => {
    item.classList.toggle('is-dismissed', action === 'resolve' || action === 'ignore');
    button.closest('.item-actions').innerHTML = '<span class="task-state">已保存，刷新后仍保留</span>';
    state.analysis.actions = { ...(state.analysis.actions || {}), [item.dataset.findingKey]: { action } };
    toast(action === 'ignore' ? '已忽略此项' : action === 'resolve' ? '已标记完成' : '已标记稍后处理');
  }).catch((error) => toast(error.message));
});

$('#historyList').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-scan-id]');
  if (!button) return;
  try {
    const result = await api(`/api/scans/${encodeURIComponent(button.dataset.scanId)}`);
    renderAnalysis(result);
    $('#historyDrawer').hidden = true;
    toast('已加载历史扫描');
  } catch (error) { toast(error.message); }
});

$('#dropzone').addEventListener('click', () => $('#fileInput').click());
$('#zipBtn').addEventListener('click', () => $('#zipInput').click());
$('#zipInput').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try { await importZip(file); } catch (error) { toast(error.message); }
});
$('#githubBtn').addEventListener('click', importGithub);
$('#fileInput').addEventListener('change', async (event) => {
  const files = await readFiles(event.target.files);
  state.files = files.map((file) => ({
    path: file.path,
    content: file.content,
    lines: file.content.split(/\r?\n/).length,
    kind: file.path.split('.').pop().toUpperCase(),
  }));
  state.sourceMeta = { kind: 'manual', loaded_files: state.files.length };
  renderFiles();
  toast(`已载入 ${state.files.length} 个文件`);
  await analyze();
});

$('#dropzone').addEventListener('dragover', (event) => {
  event.preventDefault();
  $('#dropzone').style.borderColor = '#168eae';
});
$('#dropzone').addEventListener('dragleave', () => {
  $('#dropzone').style.borderColor = '#8ccddd';
});
$('#dropzone').addEventListener('drop', async (event) => {
  event.preventDefault();
  $('#dropzone').style.borderColor = '#8ccddd';
  const files = await readFiles(event.dataTransfer.files);
  state.files = files.map((file) => ({
    path: file.path,
    content: file.content,
    lines: file.content.split(/\r?\n/).length,
    kind: file.path.split('.').pop().toUpperCase(),
  }));
  state.sourceMeta = { kind: 'manual', loaded_files: state.files.length };
  renderFiles();
  toast(`拖入 ${state.files.length} 个文件`);
  await analyze();
});

wireIcons();
loadSample();
