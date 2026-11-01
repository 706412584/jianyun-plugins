/* 简云网页控制台 · 前端逻辑
   数据全部来自 App 的本地控制接口（经插件反代）。
   写操作都是异步任务：提交拿 jobId → 轮询 → 完成刷新。
   四个视图：站点 / 组件 / 插件 / AI 助手。 */

/* 前端版本：显示在页脚。排查「明明修了却还是旧样子」时第一眼就看它——
   大概率是浏览器缓存了旧 JS，而不是修复没生效。 */
const WEB_CONSOLE_VERSION = '0.12.0';

const $ = (id) => document.getElementById(id);

/* ---------------- 请求 ---------------- */

async function api(path, options) {
  const response = await fetch(path, Object.assign({ headers: { Accept: 'application/json' } }, options));
  let data;
  try {
    data = await response.json();
  } catch (e) {
    data = { ok: false, error: '接口返回的不是 JSON（HTTP ' + response.status + '）' };
  }
  if (!response.ok) {
    const error = new Error(data.error || 'HTTP ' + response.status);
    error.status = response.status;
    throw error;
  }
  return data;
}

async function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
}

function escapeHtml(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

/* 站点地址里的主机名：用浏览器当前访问的主机，点了才打得开
   （App 与容器共享网络命名空间，站点端口在设备本机就能直连） */
function currentHost() {
  return location.hostname || '127.0.0.1';
}

/* ---------------- 连接状态 ---------------- */

async function loadHealth() {
  try {
    await api('/api/health');
    $('connDot').className = 'dot dot-success pulse';
    $('connText').textContent = 'App 已连接';
    return true;
  } catch (error) {
    if (error.status === 401) {
      location.href = '/login.html';
      return false;
    }
    $('connDot').className = 'dot dot-danger';
    $('connText').textContent = 'App 未连接';
    return false;
  }
}

/* ---------------- 异步任务 ---------------- */

const jobTimers = {};

function watchJob(jobId, title, barId, onDone) {
  const bar = $(barId);
  const startedAt = Date.now();
  let ticker = null;

  const showRunning = () => {
    const sec = Math.round((Date.now() - startedAt) / 1000);
    bar.innerHTML = '<div class="banner banner-info jobbar">' +
      '<span class="spin"></span><div><div>' + escapeHtml(title) + '</div>' +
      '<div class="wrap-note">已执行 ' + sec + ' 秒，完成后自动刷新</div></div></div>';
  };

  const stop = () => {
    clearInterval(ticker);
    clearInterval(jobTimers[jobId]);
  };

  showRunning();
  clearInterval(jobTimers[jobId]);
  // 装组件这类操作是分钟级的，不给耗时用户会以为卡死了
  ticker = setInterval(showRunning, 1000);

  jobTimers[jobId] = setInterval(async () => {
    let job;
    try {
      job = await api('/api/jobs/' + jobId);
    } catch (e) {
      stop();
      bar.innerHTML = '';
      toast('查任务状态失败：' + e.message);
      return;
    }
    if (job.state === 'running') {
      showRunning();
      return;
    }

    stop();
    bar.innerHTML = '';
    if (job.state === 'done') {
      toast(title + ' 已完成');
    } else {
      bar.innerHTML = '<div class="banner banner-err jobbar">' +
        '<svg viewBox="0 0 24 24"><path d="M12 9v4M12 17h.01"/><circle cx="12" cy="12" r="9"/></svg>' +
        '<div><div>' + escapeHtml(title) + ' 失败</div>' +
        '<div class="wrap-note">' + escapeHtml(job.message || '未知原因') +
        '。部分操作实际已经生效，可以在列表里核对一下。</div></div></div>';
    }
    // 成功与失败都刷新：失败也可能只差退出码（组件已经装上、探测却没跑完）
    if (onDone) onDone();
  }, 1500);
}

function submitJob(path, body, title, barId, onDone) {
  return post(path, body).then((res) => {
    watchJob(res.jobId, title, barId, onDone);
  }).catch((error) => toast(error.message));
}

/* ================= 站点 ================= */

let sites = [];
let manageTarget = null;
let deleteTarget = null;
let phpVersions = [];

function stateOf(site) {
  // 先看开关：停用之后 nginx 可能还会短暂返回 502（vhost 已移走、后端已停），
  // 那是停站过程中的过渡状态，报「后端异常」会让人以为站点坏了
  if (!site.enabled) return { cls: 'dot-disabled', text: '已停止', off: true };
  if (site.serving) return { cls: 'dot-success pulse', text: '运行中', off: false };
  if (site.failed) return { cls: 'dot-warning', text: '后端异常', off: false };
  return { cls: 'dot-disabled', text: '未在服务', off: true };
}

async function loadSites() {
  let data;
  try {
    data = await api('/api/sites');
  } catch (error) {
    $('sitesSkeleton').style.display = 'none';
    $('sitesError').style.display = 'flex';
    $('sitesErrorText').textContent = error.message;
    return;
  }

  $('sitesSkeleton').style.display = 'none';
  $('sitesError').style.display = 'none';
  sites = data.sites || [];
  phpVersions = data.phpVersions || [];
  fillPhpVersions();

  if (!sites.length) {
    $('sitesOverview').style.display = 'none';
    $('sitesGrid').style.display = 'none';
    $('sitesEmpty').style.display = 'block';
    $('sitesCount').textContent = '';
    return;
  }
  $('sitesOverview').style.display = 'block';
  $('sitesGrid').style.display = 'grid';
  $('sitesEmpty').style.display = 'none';

  const running = sites.filter((s) => s.serving).length;
  const stopped = sites.filter((s) => !s.serving && !s.failed).length;
  const failed = sites.filter((s) => s.failed).length;
  $('sitesCount').textContent = sites.length + ' 个站点 · ' + running + ' 个在服务';
  $('stats').innerHTML =
    stat(sites.length, '站点总数', '') +
    stat(running, '运行中', 'accent') +
    stat(stopped, '已停止', '') +
    stat(failed, '异常', failed ? 'warn' : '');

  $('sitesGrid').innerHTML = sites.map(renderSite).join('');
}

function stat(value, label, cls) {
  return '<div><div class="stat-val ' + cls + '">' + value + '</div>' +
    '<div class="stat-label">' + label + '</div></div>';
}

function renderSite(site) {
  const state = stateOf(site);
  const url = 'http://' + escapeHtml(currentHost()) + ':' + site.port + '/';
  const sub = escapeHtml(site.engineLabel) + ' · ' + site.port +
    (site.phpVersion ? ' · PHP ' + escapeHtml(site.phpVersion) : '');
  const primaryBtn = state.off
    ? '<button class="btn btn-sm btn-soft" onclick="siteAct(' + site.id + ',\'start\')">启动</button>'
    : '<button class="btn btn-sm btn-soft" onclick="siteAct(' + site.id + ',\'stop\')">停止</button>';

  return '<article class="card site' + (state.off ? ' off' : '') + '">' +
    '<div class="site-top">' +
      '<div class="site-id">' + engineIcon(site.engine) +
        '<div style="min-width:0"><div class="site-name">' + escapeHtml(site.name) + '</div>' +
        '<div class="site-sub">' + sub + '</div></div>' +
      '</div>' +
      '<span class="status-row"><span class="dot ' + state.cls + '"></span>' +
      '<span style="font-size:12px;color:var(--text-sec)">' + state.text + '</span></span>' +
    '</div>' +
    '<a class="site-url" href="' + url + '" target="_blank" rel="noopener">' +
      '<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/>' +
      '<path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>' + url + '</a>' +
    '<div class="site-foot">' + primaryBtn +
      '<button class="btn btn-sm btn-outline-gray" onclick="openManage(' + site.id + ')">管理</button>' +
    '</div>' +
  '</article>';
}

function engineIcon(engine) {
  const wrap = (cls, path) => '<span class="cell-ic ' + cls + '"><svg viewBox="0 0 24 24">' + path + '</svg></span>';
  switch (engine) {
    case 'php':
      return wrap('ic-success', '<path d="M4 7h16v10H4z"/><path d="M4 11h16M9 7v10M15 7v10"/>');
    case 'node':
    case 'python':
    case 'go':
    case 'custom':
      return wrap('ic-warning', '<path d="M12 3l9 5v8l-9 5-9-5V8z"/><path d="M12 12l9-5M12 12v10M12 12L3 7"/>');
    default:
      return wrap('ic-primary', '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18"/>');
  }
}

async function siteAct(id, action) {
  const site = sites.find((s) => s.id === id);
  const name = site ? site.name : id;
  const titles = { start: '启动站点「' + name + '」', stop: '停止站点「' + name + '」', restart: '重启站点「' + name + '」' };
  submitJob('/api/sites/' + id + '/' + action, null, titles[action], 'jobBar', loadSites);
}

function openManage(id) {
  manageTarget = id;
  const site = sites.find((s) => s.id === id);
  $('m-title').textContent = site ? site.name : '站点';
  openMask('manageMask');
}

function manageAction(action) {
  closeMask('manageMask');
  if (manageTarget != null) siteAct(manageTarget, action);
}

function askDelete() {
  closeMask('manageMask');
  deleteTarget = manageTarget;
  const site = sites.find((s) => s.id === deleteTarget);
  $('d-text').textContent = '确定删除「' + (site ? site.name : deleteTarget) +
    '」？默认只删配置，站点文件保留。';
  $('d-files').classList.remove('checked');
  openMask('deleteMask');
}

function toggleDeleteFiles() {
  $('d-files').classList.toggle('checked');
}

$('d-confirm').addEventListener('click', () => {
  closeMask('deleteMask');
  const withFiles = $('d-files').classList.contains('checked');
  const site = sites.find((s) => s.id === deleteTarget);
  const title = '删除站点「' + (site ? site.name : deleteTarget) + '」';
  api('/api/sites/' + deleteTarget + (withFiles ? '?deleteFiles=1' : ''), { method: 'DELETE' })
    .then((res) => watchJob(res.jobId, title, 'jobBar', loadSites))
    .catch((error) => toast(error.message));
});

/* ---------------- 新建站点 ---------------- */

let engine = 'static';

function fillPhpVersions() {
  const select = $('f-php');
  if (!phpVersions.length) {
    select.innerHTML = '<option value="">（没有可用版本，请先装 PHP）</option>';
    return;
  }
  select.innerHTML = phpVersions.map((v) => '<option value="' + escapeHtml(v) + '">' + escapeHtml(v) + '</option>').join('');
}

function syncFormVisibility() {
  const isPhp = engine === 'php';
  const isProxy = ['node', 'python', 'go', 'custom'].indexOf(engine) >= 0;
  $('f-php-wrap').style.display = isPhp ? 'block' : 'none';
  $('f-cmd-wrap').style.display = isProxy ? 'block' : 'none';
  $('f-db-wrap').style.display = isPhp ? 'block' : 'none';
  if (!isPhp) {
    $('f-createdb').classList.remove('on');
    $('f-db-fields').style.display = 'none';
  }
}

function openNewSite() {
  $('f-name').value = '';
  $('f-subdir').value = '';
  $('f-cmd').value = '';
  $('f-dbname').value = '';
  $('f-dbpass').value = '';
  $('f-name-msg').className = 'field-msg tip';
  $('f-name-msg').textContent = '字母、数字与 - _ . ，最长 32 位';
  let port = 10086;
  const used = sites.map((s) => s.port);
  while (used.indexOf(port) >= 0) port++;
  $('f-port').value = port;
  openMask('newSiteMask');
}

$('newSiteBtn').addEventListener('click', openNewSite);

$('f-engine').addEventListener('click', (event) => {
  const btn = event.target.closest('button');
  if (!btn) return;
  $('f-engine').querySelectorAll('button').forEach((b) => b.classList.remove('active'));
  btn.classList.add('active');
  engine = btn.dataset.engine;
  syncFormVisibility();
});

$('f-createdb').addEventListener('click', () => {
  tg($('f-createdb'));
  $('f-db-fields').style.display = $('f-createdb').classList.contains('on') ? 'block' : 'none';
});

$('f-submit').addEventListener('click', async () => {
  const name = $('f-name').value.trim();
  if (!name) {
    $('f-name-msg').className = 'field-msg err';
    $('f-name-msg').textContent = '站点名称不能为空';
    return;
  }
  const payload = {
    name: name,
    port: $('f-port').value.trim(),
    subdir: $('f-subdir').value.trim(),
    engine: engine,
    phpVersion: engine === 'php' ? $('f-php').value : '',
    startCommand: $('f-cmd').value.trim(),
    createDb: engine === 'php' && $('f-createdb').classList.contains('on'),
    dbEngine: $('f-dbengine').value,
    dbName: $('f-dbname').value.trim(),
    dbUser: $('f-dbname').value.trim(),
    dbPassword: $('f-dbpass').value.trim(),
  };
  const button = $('f-submit');
  button.classList.add('btn-loading');
  button.innerHTML = '<span class="spin"></span>提交中';
  try {
    const res = await post('/api/sites', payload);
    closeMask('newSiteMask');
    watchJob(res.jobId, '新建站点「' + name + '」', 'jobBar', loadSites);
  } catch (error) {
    toast(error.message);
  } finally {
    button.classList.remove('btn-loading');
    button.textContent = '创建站点';
  }
});

/* ================= 组件 ================= */

let components = [];
/** 进组件页的时刻：用来算「已等 N 秒」，探测卡住时用户才知道不是自己看错了 */
let compEnteredAt = 0;
/** 最近一次组件响应：探测日志直接从这里取，不再单独发请求（少一个失败点） */
let lastComponentsData = null;

async function loadComponents() {
  let data;
  try {
    data = await api('/api/components');
  } catch (error) {
    $('compGrid').innerHTML = '<div class="card"><span class="muted">' + escapeHtml(error.message) + '</span></div>';
    return;
  }
  components = data.components || [];
  lastComponentsData = data;
  const installed = components.filter((c) => c.installed.length).length;
  $('compCount').textContent = installed + ' / ' + components.length + ' 个已安装' +
    (data.loading ? ' · 刷新中' : '');

  // 只有「手里一点数据都没有」时才用警告条。已经有数据时还报「正在探测」是误报：
  // loading 只是说后台在重算，旧值本来就能用，吓人一下只会让人以为坏了
  const hasData = installed > 0;
  if (!data.containerReady) {
    $('compWarn').style.display = 'flex';
    $('compWarnText').textContent = '容器还没就绪，先到 App 里完成初始化';
  } else if (data.loading && !hasData) {
    const waited = compEnteredAt ? Math.round((Date.now() - compEnteredAt) / 1000) : 0;
    $('compWarn').style.display = 'flex';
    $('compWarnText').innerHTML = '正在探测容器内的组件状态' +
      (waited ? '，已等 ' + waited + ' 秒' : '') +
      '…<a href="#" style="color:var(--primary);margin-left:6px" onclick="refreshComponents();return false">重新探测</a>' +
      (waited > 90 ? '<br>超过 90 秒还没结果，多半是容器没跑起来：先到 App 里启动容器再回来。' : '');
  } else {
    $('compWarn').style.display = 'none';
  }

  // 探测日志是「一直转圈」时唯一的线索，折叠着不碍事
  const lines = data.log || [];
  $('compLog').innerHTML =
    (lines.length
      ? '<details class="comp-log"><summary>执行日志（' + lines.length + ' 行）</summary>' +
        '<pre class="log-pre">' + escapeHtml(lines.join('\n')) + '</pre></details>'
      : '') +
    '<div style="margin-bottom:var(--s-4)">' +
    '<a href="#" style="font-size:12.5px;color:var(--text-sec)" onclick="showDetectLog();return false">' +
    '查看探测脚本完整输出</a></div>';

  $('compGrid').innerHTML = components.map(renderComponent).join('');
}

function renderComponent(comp) {
  const hasInstalled = comp.installed.length > 0;
  const sub = hasInstalled
    ? '已装 ' + comp.installed.join(' / ') + (comp.default ? ' · 当前 ' + comp.default : '')
    : '未安装';
  const cls = hasInstalled ? 'ic-success' : 'ic-primary';
  return '<article class="card site" style="gap:var(--s-2)">' +
    '<div class="comp-head">' +
      '<div class="site-id">' +
        '<span class="cell-ic ' + cls + '"><svg viewBox="0 0 24 24">' +
        '<path d="M12 3l9 5v8l-9 5-9-5V8z"/><path d="M12 12l9-5M12 12v10M12 12L3 7"/></svg></span>' +
        '<div style="min-width:0"><div class="site-name">' + escapeHtml(comp.name) + '</div>' +
        '<div class="site-sub">' + escapeHtml(sub) + '</div></div>' +
      '</div>' +
    '</div>' +
    '<div class="comp-tags">' + (comp.installed.length
      ? comp.installed.map((v) => '<span class="tag' + (v === comp.default ? ' tag-success' : '') + '">' + escapeHtml(v) + '</span>').join('')
      : '<span class="tag tag-outline">还没装</span>') + '</div>' +
    '<div class="comp-foot">' +
      '<button class="btn btn-sm btn-outline-gray" onclick="openComponent(\'' + comp.family + '\')">管理</button>' +
    '</div>' +
  '</article>';
}

function openComponent(family) {
  const comp = components.find((c) => c.family === family);
  if (!comp) return;
  $('c-title').textContent = comp.name;

  const installedRows = comp.installed.map((v) => {
    const isCurrent = v === comp.default;
    return '<div class="cell">' +
      '<div class="cell-main"><div class="cell-title">' + escapeHtml(v) +
      (isCurrent ? ' <span class="tag tag-success">当前</span>' : '') + '</div></div>' +
      (isCurrent ? '' : '<button class="btn btn-sm btn-soft" onclick="compAct(\'' + family + '\',\'switch\',\'' + v + '\')">切换</button>') +
      '<button class="btn btn-sm btn-danger-text" onclick="compAct(\'' + family + '\',\'uninstall\',\'' + v + '\')">卸载</button>' +
      '</div>';
  }).join('') || '<div class="cell"><div class="cell-main"><div class="cell-sub">还没有安装任何版本</div></div></div>';

  const toInstall = (comp.catalogVersions || []).filter((v) => comp.installed.indexOf(v) < 0);
  const installRows = toInstall.map((v) =>
    '<div class="cell">' +
      '<div class="cell-main"><div class="cell-title">' + escapeHtml(v) + '</div></div>' +
      '<button class="btn btn-sm btn-soft" onclick="compAct(\'' + family + '\',\'install\',\'' + v + '\')">安装</button>' +
    '</div>').join('') || '<div class="cell"><div class="cell-main"><div class="cell-sub">目录里的版本都已经装了</div></div></div>';

  $('c-body').innerHTML =
    '<div class="field-label">已安装</div>' +
    '<div class="cell-group" style="margin-bottom:var(--s-4)">' + installedRows + '</div>' +
    '<div class="field-label">可安装</div>' +
    '<div class="cell-group">' + installRows + '</div>' +
    '<div class="field-msg tip" style="margin-top:var(--s-3)">' +
    '安装与切换是分钟级操作（要跑容器命令），提交后进度显示在页面上方。</div>';

  openMask('compMask');
}

function compAct(family, op, version) {
  closeMask('compMask');
  const comp = components.find((c) => c.family === family);
  const name = comp ? comp.name : family;
  const titles = {
    install: '安装 ' + name + ' ' + version,
    uninstall: '卸载 ' + name + ' ' + version,
    switch: '切换 ' + name + ' 到 ' + version,
  };
  submitJob('/api/components/' + family + '/' + op, { version: version }, titles[op], 'compJobBar', loadComponents);
}

function refreshComponents() {
  submitJob('/api/components/refresh', null, '刷新组件状态', 'compJobBar', loadComponents);
}

$('compRefreshBtn').addEventListener('click', refreshComponents);

/* ================= 插件 ================= */

let plugins = [];
let pluginPollTimer = null;

async function loadPlugins() {
  let data;
  try {
    data = await api('/api/plugins');
  } catch (error) {
    $('pluginGrid').innerHTML = '<div class="card"><span class="muted">' + escapeHtml(error.message) + '</span></div>';
    return;
  }
  plugins = data.plugins || [];
  const running = plugins.filter((p) => p.status === 'running').length;
  $('pluginCount').textContent = plugins.length
    ? plugins.length + ' 个插件 · ' + running + ' 个运行中'
    : '';

  // 宿主算插件状态要对每个插件做一次端口探测（最长 3 秒），首次拿到空数据是正常的
  if (!plugins.length && (data.refreshing || data.stale)) {
    $('pluginGrid').innerHTML = '<div class="card"><div class="empty">' +
      '<span class="spin" style="width:22px;height:22px;border-width:3px"></span>' +
      '<div class="empty-t">正在读取插件状态</div>' +
      '<div class="empty-d">宿主需要对每个插件探一次端口，稍等几秒</div></div></div>';
  } else {
    $('pluginGrid').innerHTML = plugins.length
      ? plugins.map(renderPlugin).join('')
      : '<div class="card"><div class="empty">' +
        '<svg viewBox="0 0 24 24"><path d="M9 3v6M15 3v6M7 9h10v4a5 5 0 0 1-10 0z"/><path d="M12 18v3"/></svg>' +
        '<div class="empty-t">还没有装插件</div><div class="empty-d">导入一个 .plugin 包试试</div></div></div>';
  }

  // 插件状态是「读缓存 + 后台刷新」的，缓存没就绪就快点重拉
  clearTimeout(pluginPollTimer);
  if (data.refreshing || data.stale) {
    pluginPollTimer = setTimeout(loadPlugins, 1000);
  }
}

function pluginState(status) {
  switch (status) {
    case 'running': return { cls: 'dot-success pulse', text: '运行中' };
    case 'port_taken': return { cls: 'dot-warning', text: '端口被占' };
    default: return { cls: 'dot-disabled', text: '已停止' };
  }
}

function renderPlugin(plugin) {
  const state = pluginState(plugin.status);
  const sub = 'v' + plugin.version + (plugin.isService ? ' · 端口 ' + plugin.servicePort : ' · 非服务');
  const isRunning = plugin.status === 'running';
  const source = plugin.isService
    ? '<button class="btn btn-sm btn-soft" onclick="pluginAct(\'' + plugin.id + '\',\'' +
      (isRunning ? 'stop' : 'start') + '\')">' + (isRunning ? '停止' : '启动') + '</button>'
    : '<button class="btn btn-sm btn-soft" onclick="pluginAct(\'' + plugin.id + '\',\'install\')">安装</button>';
  return '<article class="card site">' +
    '<div class="site-top">' +
      '<div class="site-id">' +
        '<span class="cell-ic ic-primary"><svg viewBox="0 0 24 24">' +
        '<path d="M9 3v6M15 3v6M7 9h10v4a5 5 0 0 1-10 0z"/><path d="M12 18v3"/></svg></span>' +
        '<div style="min-width:0"><div class="site-name">' + escapeHtml(plugin.name) + '</div>' +
        '<div class="site-sub">' + escapeHtml(sub) + '</div></div>' +
      '</div>' +
      '<span class="status-row"><span class="dot ' + state.cls + '"></span>' +
      '<span style="font-size:12px;color:var(--text-sec)">' + state.text + '</span></span>' +
    '</div>' +
    (plugin.description ? '<div class="wrap-note">' + escapeHtml(plugin.description) + '</div>' : '') +
    (plugin.detail && plugin.status === 'port_taken'
      ? '<div class="wrap-note">' + escapeHtml(plugin.detail) + '</div>' : '') +
    '<div class="plugin-foot">' + source +
      (plugin.settings && plugin.settings.length
        ? '<button class="btn btn-sm btn-outline-gray" onclick="openPluginSettings(\'' + plugin.id + '\')">设置</button>'
        : '') +
      '<button class="btn btn-sm btn-danger-text" onclick="pluginAct(\'' + plugin.id + '\',\'uninstall\')">卸载</button>' +
    '</div>' +
  '</article>';
}

/* ---------- 插件设置 ---------- */

let settingsTarget = null;

function settingField(setting, value) {
  const key = setting.key;
  const label = '<div class="field-label">' + escapeHtml(setting.label || key) + '</div>';
  const hint = setting.hint ? '<div class="field-msg tip">' + escapeHtml(setting.hint) + '</div>' : '';
  const val = value == null ? '' : String(value);

  if (setting.type === 'toggle') {
    const on = val === 'true' || val === '1' || val === 'on';
    return '<div class="db-box"><div class="db-head">' +
      '<div><div class="db-title">' + escapeHtml(setting.label || key) + '</div>' +
      (setting.hint ? '<div class="db-sub">' + escapeHtml(setting.hint) + '</div>' : '') + '</div>' +
      '<div class="toggle' + (on ? ' on' : '') + '" data-setting="' + escapeHtml(key) + '" data-type="toggle"></div>' +
      '</div></div>';
  }
  if (setting.type === 'select') {
    const options = (setting.options || []).map((o) =>
      '<option value="' + escapeHtml(o.value) + '"' + (o.value === val ? ' selected' : '') + '>' +
      escapeHtml(o.label || o.value) + '</option>').join('');
    return '<div class="field">' + label +
      '<div class="select-wrap"><select data-setting="' + escapeHtml(key) + '" data-type="select">' +
      options + '</select><svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg></div>' + hint + '</div>';
  }
  if (setting.type === 'textarea') {
    return '<div class="field">' + label +
      '<div class="input-group"><textarea rows="3" data-setting="' + escapeHtml(key) + '" data-type="text">' +
      escapeHtml(val) + '</textarea></div>' + hint + '</div>';
  }
  if (setting.type === 'number') {
    return '<div class="field">' + label +
      '<div class="input-group"><input inputmode="numeric" data-setting="' + escapeHtml(key) + '" data-type="text" value="' +
      escapeHtml(val) + '"></div>' + hint + '</div>';
  }
  return '<div class="field">' + label +
    '<div class="input-group"><input data-setting="' + escapeHtml(key) + '" data-type="text" value="' +
    escapeHtml(val) + '" autocomplete="off"></div>' + hint + '</div>';
}

function openPluginSettings(id) {
  const plugin = plugins.find((p) => p.id === id);
  if (!plugin) return;
  settingsTarget = id;
  $('p-title').textContent = plugin.name + ' · 设置';
  const values = plugin.values || {};
  $('p-body').innerHTML = plugin.settings.map((s) => settingField(s, values[s.key])).join('') +
    '<div class="field-msg tip">改完需要重启一次这个插件服务，新设置才会生效。</div>';
  $('p-body').querySelectorAll('.toggle[data-setting]').forEach((el) => {
    el.addEventListener('click', () => el.classList.toggle('on'));
  });
  openMask('pluginMask');
}

$('p-save').addEventListener('click', async () => {
  const values = {};
  $('p-body').querySelectorAll('[data-setting]').forEach((el) => {
    const key = el.dataset.setting;
    values[key] = el.dataset.type === 'toggle' ? (el.classList.contains('on') ? 'true' : 'false') : el.value;
  });
  const button = $('p-save');
  button.classList.add('btn-loading');
  button.innerHTML = '<span class="spin"></span>保存中';
  try {
    await post('/api/plugins/' + settingsTarget + '/settings', { values: values });
    closeMask('pluginMask');
    toast('已保存，记得重启这个插件服务');
    loadPlugins();
  } catch (error) {
    toast(error.message);
  } finally {
    button.classList.remove('btn-loading');
    button.textContent = '保存';
  }
});

function pluginAct(id, action) {
  const plugin = plugins.find((p) => p.id === id);
  const name = plugin ? plugin.name : id;
  const titles = {
    start: '启动插件「' + name + '」',
    stop: '停止插件「' + name + '」',
    install: '安装插件「' + name + '」',
    uninstall: '卸载插件「' + name + '」',
  };
  submitJob('/api/plugins/' + id + '/' + action, null, titles[action], 'pluginJobBar', loadPlugins);
}

$('pluginRefreshBtn').addEventListener('click', () => loadPlugins());

$('pluginImportBtn').addEventListener('click', () => $('pluginFile').click());

$('pluginFile').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  const bar = $('pluginJobBar');
  bar.innerHTML = '<div class="banner banner-info jobbar"><span class="spin"></span>' +
    '<div><div>正在上传 ' + escapeHtml(file.name) + '</div>' +
    '<div class="wrap-note">' + Math.round(file.size / 1024) + ' KB</div></div></div>';
  try {
    const res = await api('/api/plugins/import?name=' + encodeURIComponent(file.name), {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    });
    watchJob(res.jobId, '导入并安装插件「' + file.name + '」', 'pluginJobBar', loadPlugins);
  } catch (error) {
    bar.innerHTML = '';
    toast(error.message);
  }
});

/* ================= AI 助手 ================= */

let chatMessages = [];
let chatStreaming = false;
let chatTimer = null;
let chatConversations = [];
let chatCurrentId = null;

async function loadChat(silent) {
  let data;
  try {
    data = await api('/api/chat/state');
  } catch (error) {
    if (!silent) toast(error.message);
    return;
  }
  chatStreaming = data.isStreaming;
  chatConversations = data.conversations || [];
  chatCurrentId = data.currentConversationId;
  // 工具链路的总闸是模型配置里的 caps.tools：没开的话模型根本拿不到工具定义，
  // 表现就是「让它调工具它也不会」——这条提示直接告诉用户去哪开
  const current = (data.models || []).find((m) => m.id === data.currentModelId);
  if (current && !current.tools) {
    $('chatWarn').style.display = 'flex';
    $('chatWarnText').textContent = '当前模型「' + current.name +
      '」没开工具调用，AI 不会执行命令或读写文件。到 App 的模型配置里把「工具」打开即可。';
  } else {
    $('chatWarn').style.display = 'none';
  }
  $('chatTitle').textContent = data.currentTitle ? escapeHtml(data.currentTitle) : '（新会话）';
  $('chatSend').textContent = chatStreaming ? '停止' : '发送';
  $('chatSend').className = 'btn ' + (chatStreaming ? 'btn-danger-soft' : 'btn-primary');
  renderModels(data.models || [], data.currentModelId, data.currentChatModel);
  renderChat(data.messages || []);
  scheduleChat();
}

function renderModels(models, currentId, currentModel) {
  const select = $('chatModel');
  if (select.dataset.count === String(models.length) && select.dataset.current === String(currentId) + '|' + currentModel) return;
  select.dataset.count = String(models.length);
  select.dataset.current = String(currentId) + '|' + currentModel;
  if (!models.length) {
    select.innerHTML = '<option value="">未配置模型</option>';
    return;
  }
  select.innerHTML = models.map((m) => {
    const label = m.name + (m.model ? ' · ' + m.model : '');
    return '<option value="' + m.id + '"' + (m.id === currentId ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
  }).join('');
}

function renderChat(messages) {
  const list = $('chatList');
  const sameLength = messages.length === chatMessages.length;
  // 对比内容、工具卡状态与待发状态：只看条数会吞掉流式增量与工具进度
  const lastSame = sameLength && messages.length > 0 && (function () {
    const a = messages[messages.length - 1];
    const b = chatMessages[chatMessages.length - 1];
    return a.content === b.content && a.pending === b.pending &&
      (a.toolCalls || []).length === (b.toolCalls || []).length &&
      String((a.toolCalls || []).map((t) => t.status)) === String((b.toolCalls || []).map((t) => t.status));
  })();
  if (sameLength && lastSame) return;
  chatMessages = messages;

  if (!messages.length) {
    list.innerHTML = '<div class="empty">' +
      '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.3 7.3L4 21l1.7-5.7A8 8 0 1 1 21 12z"/></svg>' +
      '<div class="empty-t">和 App 里是同一个会话</div>' +
      '<div class="empty-d">在手机上聊到一半，切到浏览器能接着看</div></div>';
    return;
  }
  list.innerHTML = messages.map((m) => {
    const role = m.role === 'user' ? 'user' : 'assistant';
    const err = m.error ? ' err' : '';
    const tools = (m.toolCalls || []).map(renderToolCard).join('');
    const atts = renderAttachments(m);
    const reason = m.reasoning ? renderReason(m) : '';
    const pending = m.pending && !m.content
      ? '<span class="spin"></span> ' : '';
    return '<div class="bubble ' + role + err + '">' + reason + atts + tools + pending + renderMarkdown(m.content) + '</div>';
  }).join('');
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
}

/* 思考过程默认收起：内容动辄几千字，展开会把正文顶到屏幕外 */
const expandedReasons = new Set();

function renderReason(m) {
  const open = expandedReasons.has(m.id);
  const label = m.reasoningLive ? '思考中…' : '思考过程';
  return '<div class="reason-wrap">' +
    '<button class="reason-toggle' + (open ? ' open' : '') + '" data-msg="' + escapeHtml(m.id) +
    '" onclick="toggleReason(this)">' + label + '</button>' +
    '<div class="reason"' + (open ? '' : ' hidden') + '>' + escapeHtml(m.reasoning) + '</div></div>';
}

function toggleReason(button) {
  const body = button.nextElementSibling;
  const willOpen = body.hidden;
  body.hidden = !willOpen;
  button.classList.toggle('open', willOpen);
  // 记着展开状态：轮询会整段重建气泡，不记的话每次刷新都会被重新收起
  if (willOpen) expandedReasons.add(button.dataset.msg);
  else expandedReasons.delete(button.dataset.msg);
}

function scrollChat(direction) {
  window.scrollBy({ top: direction * Math.round(window.innerHeight * 0.8), behavior: 'smooth' });
}

/* 极简 Markdown：只处理实际会出现的三种——围栏代码块、行内 code、粗体。
   先把正文转义再做替换，所以插入的标签不会被内容里的尖括号干扰。 */
function renderMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/```[^\n]*\n?([\s\S]*?)```/g, (m, code) =>
    '<pre class="md-pre">' + code.replace(/\s+$/, '') + '</pre>');
  html = html.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>');
  html = html.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  return html;
}

/* 附件地址：App 侧只允许读 AI 工作区内的文件，越界会返回 403 */
function fileUrl(path) {
  return '/api/file?path=' + encodeURIComponent(path);
}

function baseName(path) {
  return String(path == null ? '' : path).split('/').pop();
}

/* 图片给缩略图（点开看大图），文件给可点链接。
   文件名不拼进 onclick（可能带引号），统一用 data 属性读。 */
function renderAttachments(m) {
  const images = m.images || [];
  const files = m.files || [];
  if (!images.length && !files.length) return '';
  let html = '';
  if (images.length) {
    html += '<div class="attach-thumbs">' + images.map((p) =>
      '<img class="attach-thumb" loading="lazy" src="' + fileUrl(p) +
      '" alt="' + escapeHtml(baseName(p)) + '" title="' + escapeHtml(baseName(p)) +
      '" data-name="' + escapeHtml(baseName(p)) + '" onclick="openImage(this)">').join('') + '</div>';
  }
  if (files.length) {
    html += '<div class="attach-row">' + files.map((p) =>
      '<a class="attach-chip" href="' + fileUrl(p) + '" target="_blank" rel="noopener" title="打开 ' +
      escapeHtml(baseName(p)) + '">' + escapeHtml(baseName(p)) + '</a>').join('') + '</div>';
  }
  return html;
}

function openImage(el) {
  $('imageBig').src = el.src;
  $('imageBig').alt = el.dataset.name || '';
  openMask('imageMask');
}

function renderToolCard(t) {
  const cls = t.status === 'ok' ? 'ok' : (t.status === 'running' ? 'run' : 'bad');
  const label = { running: '执行中', ok: '完成', error: '失败', blocked: '已拦截' }[t.status] || t.status;
  const result = t.result
    ? '<div class="tool-result">' + escapeHtml(String(t.result).slice(0, 800)) + '</div>' : '';
  return '<div class="tool-card ' + cls + '">' +
    '<div class="tool-head"><span class="tool-name">' + escapeHtml(t.name) + '</span>' +
    '<span class="tool-state">' + label + '</span></div>' +
    (t.args ? '<div class="tool-args">' + escapeHtml(t.args) + '</div>' : '') + result + '</div>';
}

function scheduleChat() {
  clearTimeout(chatTimer);
  // 不在助手页就不抳：浏览其它页时没必要持续请求
  if ($('view-assistant').hidden) return;
  // 生成中每 500ms 拉一次做「伪流式」，空闲时 5 秒一次；比 SSE 简单得多
  chatTimer = setTimeout(() => loadChat(true), chatStreaming ? 500 : 5000);
}

async function sendChat() {
  const input = $('chatText');
  const text = input.value.trim();
  if (!text && !pendingAttach.length) return;
  const ids = pendingAttach.map((a) => a.id);
  input.value = '';
  pendingAttach = [];
  renderPendingAttach();
  try {
    await post('/api/chat/send', { text: text, attachments: ids });
    await loadChat(true);
  } catch (error) {
    toast(error.message);
  }
}

/* ---------- 附件 ---------- */

let pendingAttach = [];

function renderPendingAttach() {
  $('chatAttachList').innerHTML = pendingAttach.map((a, i) =>
    '<span class="send-chip">' + escapeHtml(a.fileName) +
    '<span onclick="dropAttach(' + i + ')">×</span></span>').join('');
}

function dropAttach(index) {
  const item = pendingAttach[index];
  if (item) {
    api('/api/chat/attach/' + item.id, { method: 'DELETE' }).catch(() => {});
  }
  pendingAttach.splice(index, 1);
  renderPendingAttach();
}

$('chatAttachBtn').addEventListener('click', () => $('chatFile').click());

$('chatFile').addEventListener('change', async (event) => {
  const files = Array.from(event.target.files || []);
  event.target.value = '';
  for (const file of files) {
    try {
      const res = await api('/api/chat/attach?name=' + encodeURIComponent(file.name), {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      });
      pendingAttach.push({ id: res.id, fileName: res.fileName, isImage: res.isImage });
      renderPendingAttach();
    } catch (error) {
      toast(error.message);
    }
  }
});

$('chatSend').addEventListener('click', () => {
  if (chatStreaming) {
    post('/api/chat/abort', null).then(() => loadChat(true)).catch((e) => toast(e.message));
  } else {
    sendChat();
  }
});

$('chatText').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendChat();
  }
});

$('chatNewBtn').addEventListener('click', async () => {
  const button = $('chatNewBtn');
  button.classList.add('btn-loading');
  button.innerHTML = '<span class="spin"></span>新建中';
  try {
    // 同步接口：直接等它跑完，不再等 1.5 秒一次的 job 轮询
    await post('/api/chat/conversation/new', null);
    chatMessages = [];
    renderChat([]);
    await loadChat(true);
  } catch (error) {
    toast(error.message);
  } finally {
    button.classList.remove('btn-loading');
    button.textContent = '新会话';
  }
});

$('chatHistoryBtn').addEventListener('click', () => {
  $('convList').innerHTML = chatConversations.length
    ? chatConversations.map((c) => {
      const current = c.id === chatCurrentId;
      return '<div class="cell' + (current ? ' card-active' : '') + '" onclick="selectConversation(' + c.id + ')">' +
        '<div class="cell-main"><div class="cell-title">' + escapeHtml(c.title || '未命名会话') + '</div>' +
        '<div class="cell-sub">' + (c.messageCount || 0) + ' 条消息</div></div>' +
        (current ? '<span class="tag tag-success">当前</span>' : '') +
        '</div>';
    }).join('')
    : '<div class="cell"><div class="cell-main"><div class="cell-sub">还没有历史会话</div></div></div>';
  openMask('convMask');
});

async function selectConversation(id) {
  closeMask('convMask');
  try {
    await post('/api/chat/conversation/select', { id: id });
    chatMessages = [];
    renderChat([]);
    await loadChat(true);
  } catch (error) {
    toast(error.message);
  }
}

/* ---------- 站点日志 ---------- */

let logSource = 'site';

async function showDetectLog() {
  logSource = 'detect';
  openMask('logMask');
  $('logTitle').textContent = '组件探测 · 完整输出';
  const lines = (lastComponentsData && lastComponentsData.detectLog) || [];
  $('logText').textContent = lines.length
    ? lines.join('\n')
    : '还没有探测输出——可能探测从未跑完（或容器尚未安装）。';
}

async function reloadLog() {
  if (logSource === 'detect') {
    // 重新拉一次组件列表（探测输出就在它的响应里），再显示
    await loadComponents();
    showDetectLog();
  } else {
    showSiteLog(true);
  }
}

async function showSiteLog(keepOpen) {
  logSource = 'site';
  const id = manageTarget;
  if (!keepOpen) closeMask('manageMask');
  openMask('logMask');
  $('logText').textContent = '正在读取…';
  try {
    const res = await api('/api/sites/' + id + '/log');
    $('logTitle').textContent = (res.title || '站点') + ' · 日志';
    $('logText').textContent = res.text || '（没有日志）';
  } catch (error) {
    $('logText').textContent = '读取失败：' + error.message;
  }
}

$('chatModel').addEventListener('change', async (event) => {
  const id = parseInt(event.target.value, 10);
  if (isNaN(id)) return;
  try {
    await post('/api/chat/model', { id: id });
    toast('已切换模型');
  } catch (error) {
    toast(error.message);
  }
});

/* ================= 视图切换 ================= */

const loaded = { sites: false, components: false, plugins: false, assistant: false };

async function showView(name) {
  document.querySelectorAll('.view').forEach((section) => {
    section.hidden = section.id !== 'view-' + name;
  });
  document.querySelectorAll('#nav a, #tabbar .nav-item').forEach((el) => {
    el.classList.toggle('on', el.dataset.view === name);
    el.classList.toggle('active', el.dataset.view === name);
  });
  $('fab').style.display = (name === 'sites' && window.matchMedia('(max-width:768px)').matches) ? 'flex' : 'none';
  $('chatNav').classList.toggle('on', name === 'assistant');

  // 按需加载：进哪个页才拉哪份数据，首屏不被拖慢
  if (!loaded[name]) {
    loaded[name] = true;
    if (name === 'sites') await loadSites();
    if (name === 'components') {
      compEnteredAt = Date.now();
      await loadComponents();
      // 手里一点数据都没有时才主动探测；有数据就读现成的。
      // 每进一次页面就探测会 cancel 掉上一个探测，反而把 loading 卡住。
      if (!components.some((c) => c.installed.length)) refreshComponents();
    }
    if (name === 'plugins') {
      await loadPlugins();
      submitJob('/api/plugins/refresh', null, '刷新插件状态', 'pluginJobBar', loadPlugins);
    }
    if (name === 'assistant') {
      await loadChat(true);
      submitJob('/api/chat/sync', null, '加载 AI 配置', 'chatJobBar', () => loadChat(true));
    }
  }

  // 切回助手页时把轮询接上（之前可能因为离开而停了）
  if (name === 'assistant') scheduleChat();
}

document.querySelectorAll('#nav a, #tabbar .nav-item').forEach((el) => {
  el.addEventListener('click', () => showView(el.dataset.view));
});

/* ---------------- 皮肤 ---------------- */

$('skinBtn').addEventListener('click', (event) => {
  event.stopPropagation();
  $('skinMenu').classList.toggle('on');
});
document.addEventListener('click', () => $('skinMenu').classList.remove('on'));

/* ---------------- 启动 ---------------- */

(async function init() {
  $('appVersion').textContent = '网页控制台 v' + WEB_CONSOLE_VERSION;
  syncFormVisibility();
  const alive = await loadHealth();
  if (!alive) {
    setTimeout(() => location.reload(), 5000);
    return;
  }
  await showView('sites');
  setInterval(loadHealth, 20000);
})();
