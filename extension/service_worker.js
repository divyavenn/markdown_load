import { contentTypes } from './content-types.js';

const STATE_KEY = 'markdownLoadState';
const API_BASE_URL = 'http://127.0.0.1:8000';
const REQUEST_HEADERS = {
  'Content-Type': 'application/json',
  'Accept': 'application/json'
};

const JOB_STATUS_BASE_URL = `${API_BASE_URL}/jobs`;
const JOB_POLL_INTERVAL_MS = 3_000;
const JOB_POLL_MAX_INTERVAL_MS = 15_000;

const jobPolls = new Map();

let processingQueue = false;

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

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

function trackJobPoll(jobId, factory) {
  if (jobPolls.has(jobId)) {
    return jobPolls.get(jobId);
  }
  const task = (async () => {
    try {
      await factory();
    } finally {
      jobPolls.delete(jobId);
    }
  })();
  jobPolls.set(jobId, task);
  return task;
}

async function getQueueItemReference(itemId) {
  const state = await getState();
  const index = state.queue.findIndex((entry) => entry.id === itemId);
  if (index === -1) {
    return null;
  }
  return { state, index };
}

async function storeQueueItemJobId(itemId, jobId) {
  const reference = await getQueueItemReference(itemId);
  if (!reference) {
    return null;
  }
  const { state, index } = reference;
  const item = state.queue[index];
  item.jobId = jobId;
  await setState(state);
  return item;
}

async function setQueueItemProcessing(itemId) {
  const reference = await getQueueItemReference(itemId);
  if (!reference) {
    return null;
  }
  const { state, index } = reference;
  const item = state.queue[index];
  if (!item.jobId) {
    return null;
  }
  item.status = 'processing';
  item.jobStatus = 'processing';
  item.error = undefined;
  await setState(state);
  return item;
}

async function updateQueueItemJobStatus(itemId, jobStatus) {
  const reference = await getQueueItemReference(itemId);
  if (!reference) {
    return false;
  }
  const { state, index } = reference;
  state.queue[index].jobStatus = jobStatus;
  await setState(state);
  return true;
}

async function markQueueItemError(itemId, message) {
  const reference = await getQueueItemReference(itemId);
  if (!reference) {
    return;
  }
  const { state, index } = reference;
  state.queue[index].status = 'error';
  state.queue[index].jobStatus = 'error';
  state.queue[index].error = message;
  await setState(state);
}

async function moveQueueItemToReady(itemId, result) {
  const reference = await getQueueItemReference(itemId);
  if (!reference) {
    return;
  }
  const { state, index } = reference;
  const entry = state.queue[index];
  const filename = result.filename || entry.filename || 'download.md';
  state.queue.splice(index, 1);
  state.ready.push({
    id: entry.id,
    url: entry.url,
    filename,
    markdown: result.markdown,
    completedAt: Date.now()
  });
  await setState(state);
}

async function extractJobId(response) {
  if (!response.ok) {
    throw await buildError(response);
  }
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    // ignore parse errors and fall through
  }
  const jobId = data?.jobId;
  if (typeof jobId !== 'string' || !jobId) {
    throw new Error('Backend response missing jobId.');
  }
  return jobId;
}

async function submitConversionJob(item) {
  if (item.url.startsWith('file://')) {
    try {
      const allowed = await chrome.extension.isAllowedFileSchemeAccess();
      if (!allowed) {
        throw new Error('Allow access to file URLs in chrome://extensions');
      }
    } catch (error) {
      if (error instanceof Error) {
        throw error;
      }
      throw new Error('Unable to verify file URL permissions');
    }

    const pdfResponse = await fetch(item.url);
    if (!pdfResponse.ok) {
      throw await buildError(pdfResponse);
    }

    const pdfBlob = await pdfResponse.blob();
    if (!pdfBlob || pdfBlob.size === 0) {
      throw new Error('Received empty PDF when attempting upload.');
    }

    const formData = new FormData();
    formData.append('file', pdfBlob, derivePdfUploadName(item.url));
    if (item.filename) {
      formData.append('filename', item.filename);
    }

    const response = await fetch(`${API_BASE_URL}/${item.contentType.endpoint}`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
      },
      body: formData,
    });

    return await extractJobId(response);
  }

  const payload = {
    url: item.url,
    filename: item.filename,
    cookies: item.cookies,
    html: item.html,
  };

  const response = await fetch(`${API_BASE_URL}/${item.contentType.endpoint}`, {
    method: 'POST',
    headers: REQUEST_HEADERS,
    body: JSON.stringify(payload),
  });

  return await extractJobId(response);
}

async function requestJobStatus(jobId) {
  const response = await fetch(`${JOB_STATUS_BASE_URL}/${encodeURIComponent(jobId)}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    cache: 'no-cache',
  });

  if (response.status === 404) {
    throw new Error('Conversion not found on server.');
  }

  if (!response.ok) {
    throw await buildError(response);
  }

  const data = await response.json();
  if (!data || typeof data.status !== 'string') {
    throw new Error('Invalid job status response from server.');
  }
  return data;
}

async function pollJobUntilComplete(itemId, jobId) {
  let delay = JOB_POLL_INTERVAL_MS;
  while (true) {
    const reference = await getQueueItemReference(itemId);
    if (!reference) {
      return;
    }

    let statusData;
    try {
      statusData = await requestJobStatus(jobId);
    } catch (error) {
      console.error('Job status request failed', error);
      await sleep(delay);
      delay = Math.min(delay + 2_000, JOB_POLL_MAX_INTERVAL_MS);
      continue;
    }

    const status = statusData.status;
    if (status === 'ready') {
      const markdown = statusData.markdown;
      if (typeof markdown !== 'string' || !markdown) {
        await markQueueItemError(itemId, 'Backend returned an empty document.');
        return;
      }
      await moveQueueItemToReady(itemId, statusData);
      return;
    }

    if (status === 'error') {
      await markQueueItemError(itemId, statusData.error || 'Conversion failed');
      return;
    }

    await updateQueueItemJobStatus(itemId, status);
    await sleep(delay);
    delay = Math.min(delay + 2_000, JOB_POLL_MAX_INTERVAL_MS);
  }
}

async function resumeProcessingJobs() {
  const state = await getState();
  for (const entry of state.queue || []) {
    if (entry?.status === 'processing' && entry.jobId) {
      trackJobPoll(entry.jobId, () => pollJobUntilComplete(entry.id, entry.jobId));
    }
  }
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


async function enqueueItem({ type, url, cookies, contentType, filename, html }) {
  if (!url) {
    throw new Error('Missing URL');
  }
  const state = await getState();
  const id = createId();
  state.queue.push({
    id,
    url,
    contentType,
    cookies,
    filename,
    html,
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
  delete item.jobId;
  delete item.jobStatus;
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
    await resumeProcessingJobs();
    while (true) {
      const state = await getState();
      const index = state.queue.findIndex((item) => item.status === 'pending');
      if (index === -1) {
        break;
      }

      const item = state.queue[index];

      if (item.jobId) {
        continue;
      }

      try {
        const jobId = await submitConversionJob(item);
        await storeQueueItemJobId(item.id, jobId);
        const updated = await setQueueItemProcessing(item.id);
        if (!updated) {
          continue;
        }
        await trackJobPoll(jobId, () => pollJobUntilComplete(item.id, jobId));
      } catch (error) {
        console.error('Queue item failed', error);
        await markQueueItemError(item.id, error instanceof Error ? error.message : 'Conversion failed');
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



function derivePdfUploadName(url) {
  try {
    const parsed = new URL(url);
    const raw = decodeURIComponent(parsed.pathname.split('/').pop() || '');
    if (raw) {
      return raw.endsWith('.pdf') ? raw : `${raw}.pdf`;
    }
  } catch (error) {
    // fall through to string parsing below
  }

  const fallback = url.split('/').pop() || 'document.pdf';
  if (/\.pdf$/i.test(fallback)) {
    return fallback;
  }
  return `${fallback || 'document'}.pdf`;
}


async function buildError(response) {
  let detail = `HTTP ${response.status}`;
  try {
    const cloned = response.clone();
    const data = await cloned.json();
    if (data?.detail) {
      if (typeof data.detail === 'string') {
        detail = data.detail;
      } else {
        detail = JSON.stringify(data.detail);
      }
    }
  } catch (err) {
    const text = await response.text();
    if (text) {
      detail = text;
    }
  }
  return new Error(detail);
}
