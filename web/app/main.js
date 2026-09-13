import { renderApp, bindEvents } from './app.js?v=20260913-google-v1';
import { state, subscribe, updateState as setState } from './state.js';
import { loadBundledKml } from './data/kml-reader.js?v=20260913-kml-v2';
import { mountAssistantWidget } from './components/assistant-widget.js?v=20260913-minimize-v1';

function updateState(patchOrFactory) {
  setState(typeof patchOrFactory === 'function' ? patchOrFactory(state) : patchOrFactory);
}

subscribe((nextState) => {
  renderApp(nextState);
  bindEvents(updateState);
});

renderApp(state);
bindEvents(updateState);
mountAssistantWidget(() => state);

setState({ kmlStatus: 'loading' });
loadBundledKml()
  .then((kmlData) => setState({ kmlData, kmlStatus: 'ready' }))
  .catch((error) => setState({ kmlStatus: 'error', kmlData: { error: error.message } }));
