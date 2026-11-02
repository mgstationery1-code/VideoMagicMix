'use strict';

const ACTIONS = new Set(['state', 'minimize', 'toggle-maximize', 'close']);

function windowState(window) {
  return { maximized: window.isMaximized(), focused: window.isFocused() };
}

function registerWindowControls(ipcMain, getWindow, getOrigin) {
  ipcMain.handle('window-action', (event, action) => {
    const window = getWindow();
    if (!window || window.isDestroyed()) throw new Error('窗口已关闭');
    // Only the local app's top-level renderer may control its own window.
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame
        || new URL(event.senderFrame.url).origin !== getOrigin()) {
      throw new Error('不允许此页面控制窗口');
    }
    if (!ACTIONS.has(action)) throw new Error('不支持的窗口操作');
    if (action === 'close') { window.close(); return null; }
    if (action === 'minimize') window.minimize();
    if (action === 'toggle-maximize') {
      if (window.isMaximized()) window.unmaximize();
      else window.maximize();
    }
    return windowState(window);
  });
}

function observeWindowState(window) {
  const notify = () => {
    if (!window.isDestroyed()) window.webContents.send('window-state', windowState(window));
  };
  for (const event of ['maximize', 'unmaximize', 'focus', 'blur']) window.on(event, notify);
}

module.exports = { registerWindowControls, observeWindowState };
