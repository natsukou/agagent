export const state = { view: 'strategy', selectedMarket: 'all', selectedPersona: 'beginner', showRoutes: true, motion: true, stressMode: false };

const listeners = new Set();

export function updateState(patch) {
  Object.assign(state, patch);
  listeners.forEach((listener) => listener(state));
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
