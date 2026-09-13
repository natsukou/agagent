import { markets } from '../data/market-catalog.js';

export function renderSidebar(selectedMarket, currentView = 'strategy') {
  const options = [['all', '全国农业期货', 3], ...Object.values(markets).map((market) => [market.code, market.name, market.contracts.length])];
  return `
    <aside class="sidebar">
      <div class="brand"><strong>农期图谱</strong><span>Agri futures intelligence</span></div>
      <p class="section-label">信息工作台</p>
      <nav class="view-nav"><button class="filter ${currentView === 'strategy' ? 'active' : ''}" data-view="strategy">信息与收益</button><button class="filter ${currentView === 'market' ? 'active' : ''}" data-view="market">市场分布图</button></nav>
      <div class="market-filters ${currentView === 'market' ? '' : 'is-hidden'}"><p class="section-label">交易所筛选</p><nav>${options.map(([key, name, count]) => `<button class="filter ${selectedMarket === key ? 'active' : ''}" data-market="${key}">${name}<i>${count}</i></button>`).join('')}</nav></div>
      <div class="source-status"><b>● DCE 数据已接入</b><span>行情样例：2024-07-15 — 07-16<br>其余交易所等待文件接入</span></div>
    </aside>`;
}
