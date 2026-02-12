const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('api', {
  loadAirframe: (filename) => ipcRenderer.invoke('load-airframe', filename),
  saveAirframe: (filename, data) => ipcRenderer.invoke('save-airframe', filename, data),
  listAirframes: () => ipcRenderer.invoke('list-airframes'),
  loadSensorConfig: (filename) => ipcRenderer.invoke('load-sensor-config', filename),
  listSensorConfigs: () => ipcRenderer.invoke('list-sensor-configs'),
  loadMission: (filename) => ipcRenderer.invoke('load-mission', filename),
  listMissions: () => ipcRenderer.invoke('list-missions'),
  exportAirframe: (data) => ipcRenderer.invoke('export-airframe', data),
  importAirframe: () => ipcRenderer.invoke('import-airframe'),
});
