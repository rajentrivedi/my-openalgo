document.getElementById('exportBtn').addEventListener('click', async () => {
  const statusEl = document.getElementById('status');
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  if (!tab || !tab.url || !tab.url.includes('tradingview.com')) {
    statusEl.textContent = 'Please open a TradingView chart tab.';
    return;
  }

  statusEl.textContent = 'Scanning...';

  const trySend = () => new Promise((resolve) => {
    chrome.tabs.sendMessage(tab.id, { action: 'exportTrades' }, (response) => {
      resolve({ response, error: chrome.runtime.lastError });
    });
  });

  let { response, error } = await trySend();

  // If the content script isn't there, reload the tab and retry once.
  if (error) {
    statusEl.textContent = 'Reloading page...';
    await chrome.tabs.reload(tab.id);

    // Wait for the page to finish loading
    await new Promise((resolve) => {
      const listener = (updatedTabId, info) => {
        if (updatedTabId === tab.id && info.status === 'complete') {
          chrome.tabs.onUpdated.removeListener(listener);
          resolve();
        }
      };
      chrome.tabs.onUpdated.addListener(listener);
    });

    // Give TradingView a moment to render its UI
    await new Promise(r => setTimeout(r, 3000));

    ({ response, error } = await trySend());
  }

  if (error) {
    statusEl.textContent = 'Error: ' + error.message +
      ' — make sure the "List of Trades" tab is open in the Strategy Tester.';
    return;
  }

  if (response && response.success) {
    statusEl.textContent = `Exported ${response.count} trades.`;
  } else {
    statusEl.textContent = response ? response.error : 'Failed to export.';
  }
});