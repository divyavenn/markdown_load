import { ContentType, contentTypes } from './content-types.js';

const STATE_KEY = 'markdownLoadState';
const API_BASE_URL = 'http://127.0.0.1:8000';
const REQUEST_HEADERS = {
  'Content-Type': 'application/json',
  'Accept': 'text/markdown'
};

let processingQueue = false;

async function getState() {
  const result = await chrome.storage.local.get(STATE_KEY);
  return result[STATE_KEY] || { queue: [], ready: [] };
}

async function setState(state) {
  await chrome.storage.local.set({ [STATE_KEY]: state });
}

function createId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `id-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

chrome.runtime.onInstalled.addListener(() => {
  processQueue();
});

chrome.runtime.onStartup.addListener(() => {
  processQueue();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message).then((result) => {
    sendResponse({ ok: true, ...result });
  }).catch((error) => {
    console.error('Markdown.load error:', error);
    sendResponse({ error: error.message || 'Unexpected error' });
  });
  return true;
});

async function handleMessage(message) {
  switch (message?.type) {
    case 'enqueue':
      return enqueueItem(message);
    case 'retry':
      return retryItem(message.id);
    case 'removeQueue':
      return removeQueueItem(message.id);
    case 'downloadReady':
      return downloadReadyItem(message.id);
    case 'removeReady':
      return removeReadyItem(message.id);
    default:
      throw new Error('Unknown message type');
  }
}

async function enqueueItem({ url, filename, kind }) {
  if (!url) {
    throw new Error('Missing URL');
  }

  const state = await getState();
  const id = createId();
  state.queue.push({
    id,
    url,
    filename,
    kind: kind,
    status: 'pending',
    addedAt: Date.now()
  });
  await setState(state);
  processQueue();
  return { id };
}

async function retryItem(id) {
  const state = await getState();
  const item = state.queue.find((entry) => entry.id === id);
  if (!item) {
    throw new Error('Queue item not found');
  }
  item.status = 'pending';
  delete item.error;
  await setState(state);
  processQueue();
  return {};
}

async function removeQueueItem(id) {
  const state = await getState();
  const index = state.queue.findIndex((entry) => entry.id === id);
  if (index === -1) {
    throw new Error('Queue item not found');
  }
  state.queue.splice(index, 1);
  await setState(state);
  return {};
}

async function downloadReadyItem(id) {
  const state = await getState();
  const entryIndex = state.ready.findIndex((item) => item.id === id);
  if (entryIndex === -1) {
    throw new Error('Download not found');
  }
  const entry = state.ready[entryIndex];

  const blob = new Blob([entry.markdown], { type: 'text/markdown;charset=utf-8' });
  let objectUrl;
  let revokeUrl = null;
  if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
    objectUrl = URL.createObjectURL(blob);
    revokeUrl = () => URL.revokeObjectURL(objectUrl);
  } else {
    objectUrl = `data:text/markdown;charset=utf-8,${encodeURIComponent(entry.markdown)}`;
  }

  const downloadId = await new Promise((resolve, reject) => {
    chrome.downloads.download(
      {
        url: objectUrl,
        filename: entry.filename,
        saveAs: false
      },
      (downloadId) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(downloadId);
      }
    );
  });

  if (revokeUrl) {
    setTimeout(revokeUrl, 30_000);
  }

  state.ready.splice(entryIndex, 1);
  await setState(state);

  return { downloadId };
}

async function removeReadyItem(id) {
  const state = await getState();
  const index = state.ready.findIndex((entry) => entry.id === id);
  if (index === -1) {
    throw new Error('Ready item not found');
  }
  state.ready.splice(index, 1);
  await setState(state);
  return {};
}

async function processQueue() {
  if (processingQueue) {
    return;
  }
  processingQueue = true;

  try {
    while (true) {
      let state = await getState();
      const index = state.queue.findIndex((item) => item.status === 'pending');
      if (index === -1) {
        break;
      }

      const item = state.queue[index];
      item.status = 'processing';
      item.error = undefined;
      await setState(state);

      try {
        const markdown = await fetchMarkdownForItem(item);
        state = await getState();
        const queueIndex = state.queue.findIndex((entry) => entry.id === item.id);
        if (queueIndex !== -1) {
          state.queue.splice(queueIndex, 1);
          state.ready.push({
            id: item.id,
            url: item.url,
            filename: item.filename,
            markdown,
            completedAt: Date.now()
          });
          await setState(state);
        }
      } catch (error) {
        console.error('Queue item failed', error);
        state = await getState();
        const queueIndex = state.queue.findIndex((entry) => entry.id === item.id);
        if (queueIndex !== -1) {
          state.queue[queueIndex].status = 'error';
          state.queue[queueIndex].error = error.message || 'Conversion failed';
          await setState(state);
        }
      }
    }
  } finally {
    processingQueue = false;
    const state = await getState();
    const hasPending = state.queue?.some((item) => item.status === 'pending');
    if (hasPending) {
      processQueue();
    }
  }
}



async function getCookies(url){
  return new Promise((resolve) => {
    chrome.cookies.getAll({ url }, (cookies) => {
      if (chrome.runtime.lastError) {
        console.warn('Cookie lookup failed', chrome.runtime.lastError.message);
        resolve(null);
        return;
      }
      const cookieMap = {};
      cookies.forEach((cookie) => {
        cookieMap[cookie.name] = cookie.value;
      });
      resolve(cookieMap);
    });
  });
}

async function fetchMarkdown(item, ContentType){
  const cookies = await getCookies(item.url);
  const payload = {
    url: item.url,
    filename: item.filename,
    cookies: cookies || {}
  };

  const response = await fetch(`${API_BASE_URL}/${ContentType.endpoint}`, {
    method: 'POST',
    headers: REQUEST_HEADERS,
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    throw await buildError(response);
  }

  return await response.text();

}

  

async function buildError(response) {
  let detail = `HTTP ${response.status}`;
  try {
    const cloned = response.clone();
    const data = await cloned.json();
    if (data?.detail) {
      detail = data.detail;
    }
  } catch (err) {
    const text = await response.text();
    if (text) {
      detail = text;
    }
  }
  return new Error(detail);
}

