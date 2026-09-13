import { queue } from '../data/market-catalog.js';

export function renderStatusPanels() {
  return `<section class="bottom-grid"><article class="panel queue"><h3>市场接入队列</h3><div class="queue-head"><span>数据源</span><span>品种</span><span>状态</span></div>${queue.map(([source, product, status, kind]) => `<div class="queue-row"><b>${source}</b><span>${product}</span><b class="${kind}">${status}</b></div>`).join('')}</article><article class="panel callout"><h3>演示口径</h3><p>期货是价格/需求侧信号，不会被作为作物实际产量。尚无行情文件的品种只展示合约目录，避免显示为实时数据。</p><button id="stress-mode">模拟玉米热旱冲击</button></article></section>`;
}
