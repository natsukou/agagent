const directChild = (node, localName) => Array.from(node?.children || []).find((child) => child.localName === localName);
const childText = (node, localName) => directChild(node, localName)?.textContent?.trim() || '';
const descendant = (node, localName) => Array.from(node?.getElementsByTagNameNS('*', localName) || [])[0];
const descendantText = (node, localName) => descendant(node, localName)?.textContent?.trim() || '';

function parseCoordinates(value) {
  return value.trim().split(/\s+/).map((tuple) => tuple.split(',').map(Number)).filter(([lon, lat]) => Number.isFinite(lon) && Number.isFinite(lat));
}

function parseGeometry(placemark) {
  const point = descendant(placemark, 'Point');
  if (point) return { type: 'Point', coordinates: parseCoordinates(descendantText(point, 'coordinates'))[0] };
  const line = descendant(placemark, 'LineString');
  if (line) return { type: 'LineString', coordinates: parseCoordinates(descendantText(line, 'coordinates')) };
  const polygon = descendant(placemark, 'Polygon');
  if (polygon) return { type: 'Polygon', coordinates: parseCoordinates(descendantText(polygon, 'coordinates')) };
  return null;
}

export function parseKml(xmlText) {
  const document = new DOMParser().parseFromString(xmlText, 'application/xml');
  if (document.querySelector('parsererror')) throw new Error('KML XML 无法解析');
  const root = descendant(document, 'Document');
  const groundOverlays = Array.from(document.getElementsByTagNameNS('*', 'GroundOverlay')).map((overlay, index) => {
    const pyramid = descendant(overlay, 'MapTilePyramid');
    const href = descendantText(pyramid || directChild(overlay, 'Icon'), 'href');
    const box = descendant(overlay, 'LatLonBox');
    return {
      id: overlay.getAttribute('id') || `overlay-${index + 1}`,
      name: childText(overlay, 'name') || `影像图层 ${index + 1}`,
      order: Number(childText(overlay, 'drawOrder')) || index + 1,
      bounds: box ? ['north', 'south', 'east', 'west'].reduce((value, key) => ({ ...value, [key]: Number(childText(box, key)) }), {}) : null,
      href,
      privateTile: href.startsWith('earthdatalayer:'),
    };
  });
  const features = Array.from(document.getElementsByTagNameNS('*', 'Placemark')).map((placemark, index) => ({
    id: placemark.getAttribute('id') || `feature-${index + 1}`,
    name: childText(placemark, 'name') || `Google Earth 标注点 ${index + 1}`,
    description: childText(placemark, 'description'),
    geometry: parseGeometry(placemark),
  })).filter((feature) => feature.geometry?.coordinates);
  return {
    name: childText(root, 'name') || 'Google Earth KML',
    groundOverlays,
    features,
    summary: {
      overlays: groundOverlays.length,
      privateOverlays: groundOverlays.filter((layer) => layer.privateTile).length,
      points: features.filter((feature) => feature.geometry.type === 'Point').length,
      lines: features.filter((feature) => feature.geometry.type === 'LineString').length,
      polygons: features.filter((feature) => feature.geometry.type === 'Polygon').length,
    },
  };
}

export async function loadBundledKml() {
  const response = await fetch('./data/agriculture-layers.kml', { cache: 'no-store' });
  if (!response.ok) throw new Error(`KML 请求失败：${response.status}`);
  return parseKml(await response.text());
}
