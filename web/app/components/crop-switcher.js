import { cropProfiles } from '../data/market-catalog.js';

export function renderCropSwitcher(selectedCrop) {
  const options = [
    { id: 'all', name: '全部作物', meta: '10 个产区' },
    ...Object.values(cropProfiles).map((crop) => ({ id: crop.id, name: crop.name, meta: `${crop.regionIds.length} 个产区`, icon: crop.icon })),
  ];

  return `<section class="crop-switcher" aria-label="按作物筛选数据">
    <div class="crop-switcher-copy"><span>作物视角</span><b>选择品种查看专属产区与市场链</b></div>
    <div class="crop-tabs" role="group" aria-label="作物分类">
      ${options.map((crop) => `<button type="button" class="crop-tab ${selectedCrop === crop.id ? 'active' : ''}" data-crop="${crop.id}" aria-pressed="${selectedCrop === crop.id}"><i>${crop.icon || '◎'}</i><span>${crop.name}<small>${crop.meta}</small></span></button>`).join('')}
    </div>
  </section>`;
}
