import { markets, regions } from '../data/market-catalog.js';

const countryOutline = 'M260 75L345 46 414 75 497 67 575 90 665 75 738 120 795 190 766 260 810 318 757 357 727 432 650 472 565 458 522 490 432 460 383 412 318 390 280 337 217 295 236 220 201 169Z';
const provinceHints = 'M309 107L347 160 325 238M414 75L454 166 424 225 476 298M575 90L558 183 610 247 598 315M665 75L667 155 729 199M383 412L445 362 522 390 565 458';

export function renderMapView(state) {
  const visibleMarkets = state.selectedMarket === 'all' ? Object.values(markets) : [markets[state.selectedMarket]];
  const routes = state.showRoutes ? visibleMarkets.flatMap((market) => regions.filter((_, index) => market.code !== 'SHFE' || index === 3).map((region) => `<line class="map-route ${market.ready ? 'ready' : ''}" x1="${market.x}" y1="${market.y}" x2="${region.x}" y2="${region.y}"/>`)).join('') : '';
  const regionNodes = regions.map((region) => `<g class="map-node region" data-region="${region.id}"><circle cx="${region.x}" cy="${region.y}" r="7"/><text x="${region.x + 12}" y="${region.y - 10}">${region.name}</text></g>`).join('');
  const marketNodes = visibleMarkets.map((market) => `<g class="map-node market ${market.ready ? 'ready' : ''} ${state.selectedMarket === market.code ? 'selected' : ''}" data-market="${market.code}"><circle cx="${market.x}" cy="${market.y}" r="7"/>${market.ready ? `<circle class="pulse" cx="${market.x}" cy="${market.y}" r="7"/>` : ''}<text x="${market.x + 12}" y="${market.y - 10}">${market.code}</text></g>`).join('');
  const caption = state.stressMode ? '玉米热旱冲击模拟：华北、东北产区的气候异常会沿产量图谱传播；DCE 玉米仅作为市场信号观察。' : state.selectedMarket === 'all' ? '选择交易所或地图节点，可查看品种目录、行情状态及其关联产区。' : `${markets[state.selectedMarket].name}位于${markets[state.selectedMarket].city}；${markets[state.selectedMarket].ready ? '绿色脉冲表示已接入的行情信号。' : '橙色节点表示合约目录已规划、行情文件尚待接入。'}`;
  return `<article class="map-panel panel"><header class="panel-header"><div><h2>交易所与主产区关系</h2><p>经纬度位置为节点锚点；底图为演示性空间轮廓</p></div><div class="legend"><span class="ready-key">已接行情</span><span class="waiting-key">目录待接</span><span class="region-key">主产区</span></div></header><div class="map-stage ${state.motion ? '' : 'paused'}"><svg viewBox="0 0 860 570" role="img" aria-label="全国农业期货交易所与主产区分布图"><path class="country" d="${countryOutline}"/><path class="province-hints" d="${provinceHints}"/>${routes}${regionNodes}${marketNodes}</svg><p class="map-caption">${caption}</p></div></article>`;
}
