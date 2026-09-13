export const markets = {
  DCE: {
    code: 'DCE', name: '大连商品交易所', city: '大连', x: 694, y: 192,
    ready: true, source: '本地 DCE CSV/JSON', regions: '东北、华北、长江、华南',
    contracts: ['玉米 c', '玉米淀粉 cs', '粳米 rr', '豆一 a', '豆二 b', '豆粕 m', '豆油 y', '棕榈油 p', '鸡蛋 jd', '生猪 lh'],
    price: [2386, 2400],
    intro: '项目已实现 DCE 日行情文件适配；玉米与粳米可关联现有作物布局。',
  },
  CZCE: {
    code: 'CZCE', name: '郑州商品交易所', city: '郑州', x: 512, y: 294,
    ready: false, source: '待接 CSV/JSON', regions: '华北、黄淮海、长江、华南',
    contracts: ['强麦 WH', '普麦 PM', '早籼稻 RI', '晚籼稻 LR', '棉花 CF', '白糖 SR', '菜籽 RS', '菜油 OI', '菜粕 RM', '花生 PK', '苹果 AP', '红枣 CJ'],
    intro: '小麦和籼稻不在 DCE 覆盖范围；此处预留郑商所文件接入后的映射位置。',
  },
  SHFE: {
    code: 'SHFE', name: '上海期货交易所', city: '上海', x: 649, y: 349,
    ready: false, source: '待接 CSV/JSON', regions: '华南及天然橡胶产区',
    contracts: ['天然橡胶 RU'],
    intro: '天然橡胶暂未进入现有作物产量布局，保留交易所与品种目录。',
  },
};

export const regions = [
  { id: 'dongbei', name: '东北春玉米带', x: 650, y: 148, crop: '玉米' },
  { id: 'huabei', name: '华北小麦玉米带', x: 514, y: 250, crop: '小麦 / 玉米' },
  { id: 'changjiang', name: '长江中下游稻区', x: 590, y: 344, crop: '水稻' },
  { id: 'huanan', name: '华南双季稻区', x: 522, y: 430, crop: '水稻' },
];

export const queue = [
  ['DCE 日行情', '玉米、粳米', '已接入', 'ready'],
  ['CZCE 日行情', '小麦、稻米、油料', '待接文件', 'waiting'],
  ['区县统计年鉴', '单产、面积', '待接', 'waiting'],
];
