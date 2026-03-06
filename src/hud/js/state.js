// Shared mutable application state
// All modules import from here to read/write shared state
export const state = {
  selectedDroneId: null,
  currentMode: null,
  audioMuted: false,
  armedState: false,
  map: null,
  mapMarkers: {},
  mapTrails: {},
  formationLines: [],
  fovLayers: {},
  activityStream: [],
  assetTimelines: {},
  objectivesCompleted: 0,
  diagState: {},
  insightState: {},
  unreadEventCount: 0,
  inspectorRenderedAsset: null,
  diagRenderedAsset: null,
  hwRenderedAsset: null,
  insightsRenderedAsset: null,
  cameraLock: false,
  _lastTimelineCount: {},
  _prevTelemValues: {},
  assets: [],       // populated during init
  drones: [],       // alias for assets
};
