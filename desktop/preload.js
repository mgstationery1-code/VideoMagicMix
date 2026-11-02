const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('videoMagicMix', {
  windowAction: action => ipcRenderer.invoke('window-action', action),
  onWindowState: callback => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('window-state', listener);
    return () => ipcRenderer.removeListener('window-state', listener);
  },
  selectDirectory: (kind, detail = '') => ipcRenderer.invoke('select-directory', kind, detail),
  openDirectory: directory => ipcRenderer.invoke('open-directory', directory),
  checkForUpdates: () => ipcRenderer.invoke('check-for-updates'),
  installDownloadedUpdate: () => ipcRenderer.invoke('install-downloaded-update'),
  setRendering: value => ipcRenderer.invoke('set-rendering', Boolean(value)),
  onUpdateStatus: callback => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, status) => callback(status);
    ipcRenderer.on('update-status', listener);
    return () => ipcRenderer.removeListener('update-status', listener);
  }
});
