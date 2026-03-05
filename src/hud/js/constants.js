// ---- CONSTANTS ----
export const CENTER_LAT = 33.6846;
export const CENTER_LNG = -117.8265;
export const ORBIT_RADIUS = 0.0025; // degrees roughly 250m
export const TRAIL_LENGTH = 60;
export const SPARKLINE_POINTS = 60;
export const EVENT_INTERVAL_MIN = 3000;
export const EVENT_INTERVAL_MAX = 8000;
export const MAX_EVENTS = 80;

// ---- ISR COLLECTION PATTERN OFFSETS ----
// Assets are positioned relative to the pattern center using {along, cross} offsets.
// ORBIT: evenly spaced around a circle (360/N degrees apart)
// RACETRACK: spread along an elongated oval pattern
// SEARCH_GRID: systematic grid spacing for wide-area coverage
// POINT_STARE: tight convergence on a single target
export const PATTERN_OFFSETS = {
  ORBIT: [
    {along: 0,    cross: 0},
    {along: 1.5,  cross: 1.5},
    {along: -1.5, cross: 1.5},
    {along: -1.5, cross: -1.5},
    {along: 1.5,  cross: -1.5},
    {along: 0,    cross: -2.1}
  ],
  RACETRACK: [
    {along: 0,  cross: 0},
    {along: -2, cross: 0},
    {along: -4, cross: 0},
    {along: 1,  cross: 0.6},
    {along: -1, cross: 0.6},
    {along: -3, cross: 0.6}
  ],
  SEARCH_GRID: [
    {along: 0,  cross: 0},
    {along: 0,  cross: -2},
    {along: 0,  cross: 2},
    {along: -2, cross: 0},
    {along: -2, cross: -2},
    {along: -2, cross: 2}
  ],
  POINT_STARE: [
    {along: 0,    cross: 0},
    {along: -0.3, cross: -0.3},
    {along: -0.3, cross: 0.3},
    {along: 0.3,  cross: -0.3},
    {along: 0.3,  cross: 0.3},
    {along: -0.5, cross: 0}
  ],
};

export const DRONE_STATES = {
  IDLE:'IDLE', ARMED:'ARMED', TAKING_OFF:'TAKING_OFF', FLYING:'FLYING',
  LANDING:'LANDING', LANDED:'LANDED', RTB:'RTB', EMERGENCY:'EMERGENCY',
  GOTO:'GOTO', MISSION:'MISSION'
};

// ---- ASSET DEFINITIONS ----
export const ASSET_DEFS = [
  { id: 'ALPHA-1',   color: '#2D72D2', rgb: '45,114,210',  role: 'PRIMARY',    patternOffset: { along: 0, cross: 0 } },
  { id: 'BRAVO-2',   color: '#238551', rgb: '35,133,81',   role: 'ESCORT',     patternOffset: { along: 1.5, cross: 1.5 } },
  { id: 'CHARLIE-3', color: '#C87619', rgb: '200,118,25',  role: 'ISR',        patternOffset: { along: -1.5, cross: 1.5 } },
  { id: 'DELTA-4',   color: '#CD4246', rgb: '205,66,70',   role: 'ESCORT',     patternOffset: { along: -1.5, cross: -1.5 } },
  { id: 'ECHO-5',    color: '#7961DB', rgb: '121,97,219',  role: 'LOGISTICS',  patternOffset: { along: 1.5, cross: -1.5 } },
  { id: 'FOXTROT-6', color: '#4C90F0', rgb: '76,144,240',  role: 'OVERWATCH',  patternOffset: { along: 0, cross: -2.1 } },
];
