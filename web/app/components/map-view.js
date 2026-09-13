import { cropProfiles, markets, regions } from '../data/market-catalog.js';
import { coverageCountries } from '../data/coverage-countries.js';

const VIEWBOX = { width: 1000, height: 640 };
const FRAME = { left: 52, right: 948, top: 52, bottom: 568, minLon: -172, maxLon: 142, minLat: -38, maxLat: 73 };
const ZOOM = { min: 1, max: 5, step: 1.35 };
const mapView = { scale: 1, x: 0, y: 0 };
const project = (lon, lat, offsetX = 0, offsetY = 0) => ({
  x: FRAME.left + ((lon - FRAME.minLon) / (FRAME.maxLon - FRAME.minLon)) * (FRAME.right - FRAME.left) + offsetX,
  y: FRAME.bottom - ((lat - FRAME.minLat) / (FRAME.maxLat - FRAME.minLat)) * (FRAME.bottom - FRAME.top) + offsetY,
});
const escapeMarkup = (value = '') => String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const formatCoordinate = (value, positive, negative) => `${Math.abs(value).toFixed(2)}°${value >= 0 ? positive : negative}`;

function grid() {
  const longitude = [-150, -100, -50, 0, 50, 100];
  const latitude = [-20, 0, 20, 40, 60];
  const lines = `${longitude.map((lon) => {
    const { x } = project(lon, 0);
    return `<line class="geo-grid" x1="${x}" y1="${FRAME.top}" x2="${x}" y2="${FRAME.bottom}"/>`;
  }).join('')}${latitude.map((lat) => {
    const { y } = project(0, lat);
    return `<line class="geo-grid" x1="${FRAME.left}" y1="${y}" x2="${FRAME.right}" y2="${y}"/>`;
  }).join('')}`;
  const labels = `${longitude.map((lon) => {
    const { x } = project(lon, 0);
    return `<text class="geo-axis" x="${x}" y="${FRAME.bottom + 24}" text-anchor="middle">${Math.abs(lon)}°${lon < 0 ? 'W' : lon > 0 ? 'E' : ''}</text>`;
  }).join('')}${latitude.map((lat) => {
    const { y } = project(0, lat);
    return `<text class="geo-axis" x="${FRAME.left - 12}" y="${y + 4}" text-anchor="end">${Math.abs(lat)}°${lat < 0 ? 'S' : lat > 0 ? 'N' : ''}</text>`;
  }).join('')}`;
  return { lines, labels };
}

function countryPath(country) {
  const polygons = country.geometry.type === 'Polygon' ? [country.geometry.coordinates] : country.geometry.coordinates;
  return polygons.map((polygon) => polygon.map((ring) => ring.map(([lon, lat], index) => {
    const point = project(lon, lat);
    return `${index ? 'L' : 'M'}${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
  }).join('') + 'Z').join('')).join('');
}

function coordinatesPath(coordinates, close = false) {
  const path = coordinates.map(([lon, lat], index) => {
    const point = project(lon, lat);
    return `${index ? 'L' : 'M'}${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
  }).join('');
  return close ? `${path}Z` : path;
}

function googleMapUrl(region, mapMode) {
  const query = encodeURIComponent(`${region.lat},${region.lon}`);
  const type = mapMode === 'terrain' ? 'p' : 'k';
  return `https://www.google.com/maps?q=${query}&z=7&t=${type}&output=embed`;
}

function renderMapModeBar(state, visibleRegions) {
  const activeRegion = visibleRegions.find((region) => region.id === state.mapRegion) || visibleRegions[0];
  return `<div class="map-mode-bar"><div class="map-mode-tabs" role="tablist" aria-label="地图底图模式"><button type="button" role="tab" data-map-mode="vector" aria-selected="${state.mapMode === 'vector'}" class="${state.mapMode === 'vector' ? 'active' : ''}">矢量总览</button><button type="button" role="tab" data-map-mode="satellite" aria-selected="${state.mapMode === 'satellite'}" class="${state.mapMode === 'satellite' ? 'active' : ''}">Google 卫星</button><button type="button" role="tab" data-map-mode="terrain" aria-selected="${state.mapMode === 'terrain'}" class="${state.mapMode === 'terrain' ? 'active' : ''}">Google 地形</button></div>${state.mapMode === 'vector' ? '<p>锚点、交易所与关联线保持可点击</p>' : `<div class="map-region-tabs" role="group" aria-label="选择 Google 地图产区">${visibleRegions.map((region) => `<button type="button" data-map-region="${region.id}" class="${region.id === activeRegion.id ? 'active' : ''}">${region.name}</button>`).join('')}</div>`}</div>`;
}

function renderGoogleMap(state, visibleRegions) {
  const region = visibleRegions.find((item) => item.id === state.mapRegion) || visibleRegions[0];
  return `<div class="google-map-shell"><iframe title="${escapeMarkup(region.name)} Google ${state.mapMode === 'terrain' ? '地形' : '卫星'}地图" src="${googleMapUrl(region, state.mapMode)}" loading="lazy" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe><div class="google-map-credit"><b>${escapeMarkup(region.name)}</b><span>${formatCoordinate(region.lat, 'N', 'S')} · ${formatCoordinate(region.lon, 'E', 'W')}</span><small>Google Maps 交互底图 · 可拖动和缩放</small></div></div>`;
}

function renderKmlFeatures(kmlData) {
  if (!kmlData?.features) return '';
  return kmlData.features.map((feature) => {
    const safeName = escapeMarkup(feature.name);
    if (feature.geometry.type === 'Point') {
      const [lon, lat] = feature.geometry.coordinates;
      const point = project(lon, lat);
      return `<g class="kml-feature kml-point"><title>${safeName} · ${formatCoordinate(lat, 'N', 'S')}, ${formatCoordinate(lon, 'E', 'W')}</title><path d="M${point.x} ${point.y - 13}c-7 0-12 5-12 12 0 9 12 20 12 20s12-11 12-20c0-7-5-12-12-12Z"/><circle cx="${point.x}" cy="${point.y - 1}" r="4"/><text x="${point.x + 16}" y="${point.y + 5}">${safeName}</text></g>`;
    }
    if (feature.geometry.type === 'LineString') return `<path class="kml-feature kml-line" d="${coordinatesPath(feature.geometry.coordinates)}"><title>${safeName}</title></path>`;
    if (feature.geometry.type === 'Polygon') return `<path class="kml-feature kml-polygon" d="${coordinatesPath(feature.geometry.coordinates, true)}"><title>${safeName}</title></path>`;
    return '';
  }).join('');
}

function renderKmlInspector(state) {
  if (!state.kmlEnabled || !state.kmlData?.summary) return '';
  const { summary, groundOverlays, features } = state.kmlData;
  return `<section class="kml-inspector" aria-label="Google Earth KML 解析结果">
    <header><div><span>GOOGLE EARTH / KML</span><b>图层解析结果</b></div><strong>${summary.overlays} 图层 · ${summary.points + summary.lines + summary.polygons} 几何</strong></header>
    <div class="kml-summary"><span><i>${summary.points}</i> 点</span><span><i>${summary.lines}</i> 线</span><span><i>${summary.polygons}</i> 面</span><span><i>${summary.privateOverlays}</i> 私有影像索引</span></div>
    <div class="kml-layer-list">${groundOverlays.map((layer) => `<div><i>${String(layer.order).padStart(2, '0')}</i><b>${escapeMarkup(layer.name)}</b><span>${layer.privateTile ? 'Earth 专属影像' : '网页可渲染'}</span></div>`).join('')}</div>
    ${features.length ? `<p>已投影坐标：${features.map((feature) => `${escapeMarkup(feature.name)}（${feature.geometry.type}）`).join('、')}</p>` : ''}
    <small><code>earthdatalayer:</code> 只提供 Google Earth 内部索引；网页保留图层名称、范围和顺序，不伪造影像内容。</small>
  </section>`;
}

function routePath(start, end, index) {
  const distance = Math.abs(end.x - start.x) + Math.abs(end.y - start.y);
  const bend = Math.min(46, Math.max(16, distance * 0.16)) * (index % 2 ? 1 : -1);
  const controlX = (start.x + end.x) / 2;
  const controlY = (start.y + end.y) / 2 + bend;
  return `M${start.x.toFixed(1)} ${start.y.toFixed(1)} Q${controlX.toFixed(1)} ${controlY.toFixed(1)} ${end.x.toFixed(1)} ${end.y.toFixed(1)}`;
}

const countryLabels = [
  { label: 'UNITED STATES', lon: -101, lat: 53 },
  { label: 'BRAZIL', lon: -54, lat: 1 },
  { label: 'CHINA', lon: 101, lat: 57 },
];

function clampMapView() {
  const minX = FRAME.right * (1 - mapView.scale);
  const maxX = FRAME.left * (1 - mapView.scale);
  const minY = FRAME.bottom * (1 - mapView.scale);
  const maxY = FRAME.top * (1 - mapView.scale);
  mapView.x = Math.min(maxX, Math.max(minX, mapView.x));
  mapView.y = Math.min(maxY, Math.max(minY, mapView.y));
}

function mapTransform() {
  return `translate(${mapView.x.toFixed(2)} ${mapView.y.toFixed(2)}) scale(${mapView.scale.toFixed(3)})`;
}

export function bindMapInteractions() {
  const svg = document.querySelector('#coverage-map');
  const viewport = svg?.querySelector('.map-viewport');
  const zoomLevel = document.querySelector('#map-zoom-level');
  const detailStatus = document.querySelector('#map-detail-status');
  const resetButton = document.querySelector('[data-map-action="reset"]');
  if (!svg || !viewport) return;

  const applyView = () => {
    clampMapView();
    viewport.setAttribute('transform', mapTransform());
    if (zoomLevel) zoomLevel.textContent = `${Math.round(mapView.scale * 100)}%`;
    const zoomTier = mapView.scale >= 2.15 ? 'micro' : mapView.scale >= 1.4 ? 'detail' : 'overview';
    svg.dataset.zoom = zoomTier;
    if (detailStatus) detailStatus.textContent = zoomTier === 'micro' ? '细节层 L3' : zoomTier === 'detail' ? '细节层 L2' : '概览层 L1';
    if (resetButton) resetButton.disabled = mapView.scale === 1;
  };
  const center = () => ({
    x: Number(svg.dataset.focusX) || (FRAME.left + FRAME.right) / 2,
    y: Number(svg.dataset.focusY) || (FRAME.top + FRAME.bottom) / 2,
  });
  const eventPoint = (event) => {
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  };
  const zoomAt = (factor, point = center()) => {
    const previousScale = mapView.scale;
    const nextScale = Math.min(ZOOM.max, Math.max(ZOOM.min, previousScale * factor));
    if (nextScale === previousScale) return;
    const contentX = (point.x - mapView.x) / previousScale;
    const contentY = (point.y - mapView.y) / previousScale;
    mapView.scale = nextScale;
    mapView.x = point.x - contentX * nextScale;
    mapView.y = point.y - contentY * nextScale;
    applyView();
  };
  const resetView = () => {
    Object.assign(mapView, { scale: 1, x: 0, y: 0 });
    applyView();
  };

  svg.addEventListener('wheel', (event) => {
    event.preventDefault();
    zoomAt(Math.exp(-event.deltaY * 0.0015), eventPoint(event));
  }, { passive: false });
  svg.addEventListener('dblclick', (event) => {
    event.preventDefault();
    zoomAt(ZOOM.step, eventPoint(event));
  });

  let dragging = false;
  let moved = false;
  let previousPoint;
  svg.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || mapView.scale === 1) return;
    dragging = true;
    moved = false;
    previousPoint = eventPoint(event);
    svg.setPointerCapture(event.pointerId);
    svg.classList.add('dragging');
  });
  svg.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const point = eventPoint(event);
    const dx = point.x - previousPoint.x;
    const dy = point.y - previousPoint.y;
    if (Math.abs(dx) + Math.abs(dy) > 1) moved = true;
    mapView.x += dx;
    mapView.y += dy;
    previousPoint = point;
    applyView();
  });
  const endDrag = (event) => {
    if (!dragging) return;
    dragging = false;
    svg.releasePointerCapture(event.pointerId);
    svg.classList.remove('dragging');
  };
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);
  svg.addEventListener('click', (event) => {
    if (!moved) return;
    event.preventDefault();
    event.stopPropagation();
    moved = false;
  }, true);
  svg.addEventListener('keydown', (event) => {
    if (event.key === '+' || event.key === '=') zoomAt(ZOOM.step);
    else if (event.key === '-') zoomAt(1 / ZOOM.step);
    else if (event.key === '0' || event.key === 'Escape') resetView();
    else return;
    event.preventDefault();
  });

  document.querySelectorAll('[data-map-action]').forEach((button) => button.addEventListener('click', () => {
    if (button.dataset.mapAction === 'zoom-in') zoomAt(ZOOM.step);
    if (button.dataset.mapAction === 'zoom-out') zoomAt(1 / ZOOM.step);
    if (button.dataset.mapAction === 'reset') resetView();
  }));
  applyView();
}

export function renderMapView(state) {
  const crop = cropProfiles[state.selectedCrop];
  const visibleRegionIds = new Set(crop ? crop.regionIds : regions.map((region) => region.id));
  const visibleRegions = regions.filter((region) => visibleRegionIds.has(region.id));
  const visibleMarkets = state.selectedMarket !== 'all'
    ? [markets[state.selectedMarket]]
    : crop
      ? crop.marketCodes.map((code) => markets[code])
      : Object.values(markets);
  const byId = Object.fromEntries(regions.map((region) => [region.id, region]));
  const routes = state.showRoutes ? visibleMarkets.flatMap((market) => {
    const start = project(market.lon, market.lat, market.offsetX, market.offsetY);
    const linkedRegionIds = crop ? crop.regionIds : market.regionIds;
    return linkedRegionIds.filter((regionId) => visibleRegionIds.has(regionId)).map((regionId, index) => {
      const end = project(byId[regionId].lon, byId[regionId].lat);
      return `<path class="map-route ${market.ready ? 'ready' : ''}" d="${routePath(start, end, index)}"/>`;
    });
  }).join('') : '';
  const regionNodes = visibleRegions.map((region) => {
    const point = project(region.lon, region.lat);
    const targetCrop = crop?.id || region.crops[0] || 'all';
    const radiusX = region.country === '中国' ? 28 : region.id === 'us_belt' ? 42 : 32;
    const radiusY = region.id === 'br_soy' ? 34 : 22;
    return `<g class="region-detail crop-${targetCrop}"><ellipse class="region-footprint" cx="${point.x}" cy="${point.y}" rx="${radiusX}" ry="${radiusY}"/><g class="map-node region" data-region="${region.id}" data-crop="${targetCrop}"><title>${region.name} · ${region.crop} · ${formatCoordinate(region.lat, 'N', 'S')}, ${formatCoordinate(region.lon, 'E', 'W')}</title><circle class="node-halo" cx="${point.x}" cy="${point.y}" r="12"/><circle class="node-core" cx="${point.x}" cy="${point.y}" r="5"/><text x="${point.x + region.labelDx}" y="${point.y + region.labelDy}">${region.name}</text><text class="node-meta" x="${point.x + region.labelDx}" y="${point.y + region.labelDy + 12}">${region.crop}</text></g></g>`;
  }).join('');
  const marketNodes = visibleMarkets.map((market) => {
    const point = project(market.lon, market.lat, market.offsetX, market.offsetY);
    const placeLeft = point.x > FRAME.right - 74;
    const labelX = point.x + (placeLeft ? -13 : 13);
    const labelWidth = market.code.length * 8 + 18;
    const rectX = placeLeft ? labelX - labelWidth + 7 : labelX - 7;
    return `<g class="map-node market ${market.ready ? 'ready' : ''} ${state.selectedMarket === market.code ? 'selected' : ''}" data-market="${market.code}"><circle class="market-halo" cx="${point.x}" cy="${point.y}" r="13"/><circle class="market-core" cx="${point.x}" cy="${point.y}" r="7"/>${market.ready ? `<circle class="pulse" cx="${point.x}" cy="${point.y}" r="7"/>` : ''}<g class="market-badge"><rect class="market-tag" x="${rectX}" y="${point.y - 23}" width="${labelWidth}" height="20" rx="10"/><text class="market-label" x="${labelX}" y="${point.y - 9}" text-anchor="${placeLeft ? 'end' : 'start'}">${market.code}</text></g></g>`;
  }).join('');
  const caption = state.stressMode
    ? '玉米热旱冲击模拟：华北、东北的气候异常沿作物图谱传播；DCE 玉米仅作市场信号观察。'
    : crop
      ? `${crop.name}视图：${crop.note}`
    : state.selectedMarket === 'all'
      ? '覆盖中国、美国、巴西 10 个代表性产区锚点；位置按经纬度投影，不等同于行政区边界。'
      : `${markets[state.selectedMarket].name}位于${markets[state.selectedMarket].city}；连线表示作物—市场的解释关系，不表示物流或资金流。`;
  const countryIds = { 中国: 'CHN', 美国: 'USA', 巴西: 'BRA' };
  const activeCountryIds = new Set(crop ? crop.countries.map((country) => countryIds[country]) : Object.values(countryIds));
  const countryPaths = coverageCountries.map((country) => ({ country, path: countryPath(country) }));
  const countries = countryPaths.map(({ country, path }) => `<path class="coverage-country coverage-${country.id.toLowerCase()} ${activeCountryIds.has(country.id) ? 'crop-active' : 'crop-muted'}" d="${path}" fill-rule="evenodd"/>`).join('');
  const labels = countryLabels.map(({ label, lon, lat }) => {
    const point = project(lon, lat);
    return `<text class="country-label" x="${point.x}" y="${point.y}" text-anchor="middle">${label}</text>`;
  }).join('');
  const mapGrid = grid();
  const kmlFeatures = state.kmlEnabled ? renderKmlFeatures(state.kmlData) : '';
  const focusRegion = visibleRegions.find((region) => region.country === '中国') || visibleRegions[0];
  const mapFocus = project(focusRegion?.lon ?? 112, focusRegion?.lat ?? 35);
  const title = crop ? `${crop.name}产区与市场链` : '农业期货与产区锚点';
  const subtitle = crop ? `${crop.countries.join(' / ')} · ${crop.regionIds.length} 个代表性产区 · ${crop.signals} 个市场信号` : '三国矢量边界 · 10 个代表性产区 · 4 个交易所节点';
  const coverageTitle = crop ? crop.countries.map((country) => country === '中国' ? 'CHINA' : country === '美国' ? 'USA' : 'BRAZIL').join(' · ') : 'CHINA · USA · BRAZIL';
  const mapMode = ['vector', 'satellite', 'terrain'].includes(state.mapMode) ? state.mapMode : 'vector';
  const vectorMap = `<div class="map-canvas">
      <svg id="coverage-map" viewBox="0 0 ${VIEWBOX.width} ${VIEWBOX.height}" data-focus-x="${mapFocus.x.toFixed(1)}" data-focus-y="${mapFocus.y.toFixed(1)}" tabindex="0" role="img" aria-label="可缩放的农业产区锚点与期货交易所矢量总览地图">
        <defs><clipPath id="coverage-clip"><rect x="${FRAME.left}" y="${FRAME.top}" width="${FRAME.right - FRAME.left}" height="${FRAME.bottom - FRAME.top}" rx="16"/></clipPath><radialGradient id="ocean-glow" cx="50%" cy="45%" r="70%"><stop offset="0" stop-color="#163d30"/><stop offset="1" stop-color="#071612"/></radialGradient><filter id="country-glow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="8" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
        <rect class="geo-ocean" x="${FRAME.left}" y="${FRAME.top}" width="${FRAME.right - FRAME.left}" height="${FRAME.bottom - FRAME.top}" rx="16"/>
        <g clip-path="url(#coverage-clip)"><g class="map-viewport" transform="${mapTransform()}">${mapGrid.lines}<g class="country-layer">${countries}</g>${labels}${routes}${regionNodes}${kmlFeatures}${marketNodes}</g></g>
        <rect class="geo-frame" x="${FRAME.left}" y="${FRAME.top}" width="${FRAME.right - FRAME.left}" height="${FRAME.bottom - FRAME.top}" rx="16"/>${mapGrid.labels}<text class="geo-title" x="500" y="82" text-anchor="middle">${coverageTitle}</text><text class="geo-subtitle" x="500" y="103" text-anchor="middle">${crop ? `${crop.name.toUpperCase()} COVERAGE / ${crop.regionIds.length} ANCHORS` : 'AGRICULTURAL COVERAGE NETWORK / 10 ANCHORS'}</text><g class="map-scale" transform="translate(790 594)"><line x1="0" y1="0" x2="110" y2="0"/><circle cx="0" cy="0" r="2"/><circle cx="110" cy="0" r="2"/><text x="55" y="18" text-anchor="middle">REPRESENTATIVE LOCATIONS</text></g>
      </svg>
      <div class="map-controls" role="group" aria-label="地图缩放控件"><button type="button" data-map-action="zoom-in" aria-label="放大地图" title="放大">+</button><button type="button" data-map-action="zoom-out" aria-label="缩小地图" title="缩小">−</button><button type="button" class="map-reset" data-map-action="reset" aria-label="复位地图" title="复位地图"><span id="map-zoom-level">${Math.round(mapView.scale * 100)}%</span><b>复位</b></button></div><span class="map-gesture-hint">滚轮缩放 · 拖拽平移 · 双击放大</span>
    </div>`;
  const detailNote = mapMode === 'vector'
    ? `${caption} 点击上方 Google 卫星或地形，可查看真实影像底图；两种地图分层显示，互不遮挡。`
    : 'Google Maps 作为独立 iframe 加载，可直接拖动和缩放；本平台的产区锚点与交易所关系请切回“矢量总览”查看。';
  return `<article class="map-panel panel"><header class="panel-header"><div><span class="panel-eyebrow">${crop ? `${crop.code} CROP VIEW` : 'GLOBAL CROP COVERAGE'}</span><h2>${title}</h2><p>${subtitle}</p></div><div class="legend"><span class="ready-key">行情样例</span><span class="waiting-key">合约目录</span><span class="region-key">产区锚点</span>${state.kmlEnabled ? '<span class="kml-key">KML 几何</span>' : ''}</div></header>${renderMapModeBar({ ...state, mapMode }, visibleRegions)}<div class="map-stage ${state.motion ? '' : 'paused'}">${mapMode === 'vector' ? vectorMap : renderGoogleMap({ ...state, mapMode }, visibleRegions)}${mapMode === 'vector' ? renderKmlInspector(state) : ''}<div class="map-caption"><span>MAP NOTE</span><p>${detailNote}</p></div></div></article>`;
}
