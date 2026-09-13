import { renderSidebar } from './components/sidebar.js';
import { renderMapView } from './components/map-view.js';
import { renderMarketDrawer } from './components/market-drawer.js';
import { renderStatusPanels } from './components/status-panels.js';
import { renderTradingImpact } from './components/trading-impact.js';
import { markets } from './data/market-catalog.js';

export function renderApp(state) {
  const app = document.querySelector('#app');
  const marketPage = `<header class="topbar"><div><p class="breadcrumb">市场总览 / 农业品种 / 全国分布</p><h1>全国农业期货分布图</h1></div><div class="toolbar"><button id="route-toggle" class="tool ${state.showRoutes ? 'on' : ''}">${state.showRoutes ? '产区关联' : '显示关联'}</button><button id="motion-toggle" class="tool">${state.motion ? '关闭动态' : '开启动态'}</button></div></header><section class="metric-grid"><div><span>已纳入交易所</span><b>3</b></div><div><span>农业期货目录</span><b>23</b></div><div><span>已接入行情品种</span><b class="good">2</b></div><div><span>主产区锚点</span><b>4</b></div></section><section class="workspace">${renderMapView(state)}${renderMarketDrawer(state.selectedMarket)}</section>${renderStatusPanels()}`;
  app.innerHTML = `<div class="app-shell">${renderSidebar(state.selectedMarket, state.view)}<main class="main">${state.view === 'market' ? marketPage : renderTradingImpact(state.selectedPersona)}</main></div>`;
}

export function bindEvents(updateState) {
  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => updateState({ view: button.dataset.view })));
  document.querySelectorAll('[data-persona]').forEach((button) => button.addEventListener('click', () => updateState({ selectedPersona: button.dataset.persona })));
  document.querySelectorAll('[data-market]').forEach((button) => button.addEventListener('click', () => updateState({ selectedMarket: button.dataset.market, stressMode: false })));
  document.querySelectorAll('.map-node.market').forEach((node) => node.addEventListener('click', () => updateState({ selectedMarket: node.dataset.market, stressMode: false })));
  document.querySelectorAll('.map-node.region').forEach((node) => node.addEventListener('click', () => updateState({ selectedMarket: 'all', stressMode: false })));
  document.querySelector('#route-toggle')?.addEventListener('click', () => updateState((state) => ({ showRoutes: !state.showRoutes })));
  document.querySelector('#motion-toggle')?.addEventListener('click', () => updateState((state) => ({ motion: !state.motion })));
  document.querySelector('#stress-mode')?.addEventListener('click', () => updateState({ selectedMarket: 'DCE', stressMode: true }));
}
