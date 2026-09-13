import { cropProfiles, markets, regions } from '../data/market-catalog.js';

function chart(values) {
  if (!values) return '<div class="empty-chart">尚无可展示的行情文件</div>';
  const y = (value) => 86 - (value - 2370) * 1.65;
  return `<svg class="mini-chart" viewBox="0 0 270 110" role="img" aria-label="DCE 玉米结算价走势">
    <line x1="24" y1="86" x2="246" y2="86" class="chart-axis"/>
    <path d="M24 ${y(values[0])} L246 ${y(values[1])}" class="chart-line"/>
    ${values.map((value, index) => `<circle cx="${index ? 246 : 24}" cy="${y(value)}" r="5" class="chart-point"/><text x="${index ? 246 : 24}" y="${y(value) - 10}" text-anchor="middle">${value}</text>`).join('')}
  </svg>`;
}

function renderCropDrawer(crop) {
  const cropRegions = crop.regionIds.map((id) => regions.find((region) => region.id === id));
  return `<aside class="drawer crop-drawer panel">
    <div class="market-code">${crop.code} · CROP PROFILE</div>
    <div class="crop-title"><span>${crop.icon}</span><div><h2>${crop.name}</h2><small>${crop.countries.join(' · ')}</small></div></div>
    <span class="tag">${crop.layouts} 套作物布局 · ${crop.signals} 个市场信号</span>
    <p class="muted intro">${crop.note}</p>
    <dl class="market-details">
      <div><dt>研究链路</dt><dd>${crop.focus}</dd></div>
      <div><dt>关键季节</dt><dd>${crop.season}</dd></div>
      <div><dt>覆盖范围</dt><dd>${crop.regionIds.length} 个锚点 / ${crop.countries.length} 国</dd></div>
    </dl>
    <h3>相关市场</h3>
    <div class="contracts crop-contracts">${crop.contracts.map((contract) => `<span>${contract}</span>`).join('')}</div>
    <h3 class="region-list-title">代表性产区</h3>
    <div class="region-list">${cropRegions.map((region, index) => `<div><i>0${index + 1}</i><span><b>${region.name}</b><small>${region.country} · ${region.crop}</small></span></div>`).join('')}</div>
  </aside>`;
}

export function renderMarketDrawer(state) {
  const crop = cropProfiles[state.selectedCrop];
  if (crop) return renderCropDrawer(crop);
  const selectedMarket = state.selectedMarket;
  const market = selectedMarket === 'all' ? null : markets[selectedMarket];
  const contracts = market ? market.contracts : ['DCE：10 个', 'CZCE：14 个', 'SHFE：1 个', 'INE：1 个'];
  return `
    <aside class="drawer panel">
      <div class="market-code">${market ? market.code : 'MARKET OVERVIEW'}</div>
      <h2>${market ? market.name : '农业期货总览'}</h2>
      <span class="tag ${market && !market.ready ? 'waiting' : ''}">${market ? (market.ready ? '网页行情样例' : '合约目录已纳入') : '26 个国内合约 · 13 个国际参照'}</span>
      <p class="muted intro">${market ? market.intro : '期货价格信号与农业产量图谱分层处理，不能直接推论价格预测能力。'}</p>
      <dl class="market-details">
        <div><dt>所在地</dt><dd>${market ? market.city : '大连 / 郑州 / 上海'}</dd></div>
        <div><dt>地理范围</dt><dd>${market ? `${market.regionIds.length || 0} 个关联产区锚点` : '中国 / 美国 / 巴西 10 个锚点'}</dd></div>
        <div><dt>数据口径</dt><dd>${market ? market.source : '目录 + 国际市场参照'}</dd></div>
      </dl>
      <h3>合约目录</h3>
      <div class="contracts">${contracts.map((contract) => `<span>${contract}</span>`).join('')}</div>
      ${chart(market ? market.price : markets.DCE.price)}
      <p class="muted">${market && !market.ready ? '该页仅展示目录与作物关联，不把它显示成实时行情。' : 'DCE 玉米 c2409：结算价 2,386 → 2,400 元/吨（两日样例）'}</p>
    </aside>`;
}
