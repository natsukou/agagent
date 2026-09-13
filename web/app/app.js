import { renderSidebar } from './components/sidebar.js?v=20260913-kml-v2';
import { renderCropSwitcher } from './components/crop-switcher.js?v=20260913-kml-v2';
import { bindMapInteractions, renderMapView } from './components/map-view.js?v=20260913-google-v1';
import { renderMarketDrawer } from './components/market-drawer.js?v=20260913-kml-v2';
import { renderStatusPanels } from './components/status-panels.js?v=20260913-kml-v2';
import { renderTradingImpact } from './components/trading-impact.js';
import { cropProfiles, markets } from './data/market-catalog.js?v=20260913-kml-v2';

function renderMetrics(selectedCrop) {
  const crop = cropProfiles[selectedCrop];
  const metrics = crop
    ? [['覆盖国家', crop.countries.length], ['产区锚点', crop.regionIds.length], ['作物布局', crop.layouts], ['市场信号', crop.signals]]
    : [['覆盖国家', 3], ['产区锚点', 10], ['作物布局', 13], ['市场基准', 39]];
  return `<section class="metric-grid">${metrics.map(([label, value], index) => `<div><span>${label}</span><b class="${index === metrics.length - 1 ? 'good' : ''}">${value}</b></div>`).join('')}</section>`;
}

export function renderApp(state) {
  const app = document.querySelector('#app');
  const kmlCount = state.kmlData?.summary?.overlays || 0;
  const marketPage = `<header class="topbar"><div class="hero-copy"><p class="breadcrumb">GREENSTAR ATLAS · AI INTELLIGENCE FOR AGRICULTURE &amp; MARKETS</p><div class="hero-title"><span>✦</span><h1>绿星图光</h1><em>农业产融智能专家</em></div><p class="product-position">让全球作物产区、气候与市场信号，在一张可解释的数据图谱中被看见。</p></div><div class="toolbar"><button id="kml-toggle" class="tool kml-tool ${state.kmlEnabled ? 'on' : ''}" ${state.kmlStatus !== 'ready' ? 'disabled' : ''}>${state.kmlStatus === 'loading' ? '读取 KML…' : `Google 图层${kmlCount ? ` ${kmlCount}` : ''}`}</button><button id="route-toggle" class="tool ${state.showRoutes ? 'on' : ''}">${state.showRoutes ? '产区关联' : '显示关联'}</button><button id="motion-toggle" class="tool">${state.motion ? '关闭动态' : '开启动态'}</button></div></header>${renderCropSwitcher(state.selectedCrop)}${renderMetrics(state.selectedCrop)}<section class="workspace">${renderMapView(state)}${renderMarketDrawer(state)}</section>${renderStatusPanels(state)}`;
  app.innerHTML = `<div class="app-shell">${renderSidebar(state.selectedMarket, state.view)}<main class="main">${state.view === 'market' ? marketPage : renderTradingImpact(state.selectedPersona)}</main></div>`;
}

export function bindEvents(updateState) {
  bindMapInteractions();
  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => updateState({ view: button.dataset.view })));
  document.querySelectorAll('[data-persona]').forEach((button) => button.addEventListener('click', () => updateState({ selectedPersona: button.dataset.persona })));
  document.querySelectorAll('.crop-tab[data-crop]').forEach((button) => button.addEventListener('click', () => updateState({ selectedCrop: button.dataset.crop, selectedMarket: 'all', stressMode: false })));
  document.querySelectorAll('[data-market]').forEach((button) => button.addEventListener('click', () => updateState({ selectedMarket: button.dataset.market, selectedCrop: 'all', stressMode: false })));
  document.querySelectorAll('.map-node.market').forEach((node) => node.addEventListener('click', () => updateState({ selectedMarket: node.dataset.market, selectedCrop: 'all', stressMode: false })));
  document.querySelectorAll('.map-node.region').forEach((node) => node.addEventListener('click', () => updateState({ selectedMarket: 'all', selectedCrop: node.dataset.crop || 'all', stressMode: false })));
  document.querySelector('#route-toggle')?.addEventListener('click', () => updateState((state) => ({ showRoutes: !state.showRoutes })));
  document.querySelector('#motion-toggle')?.addEventListener('click', () => updateState((state) => ({ motion: !state.motion })));
  document.querySelector('#kml-toggle')?.addEventListener('click', () => updateState((state) => ({ kmlEnabled: !state.kmlEnabled })));
  document.querySelectorAll('[data-map-mode]').forEach((button) => button.addEventListener('click', () => updateState({ mapMode: button.dataset.mapMode })));
  document.querySelectorAll('[data-map-region]').forEach((button) => button.addEventListener('click', () => updateState({ mapRegion: button.dataset.mapRegion })));
  document.querySelector('#stress-mode')?.addEventListener('click', () => updateState({ selectedCrop: 'corn', selectedMarket: 'all', stressMode: true }));
}
