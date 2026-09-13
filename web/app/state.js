export const state = { selectedMarket: 'all', showRoutes: true, motion: true, stressMode: false };

const listeners = new Set();

export function updateState(patch) {
  Object.assign(state, patch);
  listeners.forEach((listener) => listener(state));
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
