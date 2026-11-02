const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const { spawn, execFile } = require('child_process');
const path = require('path');
const http = require('http');
const net = require('net');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');
const { autoUpdater } = require('electron-updater');
const { registerWindowControls, observeWindowState } = require('./window-controls');

// This side-by-side local build must not share uploads or browser cache with the old app.
// Preserve the explicit isolated profile used by smoke tests.
if (!app.commandLine.hasSwitch('user-data-dir')) {
  app.setPath('userData', path.join(app.getPath('appData'), 'VideoMagicMix-balanced'));
}

let backend = null;
let mainWindow = null;
let backendError = '';
let backendPort = null;
let updateOperation = false;
let updateDownloaded = false;
let downloadedVersion = '';
let downloadedReleaseNotes = '';
let installingUpdate = false;
let rendering = false;
let updateLockHeld = false;

autoUpdater.autoDownload = false;
autoUpdater.autoInstallOnAppQuit = false;
autoUpdater.allowPrerelease = false;
autoUpdater.allowDowngrade = false;
autoUpdater.logger = {
  info: message => startupLog(`updater: ${message}`),
  warn: message => startupLog(`updater warning: ${message}`),
  error: message => startupLog(`updater error: ${message}`),
};

function updateStatus(status) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('update-status', status);
}

function requestUpdateLock(action) {
  return new Promise((resolve, reject) => {
    if (!backendPort) return reject(new Error('本地后端尚未启动'));
    const body = new URLSearchParams({ action, token: process.env.VMM_INSTANCE_TOKEN || '' }).toString();
    const request = http.request({ hostname: '127.0.0.1', port: backendPort, path: '/api/runtime/update-lock', method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'Content-Length': Buffer.byteLength(body) } }, response => {
      let payload = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { payload += chunk; });
      response.on('end', () => {
        let result = {};
        try { result = JSON.parse(payload); } catch (_) {}
        if (response.statusCode >= 200 && response.statusCode < 300) return resolve(result);
        reject(new Error(result.detail || `更新保护检查失败（HTTP ${response.statusCode}）`));
      });
    });
    request.setTimeout(5000, () => request.destroy(new Error('更新保护检查超时')));
    request.on('error', reject);
    request.end(body);
  });
}

function releaseUpdateLock() {
  if (!updateLockHeld) return Promise.resolve();
  updateLockHeld = false;
  return requestUpdateLock('release').catch(error => startupLog(`update lock release failed: ${error.message}`));
}

function releaseNotesText(notes) {
  const entries = Array.isArray(notes) ? notes.map(item => typeof item === 'string' ? item : item?.note || '') : [notes || ''];
  return entries.join('\n').replace(/<[^>]*>/g, '').trim().slice(0, 3500);
}

async function promptInstallUpdate(version, notes = '') {
  const result = await dialog.showMessageBox(mainWindow, {
    type: 'info',
    title: '更新已下载',
    message: `VideoMagicMix ${version} 已下载，是否现在退出并安装？`,
    detail: `${notes ? `${notes}\n\n` : ''}安装会关闭软件。当前尚未保存到磁盘的项目状态会丢失，请确认渲染和识别任务已结束。`,
    buttons: ['立即重启安装', '稍后'],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });
  if (result.response === 0) {
    installingUpdate = true;
    updateStatus({ text: `正在安装 ${version}...`, busy: true });
    autoUpdater.quitAndInstall(false, true);
  } else {
    await releaseUpdateLock();
    updateStatus({ text: `已下载 ${version}，可随时安装`, busy: false, canInstall: true });
  }
}

async function checkForUpdates() {
  if (!app.isPackaged) {
    await dialog.showMessageBox(mainWindow, { type: 'info', message: '开发运行模式不检查更新，请使用已安装的桌面版。', buttons: ['确定'] });
    return;
  }
  if (rendering) throw new Error('当前正在渲染，请等待渲染完成后再检查更新');
  if (updateOperation) throw new Error('已有更新操作正在进行');
  if (updateDownloaded) return promptInstallUpdate(downloadedVersion || app.getVersion(), downloadedReleaseNotes);

  updateOperation = true;
  updateStatus({ text: '正在检查 GitHub 正式版...', busy: true });
  let version = '';
  try {
    const result = await autoUpdater.checkForUpdates();
    if (!result?.isUpdateAvailable) {
      updateStatus({ text: '已是最新版本', busy: false });
      await dialog.showMessageBox(mainWindow, { type: 'info', title: '检查更新', message: `当前已是最新正式版（${app.getVersion()}）`, detail: '更新仅来自 mgstationery1-code/VideoMagicMix 的 GitHub 正式发行版。', buttons: ['确定'], noLink: true });
      return;
    }
    version = result.updateInfo.version;
    const notes = releaseNotesText(result.updateInfo.releaseNotes);
    const confirm = await dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '发现新版本',
      message: `发现 VideoMagicMix ${version}（当前 ${app.getVersion()}）`,
      detail: `${notes ? `${notes}\n\n` : ''}更新包将从指定的公开仓库下载。下载期间不能开始渲染或配音识别；安装时软件将退出并重启。`,
      buttons: ['下载并安装', '稍后'],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (confirm.response !== 0) {
      updateStatus({ text: '已取消更新', busy: false });
      return;
    }
    if (rendering) throw new Error('当前正在渲染，请等待渲染完成后再更新');
    await requestUpdateLock('acquire');
    updateLockHeld = true;
    updateStatus({ text: `正在下载 ${version}...`, busy: true, percent: 0 });
    await autoUpdater.downloadUpdate();
    updateDownloaded = true;
    downloadedVersion = version;
    downloadedReleaseNotes = notes;
    updateStatus({ text: `已下载 ${version}`, busy: false, canInstall: true });
    await promptInstallUpdate(version, notes);
  } catch (error) {
    await releaseUpdateLock();
    updateStatus({ text: '更新失败', busy: false });
    await dialog.showMessageBox(mainWindow, { type: 'error', title: '更新失败', message: error.message || String(error), detail: '请检查网络后重试；当前已安装版本不会被卸载。', buttons: ['确定'], noLink: true });
  } finally {
    updateOperation = false;
  }
}

autoUpdater.on('download-progress', progress => {
  const percent = Math.max(0, Math.min(100, Math.round(progress.percent || 0)));
  updateStatus({ text: `正在下载更新 ${percent}%`, busy: true, percent });
});
autoUpdater.on('error', error => startupLog(`updater event error: ${error.stack || error}`));

const startupLogPath = path.join(os.tmpdir(), 'VideoMagicMix-original-startup.log');
function startupLog(message) {
  try {
    fs.appendFileSync(startupLogPath, `${new Date().toISOString()} ${message}\n`, 'utf8');
  } catch (_) {
    // Logging must never prevent the desktop app from starting.
  }
}

// The UI does not require GPU acceleration. Disabling it avoids Electron
// renderer crashes on machines with an incompatible graphics DLL/driver.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-gpu-compositing');

function projectRoot() {
  return app.isPackaged ? path.join(process.resourcesPath, 'app-runtime') : path.resolve(__dirname, '..');
}
function backendCommand() {
  const root = projectRoot();
  if (app.isPackaged) {
    const executable = path.join(root, 'videomagicmix-backend.exe');
    if (!fs.existsSync(executable)) throw new Error(`安装不完整，缺少独立后端：${executable}`);
    return { command: executable, args: [], cwd: root };
  }
  const python = path.join(root, '.venv', 'Scripts', 'python.exe');
  return { command: python, args: [path.join(root, 'run_backend.py')], cwd: root };
}
function findAvailablePort(start = 8787, end = 8887) {
  return new Promise((resolve, reject) => {
    const tryPort = port => {
      if (port > end) return reject(new Error('找不到可用的本地后端端口（8787-8887）'));
      const server = net.createServer();
      server.unref();
      server.once('error', () => tryPort(port + 1));
      server.listen({ host: '127.0.0.1', port, exclusive: true }, () => {
        server.close(() => resolve(port));
      });
    };
    tryPort(start);
  });
}
function startBackend(port, token) {
  const config = backendCommand();
  const dataDirectory = path.join(app.getPath('userData'), 'data');
  const environment = {
    ...process.env,
    VMM_DATA_DIR: dataDirectory,
    VMM_PORT: String(port),
    VMM_INSTANCE_TOKEN: token,
    WHISPER_DEVICE: process.env.WHISPER_DEVICE || 'cpu',
    VMM_RESOURCE_DIR: projectRoot(),
  };
  if (app.isPackaged) {
    environment.WHISPER_MODEL_PATH = path.join(projectRoot(), 'data', 'models', 'faster-whisper-small');
    environment.HF_HUB_OFFLINE = '1';
  }
  // Electron may set this variable for its own child processes. Python must
  // start as Python, not as an embedded Electron Node process.
  delete environment.ELECTRON_RUN_AS_NODE;
  backend = spawn(config.command, config.args, {
    cwd: config.cwd,
    windowsHide: true,
    detached: true,
    stdio: ['ignore', 'ignore', 'pipe'],
    env: environment,
  });
  startupLog(`backend spawned pid=${backend.pid} port=${port} command=${config.command}`);
  backend.stderr.on('data', chunk => {
    backendError = `${backendError}${chunk}`.slice(-4000);
    startupLog(`backend: ${String(chunk).trim()}`);
  });
  backend.on('error', error => {
    backendError = `${backendError}\n${error.stack || error}`.slice(-4000);
    startupLog(`backend error ${error.stack || error}`);
  });
  backend.on('exit', (code, signal) => startupLog(`backend exited code=${code} signal=${signal}`));
}
function waitForBackend(port, token, timeout = 30000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const check = () => {
      const request = http.get(`http://127.0.0.1:${port}/api/health`, response => {
        let body = '';
        response.setEncoding('utf8');
        response.on('data', chunk => { body += chunk; });
        response.on('end', () => {
          try {
            const health = JSON.parse(body);
            if (response.statusCode === 200 && health.instance_token === token) return resolve();
          } catch (_) {
            // The backend may still be writing its response while starting.
          }
          retry();
        });
      });
      request.on('error', retry);
      request.setTimeout(2000, () => request.destroy(new Error('后端健康检查超时')));
    };
    const retry = () => {
      if (Date.now() - started > timeout) {
        const detail = backendError.trim();
        return reject(new Error(`VideoMagicMix 后端启动超时${detail ? `\n${detail}` : ''}`));
      }
      setTimeout(check, 250);
    };
    check();
  });
}
async function createWindow() {
  startupLog('app ready; finding backend port');
  const port = await findAvailablePort();
  backendPort = port;
  const token = crypto.randomUUID();
  startBackend(port, token);
  await waitForBackend(port, token);
  startupLog(`backend ready port=${port}; creating window`);
  const smokeTest = process.argv.includes('--smoke-test');
  mainWindow = new BrowserWindow({ show: !smokeTest, width: 1440, height: 980, minWidth: 1100, minHeight: 720, frame: false, backgroundColor: '#eaf0f5', webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false } });
  observeWindowState(mainWindow);
  mainWindow.on('closed', () => { mainWindow = null; });
  // Previous unpacked builds share Electron's userData cache. Clear it so a
  // newly packaged UI cannot be replaced by a cached index.html/app.js.
  await mainWindow.webContents.session.clearCache();
  await mainWindow.loadURL(`http://127.0.0.1:${port}/?desktopVersion=${encodeURIComponent(app.getVersion())}&ui=${Date.now()}`);
  startupLog('window loaded');
  if (smokeTest) {
    const result = await mainWindow.webContents.executeJavaScript(`(async () => {
      const rows = Array.from({length:20}, (_, index) => ({row_key:'Body'+(index+1),group_name:'分组/示例'+(index+1),text:'这是用于试听和组合预览的文案',preview:'这是用于试听和组合预览',enabled:true,start:index*10,end:(index+1)*10}));
      project = {project_id:'desktop-smoke',schemes:[{scheme_id:'scheme-1',display_name:'字幕1',source_column:'C',voice_path:'smoke.wav',segments:rows,preview:rows}],body_mapping:{},body_configs:{'scheme-1':Object.fromEntries(rows.map(row => [row.row_key,{candidate_count:10,actual_candidate_count:10,min_speed:.9,threshold:1.4,target_duration:10,scanned_video_count:8}]))}};
      selectedSchemeId = 'scheme-1';
      document.getElementById('schemeSelect').innerHTML = '<option value="scheme-1">字幕1</option>';
      renderBodyPreview();
      const controls = ['voiceVolume','bgmVolume','hookVolume'].every(id => document.getElementById(id)?.max === '500');
      const fits = [...document.querySelectorAll('[data-body-prerender]')].every(button => button.getBoundingClientRect().right <= document.documentElement.clientWidth);
      const initiallyLocked = document.getElementById('renderSelected').disabled;
      document.getElementById('outputLimit').value = '100';
      const nativeFetch = window.fetch;
      let hold = false, release = null, needsAcceptance = false;
      let quantityRefresh = false, staleRejected = false, preflightMetric = false;
      window.fetch = async (url, options = {}) => {
        if (String(url).endsWith('/settings')) {
          const fields = Object.fromEntries(options.body);
          return new Response(JSON.stringify({...project,...fields,output_limit:Number(fields.output_limit),recent_window:Number(fields.recent_window),max_overlap:fields.max_overlap === '' ? null : Number(fields.max_overlap)/100}));
        }
        if (String(url).endsWith('/preflight')) {
          const count = Number(project.output_limit) || 100;
          const data = {ok:true,requires_acceptance:needsAcceptance,plan_id:'smoke-'+count,failures:[],plan:[],estimated_outputs:100,planned_outputs:count,hook_count:3,scheme_estimates:[],
            overlap:{strategy:'balanced_rotation',recent_window:3,max_pair_overlap:count === 100 ? .5 : .3,upper_limit:.5,pair_count:count*3-6,violating_pairs:needsAcceptance ? 1 : 0,shortage:false}};
          if (hold) await new Promise(resolve => {release = resolve;});
          return new Response(JSON.stringify(data));
        }
        return nativeFetch(url, options);
      };
      let rotationGuard = false;
      try {
        await preflight({keepBodyPreview:true});
        preflightMetric = verifiedPlan?.planned_outputs === 100 && document.getElementById('batchOverlapSummary').textContent.includes('50.0%') && !document.getElementById('renderSelected').disabled;
        const quantity = document.getElementById('outputLimit');
        quantity.value = '30'; quantity.dispatchEvent(new Event('input',{bubbles:true}));
        const editLocked = document.getElementById('renderSelected').disabled;
        await quantity.onchange();
        quantityRefresh = editLocked && verifiedPlan?.planned_outputs === 30 && document.getElementById('batchOverlapSummary').textContent.includes('30.0%');
        hold = true;
        const pending = preflight({keepBodyPreview:true});
        while (!release) await new Promise(resolve => setTimeout(resolve, 10));
        quantity.value = '20'; quantity.dispatchEvent(new Event('input',{bubbles:true}));
        release(); await pending;
        staleRejected = verifiedPlan === null && document.getElementById('renderSelected').disabled;
        hold = false;
        await preflight({keepBodyPreview:true});
        needsAcceptance = true;
        await preflight({keepBodyPreview:true});
        const blocked = document.getElementById('renderSelected').disabled && !document.getElementById('acceptRotation').classList.contains('hidden');
        document.getElementById('viewRotationDetails').click();
        const detailsVisible = !document.querySelector('[data-panel-content="dedupe"]').classList.contains('hidden');
        document.getElementById('acceptRotation').click();
        const accepted = !document.getElementById('renderSelected').disabled && acceptedRotationPlanId === verifiedPlan.plan_id;
        document.getElementById('recentWindow').value = '1';
        document.getElementById('recentWindow').dispatchEvent(new Event('input',{bubbles:true}));
        rotationGuard = blocked && detailsVisible && accepted && acceptedRotationPlanId === null && document.getElementById('renderSelected').disabled;
        needsAcceptance = false;
        await preflight({keepBodyPreview:true});
      } finally {window.fetch = nativeFetch;}
      const silverTheme = getComputedStyle(document.body).backgroundColor === 'rgb(234, 240, 245)' && document.getElementById('theme').textContent === '银蓝夜航';
      const interfaceFont = getComputedStyle(document.documentElement).fontFamily.startsWith('"HarmonyOS Sans SC"')
        && getComputedStyle(document.querySelector('.primary')).fontFamily === getComputedStyle(document.body).fontFamily;
      const visibleBorders = ['.section-box','.outline','.primary'].every(selector =>
        parseFloat(getComputedStyle(document.querySelector(selector)).borderTopWidth) > 0);
      const borderWidths = {
        section: getComputedStyle(document.querySelector('.section-box')).borderTopWidth,
        outline: getComputedStyle(document.querySelector('.outline')).borderTopWidth,
        primary: getComputedStyle(document.querySelector('.primary')).borderTopWidth
      };
      const windowState = await window.videoMagicMix.windowAction('state');
      const windowControls = !document.getElementById('windowControls').hidden && typeof windowState.maximized === 'boolean' && typeof window.videoMagicMix.onWindowState === 'function' && document.querySelectorAll('[data-window-action]').length === 3;
      const dragRegion = document.querySelector('.titlebar-drag-region');
      const topDragStrip = document.querySelector('.window-top-drag-strip');
      const titlebarDrag = Boolean(topDragStrip && getComputedStyle(topDragStrip).getPropertyValue('-webkit-app-region') === 'drag'
        && topDragStrip.getBoundingClientRect().height >= 10
        && dragRegion && getComputedStyle(dragRegion).getPropertyValue('-webkit-app-region') === 'drag'
        && dragRegion.getBoundingClientRect().width >= 24
        && getComputedStyle(document.querySelector('.top-actions')).getPropertyValue('-webkit-app-region') === 'no-drag'
        && getComputedStyle(document.getElementById('windowControls')).getPropertyValue('-webkit-app-region') === 'no-drag');
      const maximizedState = await window.videoMagicMix.windowAction('toggle-maximize');
      const restoredState = await window.videoMagicMix.windowAction('toggle-maximize');
      const maximizeCycle = maximizedState.maximized && !restoredState.maximized;
      let invalidWindowAction = false;
      try { await window.videoMagicMix.windowAction('not-allowed'); } catch (_) { invalidWindowAction = true; }
      const more = document.querySelector('.subtitle-more');
      const collapsed = !more.open;
      more.querySelector('summary').click();
      const expands = more.open;
      more.querySelector('summary').click();
      const subtitleDisclosure = collapsed && expands && !more.open;
      const statusTabs = [...document.querySelectorAll('.status-tabs [data-panel]')].every(button => {
        button.click();
        const visible = [...document.querySelectorAll('[data-panel-content]')].filter(panel => getComputedStyle(panel).display !== 'none');
        return visible.length === 1 && visible[0].dataset.panelContent === button.dataset.panel && button.getAttribute('aria-selected') === 'true';
      });
      setStatusPanel('logs');
      const panel = document.querySelector('.right-column');
      const initialHeight = panel.getBoundingClientRect().height;
      for (let i = 0; i < 150; i++) log('滚动测试 ' + i + '：长日志只在日志面板中滚动，不撑高工作区');
      const logBox = document.getElementById('statusLog');
      logBox.scrollTop = 100;
      const boundedLogs = panel.getBoundingClientRect().height === initialHeight && logBox.scrollHeight > logBox.clientHeight && logBox.scrollTop > 0;
      logBox.replaceChildren();
      document.getElementById('logCount').textContent = '0';
      log('银蓝夜航界面检查：窗口控制、分页、滚动、字幕折叠正常', 'success');
      return JSON.stringify({title:document.title, bridge:typeof window.videoMagicMix?.selectDirectory, update:typeof window.videoMagicMix?.checkForUpdates, updateEvents:typeof window.videoMagicMix?.onUpdateStatus, bodyRoot:Boolean(document.getElementById('selectBodyRoot')), controls, fits,
        silverTheme, interfaceFont, visibleBorders, borderWidths, windowControls, titlebarDrag, maximizeCycle, invalidWindowAction, subtitleDisclosure, statusTabs, boundedLogs,
        initiallyLocked, preflightMetric, quantityRefresh, staleRejected, rotationGuard,
        audioPassages:document.getElementById('audioPreviewSegment').options.length,
        overlap:Boolean(document.getElementById('maxOverlap')),
        bodies:document.querySelectorAll('[data-body-prerender]').length,
        speedInputs:document.querySelectorAll('[data-body-min-speed]').length});
    })()`);
    const state = JSON.parse(result);
    if (!state.silverTheme || !state.interfaceFont || !state.visibleBorders || !state.windowControls || !state.titlebarDrag || !state.maximizeCycle || !state.invalidWindowAction || !state.subtitleDisclosure || !state.statusTabs || !state.boundedLogs) throw new Error('银蓝夜航界面检查失败：' + result);
    if (!state.title.includes('VideoMagicMix') || state.bridge !== 'function' || state.update !== 'function' || state.updateEvents !== 'function' || !state.bodyRoot || !state.controls || !state.fits || !state.overlap || !state.initiallyLocked || !state.preflightMetric || !state.quantityRefresh || !state.staleRejected || !state.rotationGuard || state.bodies !== 20 || state.speedInputs !== 20 || state.audioPassages !== 22) throw new Error(`桌面冒烟测试失败：${result}`);
    if (process.env.VMM_SMOKE_SCREENSHOT) {
      await mainWindow.webContents.executeJavaScript('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      fs.writeFileSync(process.env.VMM_SMOKE_SCREENSHOT, (await mainWindow.webContents.capturePage()).toPNG());
      await mainWindow.webContents.executeJavaScript('document.getElementById("maxOverlap").scrollIntoView({block:"center"}); new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      fs.writeFileSync(process.env.VMM_SMOKE_SCREENSHOT.replace(/\.png$/i, '-params.png'), (await mainWindow.webContents.capturePage()).toPNG());
      // Screenshot fixture only: a shorter list lets the review show every section.
      // The 20-Body stress checks above still exercise the full workspace.
      await mainWindow.webContents.executeJavaScript(`
        project.schemes[0].segments = project.schemes[0].segments.slice(0, 3);
        project.schemes[0].preview = project.schemes[0].preview.slice(0, 3);
        renderSchemes();
        document.body.classList.remove('window-inactive');
        document.getElementById('health').textContent = '界面测试 · 示例数据';
        document.documentElement.scrollTop = 0;
        document.body.scrollTop = 0;
        new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      `);
      fs.writeFileSync(process.env.VMM_SMOKE_SCREENSHOT.replace(/\.png$/i, '-overview.png'), (await mainWindow.webContents.capturePage()).toPNG());
      await mainWindow.webContents.executeJavaScript('document.querySelector(".compact-panel").scrollIntoView({block:"start"}); new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      fs.writeFileSync(process.env.VMM_SMOKE_SCREENSHOT.replace(/\.png$/i, '-settings.png'), (await mainWindow.webContents.capturePage()).toPNG());
    }
    startupLog(`DESKTOP_SMOKE_OK ${result}`);
    app.quit();
  }
}

registerWindowControls(ipcMain, () => mainWindow, () => `http://127.0.0.1:${backendPort}`);

ipcMain.handle('select-directory', async (_event, kind, detail = '') => {
  const title = kind === 'hook' ? '选择 Hook 文件夹' : kind === 'body-root' ? '选择 Body 素材总根目录' : kind === 'body' ? `选择 ${detail || 'Body'} 文件夹` : '选择输出目录';
  const result = await dialog.showOpenDialog({ title, properties: ['openDirectory', 'createDirectory'] });
  return result.canceled ? null : result.filePaths[0];
});
ipcMain.handle('open-directory', async (_event, directory) => {
  if (!directory || !fs.existsSync(directory)) throw new Error('输出目录不存在');
  const error = await shell.openPath(directory);
  if (error) throw new Error(error);
  return true;
});
ipcMain.handle('check-for-updates', checkForUpdates);
ipcMain.handle('install-downloaded-update', async () => {
  if (!updateDownloaded) throw new Error('当前没有已下载的更新');
  if (rendering) throw new Error('当前正在渲染，请等待渲染完成后再安装更新');
  if (updateOperation) throw new Error('已有更新操作正在进行');
  updateOperation = true;
  try {
    if (!updateLockHeld) {
      await requestUpdateLock('acquire');
      updateLockHeld = true;
    }
    await promptInstallUpdate(downloadedVersion || app.getVersion(), downloadedReleaseNotes);
  } finally {
    updateOperation = false;
  }
});
ipcMain.handle('set-rendering', (_event, active) => {
  rendering = Boolean(active);
  return rendering;
});

function stopBackend() {
  if (!backend || backend.killed || !backend.pid) return;
  if (process.platform === 'win32') {
    const taskkill = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe');
    execFile(taskkill, ['/pid', String(backend.pid), '/T', '/F'], { windowsHide: true }, () => {});
  } else {
    backend.kill();
  }
}

app.whenReady().then(createWindow).catch(error => {
  startupLog(`startup failed ${error.stack || error}`);
  stopBackend();
  dialog.showErrorBox('VideoMagicMix 启动失败', error.message);
  app.quit();
});
app.on('window-all-closed', () => { stopBackend(); if (process.platform !== 'darwin') app.quit(); });
app.on('before-quit', stopBackend);
