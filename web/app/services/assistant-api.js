const MAX_HISTORY_ITEMS = 8;

export async function askAssistant(question, history = [], context = {}) {
  const response = await fetch('/api/assistant', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      history: history.slice(-MAX_HISTORY_ITEMS),
      context,
    }),
  });

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error('助手服务返回了无法识别的响应。');
  }
  if (!response.ok) {
    throw new Error(payload.error || `助手服务暂时不可用（${response.status}）。`);
  }
  return payload;
}
