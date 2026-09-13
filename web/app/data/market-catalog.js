export const markets = {
  DCE: {
    code: 'DCE', name: '大连商品交易所', city: '大连', lon: 121.61, lat: 38.92,
    ready: true, source: '本地行情样例（2024-07-15/16）', regionIds: ['dongbei', 'huabei', 'changjiang', 'huanan'],
    contracts: ['玉米 c', '玉米淀粉 cs', '粳米 rr', '豆一 a', '豆二 b', '豆粕 m', '豆油 y', '棕榈油 p', '鸡蛋 jd', '生猪 lh'],
    price: [2386, 2400],
    intro: 'DCE 价格仅作为市场信号观察，不与作物实际产量混为同一指标。',
  },
  CZCE: {
    code: 'CZCE', name: '郑州商品交易所', city: '郑州', lon: 113.65, lat: 34.76,
    ready: false, source: '合约目录已索引', regionIds: ['huabei', 'changjiang', 'huanan', 'xinjiang'],
    contracts: ['强麦 WH', '普麦 PM', '粳稻 JR', '早籼稻 RI', '晚籼稻 LR', '棉花 CF', '棉纱 CY', '白糖 SR', '菜籽 RS', '菜油 OI', '菜粕 RM', '花生 PK', '苹果 AP', '红枣 CJ'],
    intro: '小麦、稻米、棉花与油料品种已按交易所和作物口径整理。',
  },
  SHFE: {
    code: 'SHFE', name: '上海期货交易所', city: '上海', lon: 121.47, lat: 31.23, offsetX: -15, offsetY: 18,
    ready: false, source: '合约目录已索引', regionIds: [],
    contracts: ['天然橡胶 RU'],
    intro: '天然橡胶作为农业相关品种保留在目录中，尚未纳入当前作物产量布局。',
  },
  INE: {
    code: 'INE', name: '上海国际能源中心', city: '上海', lon: 121.47, lat: 31.23, offsetX: 26, offsetY: 25,
    ready: false, source: '合约目录已索引', regionIds: [],
    contracts: ['20 号胶 NR'],
    intro: '20 号胶是进口橡胶价格参照，和现有粮棉咖啡布局分层展示。',
  },
};

// 坐标来自后端 regions.py；每一项是代表性产区的天气/物候锚点，不是行政区范围或面积中心。
export const regions = [
  { id: 'dongbei', name: '东北春玉米大豆带', crop: '玉米 / 大豆', crops: ['corn', 'soy'], country: '中国', lon: 126.65, lat: 45.75, labelDx: -156, labelDy: -12 },
  { id: 'huabei', name: '华北小麦玉米带', crop: '小麦 / 玉米', crops: ['corn'], country: '中国', lon: 114.51, lat: 38.04, labelDx: -133, labelDy: -12 },
  { id: 'changjiang', name: '长江中下游稻区', crop: '水稻', crops: [], country: '中国', lon: 114.31, lat: 30.59, labelDx: -126, labelDy: 21 },
  { id: 'huanan', name: '华南双季稻区', crop: '水稻', crops: [], country: '中国', lon: 113.26, lat: 23.13, labelDx: -114, labelDy: 22 },
  { id: 'xinjiang', name: '新疆棉区', crop: '棉花', crops: ['cotton'], country: '中国', lon: 86.04, lat: 44.30, labelDx: -76, labelDy: -13 },
  { id: 'yunnan', name: '云南小粒咖啡区', crop: '咖啡', crops: [], country: '中国', lon: 100.97, lat: 22.82, labelDx: -117, labelDy: 22 },
  { id: 'us_belt', name: '美国玉米大豆带', crop: '玉米 / 大豆', crops: ['corn', 'soy'], country: '美国', lon: -93.62, lat: 42.03, labelDx: -142, labelDy: -12 },
  { id: 'us_cotton', name: '美国棉花带', crop: '棉花', crops: ['cotton'], country: '美国', lon: -101.85, lat: 33.58, labelDx: -109, labelDy: 22 },
  { id: 'br_soy', name: '巴西大豆带', crop: '大豆', crops: ['soy'], country: '巴西', lon: -55.72, lat: -12.55, labelDx: 12, labelDy: -12 },
  { id: 'br_coffee', name: '巴西阿拉比卡咖啡带', crop: '咖啡', crops: [], country: '巴西', lon: -46.99, lat: -18.94, labelDx: 12, labelDy: 22 },
];

export const cropProfiles = {
  soy: {
    id: 'soy', code: 'SOY', name: '大豆', icon: '◒', regionIds: ['dongbei', 'us_belt', 'br_soy'], marketCodes: ['DCE'],
    countries: ['中国', '美国', '巴西'], layouts: 3, signals: 7,
    contracts: ['豆一 A', '豆二 B', '豆粕 M', '豆油 Y', 'CBOT 大豆 ZS', 'CBOT 豆粕 ZM', 'CBOT 豆油 ZL'],
    focus: '种植天气 → 压榨利润 → 豆粕 / 豆油',
    season: '北半球 4—10 月 · 巴西跨年生长季',
    note: '把中、美、巴主产区的水热压力与国内压榨链、CBOT 国际基准放在同一视图中学习。',
    strategy: { title: '大豆跨市场低频观察', target: 4.6, drawdown: 9, cadence: '每周复盘', risk: '中高' },
  },
  corn: {
    id: 'corn', code: 'CORN', name: '玉米', icon: '◆', regionIds: ['dongbei', 'huabei', 'us_belt'], marketCodes: ['DCE'],
    countries: ['中国', '美国'], layouts: 3, signals: 3,
    contracts: ['玉米 C', '玉米淀粉 CS', 'CBOT 玉米 ZC'],
    focus: '播种进度 → 热旱压力 → 饲料与深加工',
    season: '北半球 4—10 月 · 关键期集中',
    note: '聚焦东北、华北与美国玉米带，比较气候胁迫、国内现货代理和国际价格参照。',
    strategy: { title: '玉米气候异常观察', target: 4.3, drawdown: 7, cadence: '关键期复盘', risk: '中高' },
  },
  cotton: {
    id: 'cotton', code: 'COTTON', name: '棉花', icon: '✦', regionIds: ['xinjiang', 'us_cotton'], marketCodes: ['CZCE'],
    countries: ['中国', '美国'], layouts: 2, signals: 3,
    contracts: ['郑棉 CF', '棉纱 CY', 'ICE 棉花 CT'],
    focus: '水热条件 → 单产修正 → 纺织链观察',
    season: '4—11 月 · 水分敏感度较高',
    note: '并列新疆与美国棉区，用统一口径学习水热压力如何进入棉花和棉纱价格观察框架。',
    strategy: { title: '棉花产区压力观察', target: 5.1, drawdown: 12, cadence: '双周复盘', risk: '高' },
  },
};

export const wealthBenchmarks = [
  { id: 'wealth', name: '银行理财', value: 2.31, label: '2026 H1 行业平均', risk: '中低' },
  { id: 'cash', name: '现金管理 / 货基', value: 1.50, label: '教学参考中枢', risk: '低' },
  { id: 'deposit', name: '一年期定存', value: 0.95, label: '大型银行挂牌参考', risk: '低' },
];

export const queue = [
  ['代表性产区锚点', '中 / 美 / 巴', '10 个已建模', 'ready'],
  ['国内期货目录', 'DCE / CZCE / SHFE / INE', '26 个已索引', 'ready'],
  ['国际市场基准', 'CBOT / ICE / CME', '13 个参照', 'ready'],
  ['网页内矢量边界', '中国 / 美国 / 巴西', '无需外部地图', 'ready'],
  ['Google Earth KML', '点 / 线 / 面 + 图层元数据', '解析器已接入', 'ready'],
];
