const STATE_KEY = 'markdownLoadState';

const queueList = document.getElementById('queue-list');
const readyList = document.getElementById('ready-list');
const filenameInput = document.getElementById('filename');
const queueButton = document.getElementById('queue');
const statusLabel = document.getElementById('status');

import { ContentType, contentTypes } from './content-types.js';


let activeTab = null;
let activeContentType = null; // "substack" | "twitter" | null
let queueDisabled = false;
let statusTimer = null;
const defaultStatus = 'Ready when you are :)';

if (statusLabel) {
  statusLabel.textContent = defaultStatus;
}

function slugify(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'substack-article';
}

function normaliseFilename(name) {
  const cleaned = name.trim();
  return cleaned.toLowerCase().endsWith('.md') ? cleaned : `${cleaned}.md`;
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
    setStatus('Removed from queue.');
  } catch (error) {
    setStatus(error.message || 'Remove failed.');
  }
}

async function handleDownload(id) {
  try {
    await sendMessage({ type: 'downloadReady', id });
    setStatus('Download started');
  } catch (error) {
    setStatus(error.message || 'Download failed.');
  }
}

async function handleRemoveReady(id) {
  try {
    await sendMessage({ type: 'removeReady', id });
    setStatus('Removed from downloads.');
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


function disableQueue(message) {
  queueDisabled = true;
  queueButton.disabled = true;
  if (filenameInput) {
    filenameInput.value = '';
  }
  setStatus(message);
}

function detectContentType(url) {
  for (const type in contentTypes) {
    if (type.regex.test(url)) {
      return type;
    }
  }
  return null;
}



async function initialise() {
  activeTab = await new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      resolve(tabs[0] || null);
    });
  });

  await refreshState();

  console.log("State refreshed")

  const url = activeTab?.url || '';
  activeContentType = detectContentType(url);
  activeContentType.handler(url);

  if (!activeContentType) {
    disableQueue('nothing to download here!');
  }

}

queueButton.addEventListener('click', async () => {
  if (queueDisabled) {
    setStatus('Open a supported page to queue it.');
    return;
  }

  if (!activeTab || !activeTab.url || !activeContentType) {
    setStatus('Open a supported page to queue it.');
    return;
  }

  const state = await new Promise((resolve) => {
    chrome.storage.local.get(STATE_KEY, (result) => {
      resolve(result[STATE_KEY] || { queue: [], ready: [] });
    });
  });

  const currentUrl = activeTab.url;
  const alreadyQueued = (state.queue || []).some((item) => item.url === currentUrl);
  const alreadyReady = (state.ready || []).some((item) => item.url === currentUrl);

  if (alreadyQueued) {
    setStatus('already queued!');
    return;
  }

  if (alreadyReady) {
    setStatus('lready ready to download!');
    return;
  }

  queueButton.disabled = true;
  
  if (activeContentType) {
    try {
      queueButton.disabled = true;
      setStatus('Adding article to queue…')
      const slug = activeContentType.fileNameGenerator(activeTab.url);
      const fileName = normaliseFilename(`${slug}.md`);
      filenameInput.value = fileName;
      await sendMessage({ type: 'enqueue', url: activeTab.url, filename: fileName, kind: activeContentType.name });
      setStatus('Queued up download! Feel free to close this tab and add more :)')
    } catch (error) {
      setStatus(error.message || 'Failed to queue item.');
    } finally {
      queueButton.disabled = false;
    }
  } else {
    setStatus('Open a supported page to queue it.');
    queueButton.disabled = true;
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
