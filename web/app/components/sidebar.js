import { markets } from '../data/market-catalog.js';

export function renderSidebar(selectedMarket, currentView = 'market') {
  const options = [['all', '农业期货总览', 26], ...Object.values(markets).map((market) => [market.code, market.name, market.contracts.length])];
  return `
    <aside class="sidebar">
      <div class="brand"><span class="brand-symbol">✦</span><div class="brand-copy"><strong>绿星图光</strong><em>GREENSTAR ATLAS</em><small>农业产融智能专家</small></div></div>
      <p class="section-label">信息工作台</p>
      <nav class="view-nav"><button class="filter ${currentView === 'market' ? 'active' : ''}" data-view="market">市场与产区</button><button class="filter ${currentView === 'strategy' ? 'active' : ''}" data-view="strategy">信息与收益</button></nav>
      <div class="market-filters ${currentView === 'market' ? '' : 'is-hidden'}"><p class="section-label">交易所筛选</p><nav>${options.map(([key, name, count]) => `<button class="filter ${selectedMarket === key ? 'active' : ''}" data-market="${key}">${name}<i>${count}</i></button>`).join('')}</nav></div>
      <div class="source-status"><b>● 数据范围已标注</b><span>中 / 美 / 巴 10 个产区锚点<br>26 个国内合约 · 13 个国际参照</span></div>
      <a class="brand-contact" href="mailto:hi.caramelux@outlook.com"><span>CONTACT</span><b>hi.caramelux@outlook.com</b></a>
    </aside>`;
}
