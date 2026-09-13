export const state = {
  view: 'market',
  selectedMarket: 'all',
  selectedCrop: 'all',
  selectedPersona: 'beginner',
  showRoutes: true,
  motion: true,
  stressMode: false,
  kmlEnabled: false,
  kmlStatus: 'idle',
  kmlData: null,
  mapMode: 'vector',
  mapRegion: 'dongbei',
};

const listeners = new Set();

export function updateState(patch) {
  Object.assign(state, patch);
  listeners.forEach((listener) => listener(state));
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
