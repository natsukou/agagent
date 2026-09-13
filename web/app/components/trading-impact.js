import { evidence, informationLayers, personas } from '../data/trading-impact.js';

const pct = (value) => `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)}%`;

function returnsChart() {
  const rows = Object.values(personas);
  const min = -2.2, max = 0.4, top = 24, bottom = 232;
  const y = (value) => top + ((max - value) / (max - min)) * (bottom - top);
  const zero = y(0);
  const ticks = [0, -0.5, -1, -1.5, -2];
  const bars = rows.map((row, index) => {
    const center = 155 + index * 205;
    const pair = [
      { value: row.off, x: center - 38, cls: 'without-info', label: '无干预' },
      { value: row.on, x: center + 4, cls: 'with-info', label: '产量干预' },
    ].map((bar) => {
      const barY = Math.min(zero, y(bar.value));
      const height = Math.max(2, Math.abs(y(bar.value) - zero));
      const labelY = bar.value < 0 ? barY + height + 17 : barY - 7;
      return `<g><rect class="return-bar ${bar.cls}" x="${bar.x}" y="${barY}" width="32" height="${height}" rx="3"><title>${row.name} · ${bar.label}：${pct(bar.value)}</title></rect><text class="bar-value" x="${bar.x + 16}" y="${labelY}" text-anchor="middle">${pct(bar.value)}</text></g>`;
    }).join('');
    return `${pair}<text class="group-label" x="${center}" y="263" text-anchor="middle">${row.short}</text>`;
  }).join('');

  return `<svg class="returns-chart" viewBox="0 0 720 280" role="img" aria-labelledby="returns-title returns-desc">
    <title id="returns-title">三类交易者无产量干预和有产量干预的模拟净收益对比</title>
    <desc id="returns-desc">新手两种情形分别为负百分之一百九十八点六和负百分之一百七十一；常规交易者为零和负百分之一百七十一；顶级交易者为负百分之一百六十四点九和负百分之一百七十一。</desc>
    ${ticks.map((tick) => `<line class="chart-grid" x1="70" x2="690" y1="${y(tick)}" y2="${y(tick)}"/><text class="axis-label" x="60" y="${y(tick) + 4}" text-anchor="end">${Math.round(tick * 100)}%</text>`).join('')}
    <line class="zero-line" x1="70" x2="690" y1="${zero}" y2="${zero}"/>${bars}
  </svg>`;
}

function contribution(persona) {
  const maxAbs = Math.max(...persona.links.map(([, value]) => Math.abs(value)));
  return persona.links.map(([name, value]) => {
    const width = Math.max(2, Math.abs(value) / maxAbs * 50);
    const side = value >= 0 ? 'left:50%' : 'right:50%';
    return `<div class="contribution-row"><span>${name}</span><div class="contribution-track"><i class="${value >= 0 ? 'positive' : 'negative'}" style="width:${width}%;${side}"></i></div><b class="${value >= 0 ? 'positive-text' : 'negative-text'}">${pct(value)}</b></div>`;
  }).join('');
}

export function renderTradingImpact(selectedPersona = 'beginner') {
  const persona = personas[selectedPersona] || personas.beginner;
  return `<section class="intelligence-page">
    <header class="strategy-hero"><div><p class="eyebrow">YIELD → DECISION → RETURN</p><h1>产量信息如何影响交易收益</h1><p>从作物产量事实出发，按交易者能力适配策略；所有收益均为历史样本模拟，不构成收益承诺。</p></div><div class="hero-signal"><span>当前证据结论</span><strong>产量信息有解释力，尚无独立交易优势</strong></div></header>
    <section class="information-flow" aria-label="农业信息链路">${informationLayers.map((layer, index) => `<article><span class="flow-step">${layer.step}</span><div><h2>${layer.title}</h2><p>${layer.text}</p><small>${layer.meta}</small></div>${index < informationLayers.length - 1 ? '<b aria-hidden="true">→</b>' : ''}</article>`).join('')}</section>
    <section class="evidence-strip" aria-label="历史证据">${evidence.map(([label, value, note]) => `<div><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join('')}</section>
    <section class="persona-section"><div class="section-heading"><div><p class="eyebrow">STRATEGY FIT</p><h2>按交易经验适配策略</h2></div><p>点击角色查看策略与收益贡献</p></div>
      <div class="persona-tabs" role="tablist" aria-label="选择交易者层级">${Object.entries(personas).map(([key, item]) => `<button type="button" role="tab" aria-selected="${key === selectedPersona}" class="persona-tab ${key === selectedPersona ? 'active' : ''}" data-persona="${key}"><span>${item.name}</span><b>${item.role}</b><i class="${item.delta >= 0 ? 'positive-text' : 'negative-text'}">信息影响 ${pct(item.delta)}</i></button>`).join('')}</div>
      <article class="persona-detail panel"><div class="persona-copy"><span class="tag">${persona.name}</span><h3>${persona.role}</h3><p>${persona.baseline}</p><ul>${persona.strategy.map((item) => `<li>${item}</li>`).join('')}</ul><div class="verdict"><span>样本结论</span><strong>${persona.verdict}</strong></div></div><div class="contribution"><h3>各作物链的信息收益影响</h3><p>有干预净收益 − 无干预净收益</p>${contribution(persona)}</div></article>
    </section>
    <section class="return-section panel"><div class="section-heading"><div><p class="eyebrow">BACKTEST RESULT</p><h2>三类交易者模拟净收益</h2></div><div class="chart-legend"><span class="without-info">无产量干预</span><span class="with-info">产量信息干预</span></div></div>${returnsChart()}<p class="chart-note">口径：新疆棉、美国棉、美国大豆、巴西咖啡四条主链净收益简单相加，已扣模拟换仓成本；不是组合复利，不能横推为账户收益率。当前结果说明规则需要库存、基差与市场预期确认后才能进入交易。</p></section>
  </section>`;
}
