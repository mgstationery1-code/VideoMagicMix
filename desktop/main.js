const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const { spawn, execFile } = require('child_process');
const path = require('path');
const http = require('http');
const net = require('net');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');

let backend = null;
let mainWindow = null;
let backendError = '';

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
  const token = crypto.randomUUID();
  startBackend(port, token);
  await waitForBackend(port, token);
  startupLog(`backend ready port=${port}; creating window`);
  const smokeTest = process.argv.includes('--smoke-test');
  mainWindow = new BrowserWindow({ show: !smokeTest, width: 1440, height: 980, minWidth: 1100, minHeight: 720, backgroundColor: '#080b10', webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false } });
  mainWindow.on('closed', () => { mainWindow = null; });
  await mainWindow.loadURL(`http://127.0.0.1:${port}/`);
  startupLog('window loaded');
  if (smokeTest) {
    const result = await mainWindow.webContents.executeJavaScript('JSON.stringify({title:document.title, bridge:typeof window.videoMagicMix?.selectDirectory})');
    const state = JSON.parse(result);
    if (!state.title.includes('VideoMagicMix') || state.bridge !== 'function') throw new Error(`桌面冒烟测试失败：${result}`);
    startupLog(`DESKTOP_SMOKE_OK ${result}`);
    app.quit();
  }
}

ipcMain.handle('select-directory', async (_event, kind, detail = '') => {
  const title = kind === 'hook' ? '选择 Hook 文件夹' : kind === 'body' ? `选择 ${detail || 'Body'} 文件夹` : '选择输出目录';
  const result = await dialog.showOpenDialog({ title, properties: ['openDirectory', 'createDirectory'] });
  return result.canceled ? null : result.filePaths[0];
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
