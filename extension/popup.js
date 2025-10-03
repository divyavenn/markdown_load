import { ContentTypes, contentTypes } from './content-types.js';

const STATE_KEY = 'markdownLoadState';

const queueList = document.getElementById('queue-list');
const readyList = document.getElementById('ready-list');
const filenameInput = document.getElementById('filename');
const queueButton = document.getElementById('queue');
const statusLabel = document.getElementById('status');

let activeTab = null;
let activeContentType = null;
let queueDisabled = false;
let statusTimer = null;
const defaultStatus = 'Ready when you are :)';

if (statusLabel) {
  statusLabel.textContent = defaultStatus;
}

function slugify(text) {
  return (text || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'download';
}

function normaliseFilename(name, fallback) {
  let cleaned = '';
  if (typeof name === 'string') {
    cleaned = name.trim();
  } else if (name != null) {
    cleaned = String(name).trim();
  }

  let base = cleaned;
  if (!base) {
    if (typeof fallback === 'string') {
      base = fallback.trim();
    } else if (fallback != null) {
      base = String(fallback).trim();
    }
  }

  if (!base) {
    base = 'download';
  }

  const lower = base.toLowerCase();
  return lower.endsWith('.md') ? base : `${base}.md`;
}

function setStatus(message) {
  if (statusTimer) {
    clearTimeout(statusTimer);
    statusTimer = null;
  }
  statusLabel.textContent = message;
  if (message !== defaultStatus) {
    statusTimer = setTimeout(() => {
      statusLabel.textContent = defaultStatus;
      statusTimer = null;
    }, 3000);
  }
}


function sendMessage(payload) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(payload, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!response) {
        reject(new Error('No response from background script.'));
        return;
      }
      if (response.error) {
        reject(new Error(response.error));
        return;
      }
      resolve(response);
    });
  });
}

function renderQueue(items) {
  queueList.innerHTML = '';
  if (!items.length) {
    return;
  }
  queueList.style.display = 'flex';

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'list-item fade';

    const deleteButton = document.createElement('button');
    deleteButton.className = 'icon-button';
    deleteButton.title = 'Remove from list';
    deleteButton.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640" aria-hidden="true"><path d="M232.7 69.9L224 96L128 96C110.3 96 96 110.3 96 128C96 145.7 110.3 160 128 160L512 160C529.7 160 544 145.7 544 128C544 110.3 529.7 96 512 96L416 96L407.3 69.9C402.9 56.8 390.7 48 376.9 48L263.1 48C249.3 48 237.1 56.8 232.7 69.9zM512 208L128 208L149.1 531.1C150.7 556.4 171.7 576 197 576L443 576C468.3 576 489.3 556.4 490.9 531.1L512 208z" /></svg>';
    deleteButton.addEventListener('click', () => handleRemove(item.id));
    li.appendChild(deleteButton);

    const content = document.createElement('span');
    content.className = 'filename';
    content.textContent = item.filename;
    li.appendChild(content);

    const spinner = document.createElement('div');
    spinner.className = 'spinner';
    li.appendChild(spinner);


    queueList.appendChild(li);
  }
}

function renderReady(items) {
  readyList.innerHTML = '';
  if (!items.length) {
    return;
  }
  readyList.style.display = 'flex';

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'list-item fade';

    const deleteButton = document.createElement('button');
    deleteButton.className = 'icon-button';
    deleteButton.title = 'Remove from list';
    deleteButton.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640"><!--!Font Awesome Free v7.1.0 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free Copyright 2025 Fonticons, Inc.--><path d="M183.1 137.4C170.6 124.9 150.3 124.9 137.8 137.4C125.3 149.9 125.3 170.2 137.8 182.7L275.2 320L137.9 457.4C125.4 469.9 125.4 490.2 137.9 502.7C150.4 515.2 170.7 515.2 183.2 502.7L320.5 365.3L457.9 502.6C470.4 515.1 490.7 515.1 503.2 502.6C515.7 490.1 515.7 469.8 503.2 457.3L365.8 320L503.1 182.6C515.6 170.1 515.6 149.8 503.1 137.3C490.6 124.8 470.3 124.8 457.8 137.3L320.5 274.7L183.1 137.4z"/></svg>';
    deleteButton.addEventListener('click', () => handleRemoveReady(item.id));
    li.appendChild(deleteButton);

    const filename = document.createElement('span');
    filename.className = 'filename';
    filename.textContent = item.filename;
    li.appendChild(filename);

    const button = document.createElement('button');
    button.className = 'icon-button';
    button.title = 'Download markdown';
    button.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 640" aria-hidden="true"><path d="M320 64C178.6 64 64 178.6 64 320C64 461.4 178.6 576 320 576C461.4 576 576 461.4 576 320C576 178.6 461.4 64 320 64zM308.7 451.3L204.7 347.3C200.1 342.7 198.8 335.8 201.2 329.9C203.6 324 209.5 320 216 320L272 320L272 224C272 206.3 286.3 192 304 192L336 192C353.7 192 368 206.3 368 224L368 320L424 320C430.5 320 436.3 323.9 438.8 329.9C441.3 335.9 439.9 342.8 435.3 347.3L331.3 451.3C325.1 457.5 314.9 457.5 308.7 451.3z" /></svg>';
    button.addEventListener('click', () => handleDownload(item.id));
    li.appendChild(button);

    readyList.appendChild(li);
  }
}

async function handleRetry(id) {
  try {
    await sendMessage({ type: 'retry', id });
    setStatus('Retrying…');
  } catch (error) {
    setStatus(error.message || 'Retry failed.');
  }
}

async function handleRemove(id) {
  try {
    await sendMessage({ type: 'removeQueue', id });
    setStatus('removed from queue');
  } catch (error) {
    setStatus(error.message || 'Remove failed.');
  }
}

async function handleDownload(id) {
  try {
    await sendMessage({ type: 'downloadReady', id });
    setStatus('downloading');
  } catch (error) {
    setStatus(error.message || 'Download failed.');
  }
}

async function handleRemoveReady(id) {
  try {
    await sendMessage({ type: 'removeReady', id });
    setStatus('removed download');
  } catch (error) {
    setStatus(error.message || 'Remove failed.');
  }
}

async function refreshState() {
  const state = await new Promise((resolve) => {
    chrome.storage.local.get(STATE_KEY, (result) => {
      resolve(result[STATE_KEY] || { queue: [], ready: [] });
    });
  });
  renderQueue(state.queue || []);
  renderReady(state.ready || []);
}


async function getCookieValue(url, name) {
  return new Promise((resolve) => {
    chrome.cookies.get({ url, name }, (cookie) => {
      if (chrome.runtime.lastError) {
        console.warn('Cookie lookup failed', chrome.runtime.lastError.message);
        resolve(null);
        return;
      }
      resolve(cookie ? cookie.value : null);
    });
  });
}

// this function doesn't work but would be great if it did 
async function getHostCookies(url, host) {
  const topLevelSite = new URL(url).origin;

  // Ask for substack.com cookies scoped to this tab’s partition
  const cookies = await chrome.cookies.getAll({
    domain: host,
    partitionKey: { topLevelSite }
  });

  return cookies;
}

async function getAllCookies(tabURL) {
  return new Promise((resolve) => {
    chrome.cookies.getAll({ url : tabURL} , (cookies) => {
      if (chrome.runtime.lastError) {
        console.warn('Cookie lookup failed', chrome.runtime.lastError.message);
        resolve({});
        return;
      }

      const lookup = Object.create(null);
      for (const cookie of cookies || []) {
        if (cookie?.name && cookie.value !== undefined) {
          lookup[cookie.name] = cookie.value;
        }
      }

      resolve(lookup);
    });
  });
}

function disableQueue(message) {
  queueDisabled = true;
  queueButton.disabled = true;
  if (filenameInput) {
    filenameInput.value = '';
  }
  setStatus(message);
}
async function prepareUI(url, activeContentType) {
  const required = activeContentType.requiredCookies || [];
  let necessaryCookies = {};

  // get cookies again in case tab is refreshed or user signed out.
  const allCookies = await getAllCookies(url);
    for (const name of required) {
      const value = allCookies[name];
      if (value) {
        necessaryCookies[name] = value;
      }
    }

  const missing = required.filter((cookie) => !necessaryCookies[cookie]);

  if (missing.length) {
    disableQueue(activeContentType.errorMessage || 'try logging in first!');
    return;
  }

  await sendMessage({
    type: 'enqueue',
    url: activeTab.url,
    cookies: necessaryCookies,
    contentType: activeContentType,
    filename: filenameInput.value,
  });
  setStatus('downloading! feel free to close this tab and add more :)');
}

function suggestSubstackFilename(url) {
  const slug = slugify(url.split('/').pop() || 'substack-article');
  return `${slug}.md`;
}

function suggestPdfFilename(url) {
  try {
    const parsed = new URL(url);
    const base = parsed.pathname.split('/').pop() || 'document.pdf';
    const name = base.replace(/\.pdf$/i, '') || 'document';
    return `${slugify(name)}.md`;
  } catch (error) {
    const fallback = url.split('/').pop() || 'document.pdf';
    const name = fallback.replace(/\.pdf$/i, '') || 'document';
    return `${slugify(name)}.md`;
  }
}


function suggestTweetFilename(url) {
  const match = url.match(ContentTypes.TWITTER.regex);
  const handle = match ? match[1] : 'thread';
  const tweetId = match ? match[2] : 'tweet';
  const slug = slugify(`${handle}-${tweetId}`);
  return `${slug}.md`;
}

async function detectContentType(url) {
  if (typeof url !== 'string' || !url) {
    return null;
  }

  for (const type of contentTypes) {
    if (type?.regex && type.regex.test(url)) return type;
  }
  return null;
}


async function prepareForContentType(type, url) {
  try {
    await prepareUI(url, type);
  } catch (error) {
    console.warn('Preparation failed', error);
    disableQueue('Unable to prepare this page for download.');
    throw error;
  }
}

async function initialise() {
  activeTab = await new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      resolve(tabs[0] || null);
    });
  });

  await refreshState();

  const url = activeTab?.url || '';
  activeContentType= await detectContentType(url);

  if (!activeContentType) {
    disableQueue('nothing to download here!');
    return;
  }

  queueDisabled = false;
  queueButton.disabled = false;

  let slug = "download.md";
    switch (activeContentType.name) {
      case 'substack':
        slug = suggestSubstackFilename(url);
        break;
      case 'twitter':
        slug = suggestTweetFilename(url);
        break;
      case 'pdf-remote':
      case 'pdf-local':
        slug = suggestPdfFilename(url);
        break;
    }
    const fname = normaliseFilename(filenameInput.value, slug);
    if (!filenameInput.value.trim()) {
        filenameInput.value = normaliseFilename('', fname);
    }
  return;
}

queueButton.addEventListener('click', async () => {
  const state = await new Promise((resolve) => {
    chrome.storage.local.get(STATE_KEY, (result) => {
      resolve(result[STATE_KEY] || { queue: [], ready: [] });
    });
  });

  if (queueDisabled) {
    setStatus('nothing to download here!');
    return;
  }

  if (!activeTab || !activeTab.url || !activeContentType) {
    setStatus('nothing to download here!');
    return;
  }

  const currentUrl = activeTab.url;
  const alreadyQueued = (state.queue || []).some((item) => item.url === currentUrl);
  const alreadyReady = (state.ready || []).some((item) => item.url === currentUrl);

  if (alreadyQueued) {
    setStatus('already queued!');
    return;
  }

  if (alreadyReady) {
    setStatus('aleady ready to download!');
    return;
  }

  queueButton.disabled = true;

  try {
    await prepareForContentType(activeContentType, activeTab.url);
  } catch (error) {
    setStatus(error.message || 'Failed to queue item.');
  } finally {
    queueButton.disabled = false;
  }
});

  chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== 'local' || !changes[STATE_KEY]) {
    return;
  }
  const next = changes[STATE_KEY].newValue || { queue: [], ready: [] };
  renderQueue(next.queue || []);
  renderReady(next.ready || []);
});


initialise();
