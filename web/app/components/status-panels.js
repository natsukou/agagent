import { cropProfiles, queue, wealthBenchmarks } from '../data/market-catalog.js';

const defaultStrategy = { title: '多作物低频学习组合', target: 4.8, drawdown: 8, cadence: '每周复盘', risk: '中高' };

function renderCapabilityComparison(crop) {
  const stages = ['数据整合', '产区预测', '风险解释', '复盘辅助'];
  const series = [
    { id: 'atlas', name: '绿星图光', values: [62, 81, 89, 93] },
    { id: 'tools', name: '通用数据工具', values: [55, 64, 68, 70] },
    { id: 'manual', name: '人工单表研判', values: [39, 47, 52, 57] },
  ];
  const left = 58;
  const top = 20;
  const bottom = 170;
  const step = 224;
  const point = (value, index) => [left + step * index, bottom - (value / 100) * (bottom - top)];
  const grid = [0, 25, 50, 75, 100].map((value) => {
    const y = point(value, 0)[1];
    return `<g class="capability-grid"><line x1="${left}" y1="${y}" x2="${left + step * 3}" y2="${y}"></line><text x="${left - 12}" y="${y + 4}" text-anchor="end">${value}</text></g>`;
  }).join('');
  const lines = series.map((item) => {
    const points = item.values.map((value, index) => point(value, index));
    const path = points.map(([x, y], index) => `${index ? 'L' : 'M'} ${x} ${y}`).join(' ');
    const marks = points.map(([x, y], index) => `<circle cx="${x}" cy="${y}" r="${item.id === 'atlas' ? 4.5 : 3}" aria-label="${item.name}，${stages[index]}，${item.values[index]}分"><title>${item.name} · ${stages[index]}：${item.values[index]} / 100</title></circle>`).join('');
    const [endX, endY] = points.at(-1);
    return `<g class="capability-series capability-${item.id}"><path d="${path}"></path>${marks}<text class="capability-end-value" x="${endX + 13}" y="${endY + 4}">${item.values.at(-1)}</text></g>`;
  }).join('');
  const labels = stages.map((stage, index) => `<text class="capability-stage" x="${left + step * index}" y="199" text-anchor="middle">${stage}</text>`).join('');

  return `<section class="capability-comparison" aria-labelledby="capability-title">
    <header class="capability-header">
      <div><span class="strategy-kicker">PREDICTION &amp; DECISION SUPPORT</span><h3 id="capability-title">预测与研判辅助能力对比</h3><p>${crop ? `${crop.name}视角` : '多作物综合视角'} · 从数据接入到复盘的研究工作流</p></div>
      <div class="capability-score"><strong>93</strong><span>/ 100</span><small>内部能力量表</small></div>
    </header>
    <div class="capability-legend" aria-label="图例">
      ${series.map((item) => `<span class="legend-${item.id}"><i></i>${item.name}</span>`).join('')}
    </div>
    <div class="capability-chart" role="img" aria-label="绿星图光、通用数据工具与人工单表研判在数据整合、产区预测、风险解释和复盘辅助四个环节的能力指数对比。绿星图光最终为93分，通用数据工具70分，人工单表研判57分。">
      <svg viewBox="0 0 810 215" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        ${grid}${lines}${labels}
      </svg>
    </div>
    <div class="capability-summary"><b>领先来自完整链路</b><span>地图化产区信号、作物预测、理由码与复盘记录在同一工作流中衔接。</span><em>较通用工具 +23</em></div>
    <p class="capability-method">能力指数为产品功能覆盖、信息时效、解释性与复盘支持的内部归一化示意，用于说明产品差异；并非第三方测评、预测准确率、投资收益或收益承诺。</p>
  </section>`;
}

function renderStrategyComparison(crop) {
  const strategy = crop?.strategy || defaultStrategy;
  const items = [
    { id: 'agri', name: strategy.title, value: strategy.target, label: '教学目标情景', risk: `${strategy.risk}风险`, recommended: true },
    ...wealthBenchmarks,
  ];
  const max = Math.max(...items.map((item) => item.value)) * 1.12;
  return `<section class="strategy-panel panel">
    <header class="strategy-header">
      <div><span class="panel-eyebrow">LEARNING STRATEGY LAB</span><h2>推荐学习策略对比</h2><p>${crop ? `当前为${crop.name}视角` : '多作物综合视角'} · 用收益目标与风险约束一起理解策略</p></div>
      <span class="education-badge">仅供学习参考</span>
    </header>
    <div class="strategy-layout">
      <article class="strategy-feature">
        <div class="strategy-kicker"><span>本平台推荐学习方案</span><b>${strategy.cadence}</b></div>
        <h3>${strategy.title}</h3>
        <p>${crop ? crop.focus : '分散跟踪大豆、玉米与棉花的产区变化，以低频观察代替高频交易。'}</p>
        <div class="strategy-numbers"><div><span>目标年化中枢</span><strong>${strategy.target.toFixed(1)}%</strong><small>情景假设，非已实现</small></div><div><span>回撤约束</span><strong class="risk-number">-${strategy.drawdown}%</strong><small>超过即停止模拟</small></div></div>
      </article>
      <div class="return-comparison" role="img" aria-label="学习策略与常见理财产品年化参考收益对比">
        <div class="comparison-axis"><span>年化参考 / 目标</span><small>0—${Math.ceil(max)}%</small></div>
        ${items.map((item) => `<div class="comparison-row ${item.recommended ? 'recommended' : ''}"><div class="comparison-label"><b>${item.name}</b><small>${item.label}</small></div><div class="comparison-track"><i style="width:${Math.max(6, item.value / max * 100).toFixed(1)}%"></i></div><strong>${item.value.toFixed(2)}%</strong><span>${item.risk}${item.recommended ? ' · 学习推荐' : ''}</span></div>`).join('')}
      </div>
    </div>
    ${renderCapabilityComparison(crop)}
    <footer class="strategy-note"><b>重要说明</b><p>策略数值是教学目标情景，不是实盘回测、收益承诺或购买建议；目标更高意味着波动和亏损风险更高。常规产品为公开参考快照：银行理财 2.31% 采用普益标准 2026 年上半年行业均值，一年期定存采用大型银行挂牌参考，现金管理为教学中枢。期限、流动性和风险不同，不能直接替代比较。</p><a href="https://jrj.wuhan.gov.cn/ztzl_57/xyrd/yxy/202607/t20260715_2821452.shtml" target="_blank" rel="noreferrer">查看银行理财公开口径 ↗</a></footer>
  </section>`;
}

export function renderStatusPanels(state) {
  const crop = cropProfiles[state.selectedCrop];
  return `${renderStrategyComparison(crop)}<section class="bottom-grid"><article class="panel queue"><h3>覆盖范围与资料</h3><div class="queue-head"><span>资料</span><span>范围</span><span>状态</span></div>${queue.map(([source, product, status, kind]) => `<div class="queue-row"><b>${source}</b><span>${product}</span><b class="${kind}">${status}</b></div>`).join('')}</article><article class="panel callout"><h3>研究口径</h3><p>地图在网页内直接绘制中国、美国、巴西的矢量边界，并叠加可核验的经纬度锚点。页面用于学习产区、气候与市场信号的关系，不提供个性化投资建议。</p><button id="stress-mode">查看玉米热旱教学情景</button></article></section>`;
}
