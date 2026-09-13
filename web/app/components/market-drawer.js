import { markets } from '../data/market-catalog.js';

function chart(values) {
  if (!values) return '<div class="empty-chart">尚无可展示的行情文件</div>';
  const y = (value) => 86 - (value - 2370) * 1.65;
  return `<svg class="mini-chart" viewBox="0 0 270 110" role="img" aria-label="DCE 玉米结算价走势">
    <line x1="24" y1="86" x2="246" y2="86" class="chart-axis"/>
    <path d="M24 ${y(values[0])} L246 ${y(values[1])}" class="chart-line"/>
    ${values.map((value, index) => `<circle cx="${index ? 246 : 24}" cy="${y(value)}" r="5" class="chart-point"/><text x="${index ? 246 : 24}" y="${y(value) - 10}" text-anchor="middle">${value}</text>`).join('')}
  </svg>`;
}

export function renderMarketDrawer(selectedMarket) {
  const market = selectedMarket === 'all' ? null : markets[selectedMarket];
  const contracts = market ? market.contracts : ['DCE：10 个', 'CZCE：12 个', 'SHFE：1 个'];
  return `
    <aside class="drawer panel">
      <div class="market-code">${market ? market.code : 'MARKET OVERVIEW'}</div>
      <h2>${market ? market.name : '全国农业期货'}</h2>
      <span class="tag ${market && !market.ready ? 'waiting' : ''}">${market ? (market.ready ? '行情文件已接入' : '行情文件待接入') : '2 个品种已有样例行情'}</span>
      <p class="muted intro">${market ? market.intro : '期货价格信号与农业产量图谱分层处理。'}</p>
      <dl class="market-details">
        <div><dt>所在地</dt><dd>${market ? market.city : '大连 / 郑州 / 上海'}</dd></div>
        <div><dt>映射主产区</dt><dd>${market ? market.regions : '4 个主产区锚点'}</dd></div>
        <div><dt>当前行情源</dt><dd>${market ? market.source : 'DCE 文件 + 待接来源'}</dd></div>
      </dl>
      <h3>合约目录</h3>
      <div class="contracts">${contracts.map((contract) => `<span>${contract}</span>`).join('')}</div>
      ${chart(market ? market.price : markets.DCE.price)}
      <p class="muted">${market && !market.ready ? '未接入行情文件，暂不绘制价格走势' : 'DCE 玉米 c2409：结算价 2,386 → 2,400 元/吨'}</p>
    </aside>`;
}
