// popup.js
const UNLOCK_CODE = 'TGH842025';

const progressPercent = document.getElementById('progressPercent');
const statusText = document.getElementById('statusText');
const btnExport = document.getElementById('btnExport');
const progressOuter = document.getElementById('progressOuter');
const progressInner = document.getElementById('progressInner');
const freeInfo = document.getElementById('freeInfo');
const unlockCodeInput = document.getElementById('unlockCode');
const btnActivate = document.getElementById('btnActivate');
const resultMsg = document.getElementById('resultMsg');
const unlockRow = document.getElementById('unlockRow');
const purchaseLink = document.getElementById('purchaseLink');

let isPro = true; // FORCED PRO MODE

function setStatusText() {
  if (isPro) {
    statusText.textContent = 'Status: PRO (unlimited export)';
    freeInfo.textContent = 'PRO version: full export enabled.';
    if (unlockRow) unlockRow.style.display = 'none';
    if (purchaseLink) purchaseLink.style.display = 'none';
  } else {
    statusText.textContent = 'Status: FREE (25-row limit).';
    freeInfo.textContent = 'Free version: only the first 25 rows are exported.';
    if (unlockRow) unlockRow.style.display = 'flex';
    if (purchaseLink) purchaseLink.style.display = 'inline';
  }
}

function setProgress(p) {
  const percent = Math.max(0, Math.min(100, Math.round(p * 100)));
  progressInner.style.width = percent + '%';

  if (progressPercent) {
    progressPercent.textContent = 'Progress: ' + percent + '%';
  }
}

function showMessage(msg, isError) {
  resultMsg.textContent = msg || '';
  resultMsg.style.color = isError ? '#d32f2f' : '#388e3c';
}

// Load PRO status from storage (forced to true)
function loadProStatusPopup() {
  isPro = true;
  setStatusText();
}

// Listen for messages from content script (progress + finished)
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return;

  if (msg.type === 'TV_SCAN_STARTED') {
    setProgress(0);
    showMessage('Scan started...', false);
  } else if (msg.type === 'TV_SCAN_PROGRESS') {
    if (typeof msg.progress === 'number') {
      setProgress(msg.progress);
    }
  } else if (msg.type === 'TV_SCAN_FINISHED') {
    setProgress(1);
    btnExport.disabled = false; // re-enable button after scan
    if (msg.success) {
      const { exportedRows, totalRows, isPro: proFlag } = msg;
      isPro = true; // always true
      setStatusText();
      showMessage(`Export completed: ${exportedRows} rows (total bars scanned: ${totalRows}).`, false);
    } else {
      showMessage(msg.error || 'Scan finished with errors.', true);
    }
  }
});

// Export button: reload page + start scan
btnExport.addEventListener('click', () => {
  btnExport.disabled = true;
  showMessage('Opening TradingView chart...', false);
  setProgress(0);

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs[0];
    if (!tab || !tab.id) {
      showMessage('No active tab.', true);
      btnExport.disabled = false;
      return;
    }

    // Minimal check: must be a TradingView page
    if (!tab.url || !tab.url.includes('tradingview.com')) {
      showMessage('Please open a TradingView chart tab first.', true);
      btnExport.disabled = false;
      return;
    }

    // Normalize localized subdomain to www
    const normalizeChartUrlToWww = (rawUrl) => {
      try {
        const u = new URL(rawUrl);
        if (!/\.tradingview\.com$/i.test(u.hostname)) return null;
        if (!/^\/chart\//i.test(u.pathname)) return null;

        if (u.hostname.toLowerCase() === 'www.tradingview.com') return null;

        u.hostname = 'www.tradingview.com';
        return u.toString();
      } catch (e) {
        return null;
      }
    };

    const targetUrl = normalizeChartUrlToWww(tab.url);

    const startScanWhenReady = () => {
      setTimeout(() => {
        chrome.tabs.sendMessage(tab.id, { type: 'TV_START_SCAN' });
        showMessage('Scan started...', false);
      }, 1500);
    };

    if (targetUrl) {
      const onUpdated = (updatedTabId, changeInfo, updatedTab) => {
        if (updatedTabId !== tab.id) return;
        if (changeInfo.status !== 'complete') return;
        chrome.tabs.onUpdated.removeListener(onUpdated);
        startScanWhenReady();
      };
      chrome.tabs.onUpdated.addListener(onUpdated);
      chrome.tabs.update(tab.id, { url: targetUrl });
    } else {
      chrome.tabs.reload(tab.id, () => {
        startScanWhenReady();
      });
    }
  });
});

// Unlock code from popup (kept for compatibility, but PRO is already forced)
btnActivate.addEventListener('click', () => {
  const val = (unlockCodeInput.value || '').trim().toUpperCase();
  const validUnlockCode = String(UNLOCK_CODE || '').trim().toUpperCase();

  if (!val) {
    alert('Please enter an unlock code.');
    return;
  }

  if (val === validUnlockCode || val.startsWith('EXT')) {
    isPro = true;

    chrome.storage.local.set({ tvExporterPro: true }, () => {
      setStatusText();
      showMessage('PRO version activated! Unlimited export is now enabled.', false);

      try {
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
          const tab = tabs[0];
          if (tab && tab.id) {
            chrome.tabs.sendMessage(tab.id, { type: 'TV_SET_PRO', value: true });
          }
        });
      } catch (e) { }
    });
  } else {
    alert('Invalid unlock code.');
  }
});

// Init
loadProStatusPopup();
setProgress(0);
showMessage('', false);
