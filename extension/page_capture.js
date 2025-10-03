chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'capture-html') {
    try {
      const html = document.documentElement?.outerHTML || '';
      sendResponse({ html });
    } catch (error) {
      console.warn('HTML capture failed', error);
      sendResponse({ html: null, error: error?.message || String(error) });
    }
    return true;
  }
  return undefined;
});

