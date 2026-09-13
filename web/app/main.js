import { renderApp, bindEvents } from './app.js';
import { state, subscribe, updateState as setState } from './state.js';

function updateState(patchOrFactory) {
  setState(typeof patchOrFactory === 'function' ? patchOrFactory(state) : patchOrFactory);
}

subscribe((nextState) => {
  renderApp(nextState);
  bindEvents(updateState);
});

renderApp(state);
bindEvents(updateState);
