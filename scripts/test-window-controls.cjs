'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { registerWindowControls, observeWindowState } = require('../desktop/window-controls');

function setup() {
  const window = new EventEmitter();
  const calls = [];
  let maximized = false;
  window.isDestroyed = () => false;
  window.isMaximized = () => maximized;
  window.isFocused = () => true;
  window.minimize = () => calls.push('minimize');
  window.maximize = () => { maximized = true; calls.push('maximize'); };
  window.unmaximize = () => { maximized = false; calls.push('restore'); };
  window.close = () => calls.push('close');
  window.webContents = { mainFrame: { url: 'http://127.0.0.1:8787/?desktopVersion=0.1.10' }, send: (...args) => calls.push(args) };
  let handler;
  registerWindowControls({ handle: (name, fn) => { assert.equal(name, 'window-action'); handler = fn; } }, () => window, () => 'http://127.0.0.1:8787');
  const event = { sender: window.webContents, senderFrame: window.webContents.mainFrame };
  return { window, calls, event, handler };
}

test('window actions minimize, maximize, restore and close the correct window', () => {
  const { handler, event, calls } = setup();
  assert.deepEqual(handler(event, 'state'), { maximized: false, focused: true });
  handler(event, 'minimize');
  assert.equal(handler(event, 'toggle-maximize').maximized, true);
  assert.equal(handler(event, 'toggle-maximize').maximized, false);
  assert.equal(handler(event, 'close'), null);
  assert.deepEqual(calls, ['minimize', 'maximize', 'restore', 'close']);
});

test('reject unknown actions, foreign renderers, subframes and remote navigation', () => {
  const { handler, event, calls } = setup();
  assert.throws(() => handler(event, 'execute'), /不支持/);
  assert.throws(() => handler({ ...event, sender: {} }, 'close'), /不允许/);
  assert.throws(() => handler({ ...event, senderFrame: { url: event.senderFrame.url } }, 'close'), /不允许/);
  event.senderFrame.url = 'https://example.com/';
  assert.throws(() => handler(event, 'close'), /不允许/);
  assert.deepEqual(calls, []);
});

test('destroyed windows reject actions and do not receive state events', () => {
  const { window, handler, event, calls } = setup();
  observeWindowState(window);
  window.isDestroyed = () => true;
  assert.throws(() => handler(event, 'close'), /已关闭/);
  window.emit('focus');
  assert.deepEqual(calls, []);
});

test('native maximize, restore and focus events notify the renderer', () => {
  const { window, calls } = setup();
  observeWindowState(window);
  for (const name of ['maximize', 'unmaximize', 'focus', 'blur']) window.emit(name);
  assert.equal(calls.length, 4);
  assert.ok(calls.every(item => item[0] === 'window-state' && item[1].focused));
});

test('preload only exposes named actions and returns event unsubscribe', () => {
  const ipc = new EventEmitter();
  const calls = [];
  ipc.invoke = (...args) => { calls.push(args); return Promise.resolve(); };
  let bridge;
  const contextBridge = { exposeInMainWorld: (name, value) => { assert.equal(name, 'videoMagicMix'); bridge = value; } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../desktop/preload.js'), 'utf8'), {
    require: name => { assert.equal(name, 'electron'); return { contextBridge, ipcRenderer: ipc }; }
  });
  bridge.windowAction('minimize');
  assert.deepEqual(calls[0], ['window-action', 'minimize']);
  let received;
  const unsubscribe = bridge.onWindowState(state => { received = state; });
  ipc.emit('window-state', { private: 'not exposed' }, { maximized: true });
  assert.deepEqual(received, { maximized: true });
  unsubscribe();
  assert.equal(ipc.listenerCount('window-state'), 0);
  assert.equal(typeof bridge.onWindowState(null), 'function');
  assert.equal(bridge.ipcRenderer, undefined);
});

test('browser fallback keeps custom window controls hidden', () => {
  const controls = { hidden: true };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../frontend/window-controls.js'), 'utf8'), {
    window: {}, document: { getElementById: () => controls }
  });
  assert.equal(controls.hidden, true);
});

test('desktop titlebar includes a dedicated draggable gap and excludes interactive controls', () => {
  const root = path.join(__dirname, '../frontend');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(root, 'silver-night.css'), 'utf8');
  assert.match(html, /class="window-top-drag-strip"/);
  assert.match(css, /\.desktop-window \.window-top-drag-strip\s*\{\s*position:\s*fixed;[^}]*-webkit-app-region:\s*drag;/);
  assert.match(html, /class="titlebar-drag-region"/);
  assert.match(css, /\.desktop-window \.titlebar-drag-region\s*\{\s*flex:\s*1 1 auto;[^}]*min-width:\s*24px;/);
  assert.match(css, /\.desktop-window \.titlebar-drag-region\s*\{[^}]*-webkit-app-region:\s*drag;/);
  assert.match(css, /\.desktop-window \.top-actions,\s*\.desktop-window \.top-actions \*,\s*\.desktop-window \.window-controls,\s*\.desktop-window \.window-controls \*\s*\{\s*-webkit-app-region:\s*no-drag;/);
});

test('银蓝夜航 uses HarmonyOS Sans SC and strengthens major outlines only', () => {
  const root = path.join(__dirname, '../frontend');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(root, 'silver-night.css'), 'utf8');
  assert.match(html, /silver-night\.css/);
  assert.match(html, /银蓝夜航/);
  assert.match(css, /font-family:\s*"HarmonyOS Sans SC"/);
  assert.match(css, /border:\s*1\.5px solid var\(--studio-border\)/);
  assert.match(css, /border:\s*1\.25px solid var\(--studio-border-strong\)/);
  assert.match(css, /border:\s*1\.25px solid #405e7a/);
});
