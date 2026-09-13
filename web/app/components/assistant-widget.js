import { askAssistant } from '../services/assistant-api.js';

const STARTERS = [
  '解释气候到产量再到期货的主链路',
  '当前哪些作物和地区有数据？',
  'GNN 与 GCN 在平台中的边界是什么？',
];

function appendMessage(container, role, content, citations = []) {
  const message = document.createElement('article');
  message.className = `assistant-message ${role}`;

  const label = document.createElement('span');
  label.className = 'assistant-message-label';
  label.textContent = role === 'user' ? '你' : '绿星助手';

  const body = document.createElement('div');
  body.className = 'assistant-message-body';
  String(content).split(/\n{2,}/).filter(Boolean).forEach((paragraph) => {
    const line = document.createElement('p');
    line.textContent = paragraph;
    body.append(line);
  });

  message.append(label, body);
  if (citations.length) {
    const source = document.createElement('small');
    source.className = 'assistant-citations';
    source.textContent = `依据：${citations.join(' · ')}`;
    message.append(source);
  }
  container.append(message);
  container.scrollTop = container.scrollHeight;
  return message;
}

export function mountAssistantWidget(getContext = () => ({})) {
  if (document.querySelector('#agri-assistant')) return;

  const root = document.createElement('section');
  root.id = 'agri-assistant';
  root.className = 'assistant-widget';
  root.innerHTML = `
    <button class="assistant-launcher" type="button" aria-expanded="false" aria-controls="assistant-panel">
      <span class="assistant-launcher-mark" aria-hidden="true">✦</span>
      <span class="assistant-launcher-copy"><b>问问绿星助手</b><small>气候 · 产量 · 期货解释</small></span>
    </button>
    <div id="assistant-panel" class="assistant-panel" role="dialog" aria-label="绿星农业智能助手" aria-hidden="true">
      <header class="assistant-header">
        <div class="assistant-avatar" aria-hidden="true">✦</div>
        <div><b>绿星助手</b><small><i></i> 农业图谱解释在线</small></div>
        <button class="assistant-close" type="button" aria-label="关闭助手">×</button>
      </header>
      <div class="assistant-scope"><span>受控 Harness</span> 只读解释，不执行交易，不替代农艺决策</div>
      <div class="assistant-messages" aria-live="polite"></div>
      <div class="assistant-starters"></div>
      <form class="assistant-form">
        <label class="sr-only" for="assistant-question">向绿星助手提问</label>
        <textarea id="assistant-question" maxlength="4000" rows="2" placeholder="例如：东北玉米的气候信号如何影响产量判断？"></textarea>
        <button type="submit" aria-label="发送问题">发送</button>
      </form>
      <footer>AI 输出需结合来源与覆盖缺口核验</footer>
    </div>`;
  document.body.append(root);

  const launcher = root.querySelector('.assistant-launcher');
  const panel = root.querySelector('.assistant-panel');
  const close = root.querySelector('.assistant-close');
  const messages = root.querySelector('.assistant-messages');
  const starters = root.querySelector('.assistant-starters');
  const form = root.querySelector('.assistant-form');
  const input = root.querySelector('textarea');
  const submit = form.querySelector('button');
  const history = [];

  function setOpen(open) {
    root.classList.toggle('open', open);
    launcher.setAttribute('aria-expanded', String(open));
    panel.setAttribute('aria-hidden', String(!open));
    if (open) window.setTimeout(() => input.focus(), 80);
  }

  async function send(question) {
    const cleanQuestion = question.trim();
    if (!cleanQuestion || submit.disabled) return;
    setOpen(true);
    appendMessage(messages, 'user', cleanQuestion);
    history.push({ role: 'user', content: cleanQuestion });
    input.value = '';
    starters.hidden = true;
    submit.disabled = true;
    input.disabled = true;
    const pending = appendMessage(messages, 'assistant', '正在沿“气候 → 产量 → 期货”链路检索…');
    pending.classList.add('pending');
    try {
      const result = await askAssistant(cleanQuestion, history.slice(0, -1), getContext());
      pending.remove();
      appendMessage(messages, 'assistant', result.answer, result.citations || []);
      history.push({ role: 'assistant', content: result.answer });
    } catch (error) {
      pending.remove();
      appendMessage(messages, 'assistant', `暂时无法完成解释：${error.message}`);
    } finally {
      submit.disabled = false;
      input.disabled = false;
      input.focus();
    }
  }

  STARTERS.forEach((question) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = question;
    button.addEventListener('click', () => send(question));
    starters.append(button);
  });
  appendMessage(messages, 'assistant', '你好，我可以结合当前图谱解释气候证据、产量含义、数据覆盖与期货市场信号。');

  launcher.addEventListener('click', () => setOpen(!root.classList.contains('open')));
  close.addEventListener('click', () => setOpen(false));
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    send(input.value);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && root.classList.contains('open')) setOpen(false);
  });
}
