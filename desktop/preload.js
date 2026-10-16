const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('videoMagicMix', {
  selectDirectory: (kind, detail = '') => ipcRenderer.invoke('select-directory', kind, detail)
});
