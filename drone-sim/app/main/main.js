const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const yaml = require('js-yaml');

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 1200,
    minHeight: 800,
    backgroundColor: '#0a0e17',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (process.env.NODE_ENV === 'development' || process.argv.includes('--dev')) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'renderer', 'index.html'));
  }
}

// --- IPC Handlers ---

const CONFIG_DIR = path.join(__dirname, '..', '..', 'config');

ipcMain.handle('load-airframe', async (_event, filename) => {
  const filePath = path.join(CONFIG_DIR, 'airframes', filename);
  const content = fs.readFileSync(filePath, 'utf-8');
  return yaml.load(content);
});

ipcMain.handle('save-airframe', async (_event, filename, data) => {
  const filePath = path.join(CONFIG_DIR, 'airframes', filename);
  const content = yaml.dump(data, { lineWidth: -1, noRefs: true });
  fs.writeFileSync(filePath, content);
  return true;
});

ipcMain.handle('list-airframes', async () => {
  const dir = path.join(CONFIG_DIR, 'airframes');
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter(f => f.endsWith('.yaml') || f.endsWith('.yml'));
});

ipcMain.handle('load-sensor-config', async (_event, filename) => {
  const filePath = path.join(CONFIG_DIR, 'sensors', filename);
  const content = fs.readFileSync(filePath, 'utf-8');
  return yaml.load(content);
});

ipcMain.handle('list-sensor-configs', async () => {
  const dir = path.join(CONFIG_DIR, 'sensors');
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter(f => f.endsWith('.yaml') || f.endsWith('.yml'));
});

ipcMain.handle('load-mission', async (_event, filename) => {
  const filePath = path.join(CONFIG_DIR, 'missions', filename);
  const content = fs.readFileSync(filePath, 'utf-8');
  return yaml.load(content);
});

ipcMain.handle('list-missions', async () => {
  const dir = path.join(CONFIG_DIR, 'missions');
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter(f => f.endsWith('.yaml') || f.endsWith('.yml'));
});

ipcMain.handle('export-airframe', async (_event, data) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    filters: [{ name: 'YAML', extensions: ['yaml', 'yml'] }],
    defaultPath: `${data.name || 'airframe'}.yaml`,
  });
  if (!result.canceled && result.filePath) {
    const content = yaml.dump(data, { lineWidth: -1, noRefs: true });
    fs.writeFileSync(result.filePath, content);
    return result.filePath;
  }
  return null;
});

ipcMain.handle('import-airframe', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    filters: [{ name: 'YAML', extensions: ['yaml', 'yml'] }],
    properties: ['openFile'],
  });
  if (!result.canceled && result.filePaths.length > 0) {
    const content = fs.readFileSync(result.filePaths[0], 'utf-8');
    return yaml.load(content);
  }
  return null;
});

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
