// content.js

// --- CONFIG PRO/FREE ---

const UNLOCK_CODE = 'TGH842025';
let isPro = true; // FORCED PRO MODE
let scanInProgress = false;

// --- UTILITIES ---

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// clean Volume (remove weird characters before K/M/B/T)
function cleanVolume(vol) {
  if (!vol) return '';
  return vol.replace(/[^0-9.,\-KMBT]/gi, '');
}

// --- TIMESTAMP CAPTURE (passive TradingView WebSocket observer) ---

let capturedTvBars = [];

window.addEventListener('message', (event) => {
  if (event.source !== window || !event.data) return;
  if (event.data.source !== 'tv-exporter-page-hook' || event.data.type !== 'TV_EXPORTER_WS_BARS') return;
  if (!Array.isArray(event.data.bars)) return;

  capturedTvBars = event.data.bars
    .filter(b => b && Number.isFinite(Number(b.t)) && Number.isFinite(Number(b.o)) &&
                 Number.isFinite(Number(b.h)) && Number.isFinite(Number(b.l)) && Number.isFinite(Number(b.c)))
    .map(b => ({
      t: Number(b.t), o: Number(b.o), h: Number(b.h), l: Number(b.l), c: Number(b.c),
      v: b.v == null || !Number.isFinite(Number(b.v)) ? null : Number(b.v)
    }))
    .sort((a, b) => a.t - b.t);

  console.log(`[TV-EXPORTER] captured ${capturedTvBars.length} TradingView bars with timestamps.`);
});

function requestCapturedTvBars() {
  try {
    window.postMessage({ source: 'tv-exporter-content', type: 'TV_EXPORTER_REQUEST_WS_BARS' }, '*');
  } catch (_) { }
}

function parseLegendNumber(value) {
  if (value == null) return null;
  let s = String(value).trim().replace(/\u2212/g, '-').replace(/\s/g, '');
  if (!s || /^(n\/a|na|—|-)$/i.test(s)) return null;

  let mult = 1;
  const suffix = s.match(/([KMBT])$/i);
  if (suffix) {
    const u = suffix[1].toUpperCase();
    mult = u === 'K' ? 1e3 : u === 'M' ? 1e6 : u === 'B' ? 1e9 : 1e12;
    s = s.slice(0, -1);
  }

  if (s.includes(',') && s.includes('.')) {
    s = s.replace(/,/g, '');
  } else if (s.includes(',') && !s.includes('.')) {
    const parts = s.split(',');
    if (parts.length === 2 && parts[1].length <= 6) s = parts[0] + '.' + parts[1];
    else s = s.replace(/,/g, '');
  }

  const n = Number(s.replace(/[^0-9.+\-eE]/g, ''));
  return Number.isFinite(n) ? n * mult : null;
}

function approxEqualLegendToRaw(legendValue, rawValue) {
  const a = parseLegendNumber(legendValue);
  const b = Number(rawValue);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;

  const txt = String(legendValue || '').replace(/,/g, '');
  const decimalMatch = txt.match(/\.(\d+)/);
  const decimals = decimalMatch ? decimalMatch[1].length : 0;
  const tolerance = Math.max(Math.pow(10, -decimals) * 0.51, Math.abs(b) * 1e-9, 1e-10);
  return Math.abs(a - b) <= tolerance;
}

function barMatchesLegend(bar, row) {
  return approxEqualLegendToRaw(row.O, bar.o) &&
         approxEqualLegendToRaw(row.H, bar.h) &&
         approxEqualLegendToRaw(row.L, bar.l) &&
         approxEqualLegendToRaw(row.C, bar.c);
}

function attachTimestampsToRows(rows, bars) {
  if (!Array.isArray(rows) || !rows.length || !Array.isArray(bars) || !bars.length) return 0;

  const starts = [];
  for (let i = 0; i < bars.length; i++) {
    if (barMatchesLegend(bars[i], rows[0])) starts.push(i);
  }
  if (!starts.length) return 0;

  let best = null;
  const maxGap = 12;

  for (const start of starts) {
    let bi = start;
    let matched = 0;
    const mapping = new Array(rows.length).fill(-1);

    for (let ri = 0; ri < rows.length; ri++) {
      let found = -1;
      const end = Math.min(bars.length, bi + maxGap + 1);
      for (let j = bi; j < end; j++) {
        if (barMatchesLegend(bars[j], rows[ri])) {
          found = j;
          break;
        }
      }
      if (found >= 0) {
        mapping[ri] = found;
        matched++;
        bi = found + 1;
      }
    }

    if (!best || matched > best.matched || (matched === best.matched && start > best.start)) {
      best = { start, matched, mapping };
    }
  }

  if (!best || !best.matched) return 0;
  best.mapping.forEach((barIndex, rowIndex) => {
    if (barIndex >= 0) rows[rowIndex].timestamp = bars[barIndex].t;
  });
  return best.matched;
}

// Format unix seconds → IST string (Asia/Kolkata)
function formatIstTimestamp(unixSeconds) {
  const n = Number(unixSeconds);
  if (!Number.isFinite(n)) return '';
  const d = new Date(n * 1000);
  if (Number.isNaN(d.getTime())) return '';
  try {
    return d.toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    }).replace(/\//g, '-');
  } catch (e) {
    // Fallback: manual IST offset (+5:30)
    const ist = new Date(d.getTime() + 5.5 * 60 * 60 * 1000);
    const pad = x => String(x).padStart(2, '0');
    return `${ist.getUTCFullYear()}-${pad(ist.getUTCMonth() + 1)}-${pad(ist.getUTCDate())} ${pad(ist.getUTCHours())}:${pad(ist.getUTCMinutes())}:${pad(ist.getUTCSeconds())}`;
  }
}

requestCapturedTvBars();

// --- SYMBOL PAGE → CHART REDIRECT ---

function isSymbolPage() {
  return /^\/symbols\/[^/]+\/?$/i.test(window.location.pathname);
}

function getChartUrlFromSymbolPage() {
  // /symbols/XETR-DAX/  →  /chart/?symbol=XETR%3ADAX
  const m = window.location.pathname.match(/^\/symbols\/([^/]+)/i);
  if (!m) return null;
  const raw = m[1]; // e.g. "XETR-DAX"
  const idx = raw.indexOf('-');
  if (idx < 0) return null;
  const exchange = raw.substring(0, idx);
  const ticker = raw.substring(idx + 1);
  return '/chart/?symbol=' + encodeURIComponent(exchange + ':' + ticker);
}

function isChartPage() {
  return window.location.pathname.startsWith('/chart');
}

// Read O/H/L/C/Vol from TradingView legend
function getCurrentOHLCFromLegend() {
  // Prefer stable attributes; hashed class names change frequently.
  const legendItem =
    document.querySelector('[data-qa-id="legend-series-item"]') ||
    document.querySelector('[data-qa-id="legend-series"]') ||
    document.querySelector('.legendMainSourceWrapper-l31H9iuA');

  const wrapper =
    (legendItem && legendItem.querySelector('[class*="valuesWrapper"]')) ||
    document.querySelector('[class*="valuesWrapper"]');

  if (!wrapper) return null;

  const getVal = (titles) => {
    const candidates = Array.isArray(titles) ? titles : [titles];
    for (const t of candidates) {
      const node = wrapper.querySelector(`[data-test-id-value-title="${t}"]`);
      if (!node) continue;
      const valEl = node.querySelector('[class*="valueValue"]') || node.querySelector('.valueValue-l31H9iuA');
      const txt = valEl ? valEl.textContent.trim() : '';
      if (txt) return txt;
    }
    return null;
  };

  // Works across locales (it/en/de/...) – TradingView uses short keys in many cases.
  const O = getVal(['O', 'Open', 'O - Open', 'O - Aper.']);
  const H = getVal(['H', 'High', 'H - High', 'H - Max.']);
  const L = getVal(['L', 'Low', 'L - Low', 'L - Min.']);
  const C = getVal(['C', 'Close', 'C - Close', 'C - Chius.']);
  const VolRaw = getVal(['Vol', 'Volume', 'V']);
  const Vol = cleanVolume(VolRaw);

  if (!O || !H || !L || !C) return null;
  return { O, H, L, C, Vol };
}

function downloadCsv(filename, csv) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Simulate "entering" the chart with a real mouse
async function activateChartHover(canvas) {
  const rect = canvas.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height * 0.4; // 40% from top

  const events = ['mouseover', 'mouseenter', 'mousedown', 'mouseup', 'click', 'mousemove'];

  for (const type of events) {
    const evt = new MouseEvent(type, {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX: x,
      clientY: y
    });
    canvas.dispatchEvent(evt);
    await sleep(20);
  }
}

// Read table title from legend
function getTableTitle() {
  try {
    const titlesWrapper =
      document.querySelector('[data-qa-id="legend-titles"]') ||
      document.querySelector('[data-qa-id="legend-series-item"] [data-qa-id="legend-titles"]') ||
      document.querySelector('.titlesWrapper-l31H9iuA');
    if (!titlesWrapper) return '';

    const symbolEl =
      titlesWrapper.querySelector('[data-qa-id="legend-source-title"] button') ||
      titlesWrapper.querySelector('[data-qa-id="legend-source-title"] .title-l31H9iuA') ||
      titlesWrapper.querySelector('button[aria-label]');

    const intervalEl =
      titlesWrapper.querySelector('[data-qa-id="legend-source-interval"] button') ||
      titlesWrapper.querySelector('[data-qa-id="legend-source-interval"] .title-l31H9iuA');

    const exchangeEl =
      titlesWrapper.querySelector('[data-qa-id="legend-source-exchange"] .title-l31H9iuA') ||
      titlesWrapper.querySelector('[data-qa-id="legend-source-exchange"]');

    const parts = [];
    if (symbolEl && symbolEl.textContent) parts.push(symbolEl.textContent.trim());
    if (intervalEl && intervalEl.textContent) parts.push(intervalEl.textContent.trim());
    if (exchangeEl && exchangeEl.textContent) parts.push(exchangeEl.textContent.trim());

    return parts.join(' - ');
  } catch (e) {
    console.warn('[TV-EXPORTER] getTableTitle error', e);
    return '';
  }
}

// --- FREE / PRO STORAGE ---

function loadProStatus(callback) {
  // Force PRO mode always
  isPro = true;
  console.log('[TV-EXPORTER] PRO status forced to true');
  if (callback) callback();
}

function saveProStatus() {
  if (!chrome || !chrome.storage || !chrome.storage.local) return;
  chrome.storage.local.set({ tvExporterPro: isPro });
}

// --- SCAN LOGIC ---

async function scanChartAndExport(onProgress) {
  // If on a symbol page, redirect to chart page instead of scanning
  if (isSymbolPage()) {
    const chartUrl = getChartUrlFromSymbolPage();
    if (chartUrl) {
      window.location.href = chartUrl + '&tvExportAutoScan=1';
      return;
    }
  }

  if (scanInProgress) {
    console.warn('[TV-EXPORTER] scan already in progress');
    return;
  }
  scanInProgress = true;

  const reportProgress = (p) => {
    if (typeof onProgress === 'function') onProgress(p);
    try {
      if (chrome && chrome.runtime && chrome.runtime.sendMessage) {
        chrome.runtime.sendMessage({ type: 'TV_SCAN_PROGRESS', progress: p });
      }
    } catch (e) {
      // ignore
    }
  };

  // notify popup that scan started
  try {
    if (chrome && chrome.runtime && chrome.runtime.sendMessage) {
      chrome.runtime.sendMessage({ type: 'TV_SCAN_STARTED' });
    }
  } catch (e) { }

  // 1) Find interactive chart canvas
  const canvas =
    document.querySelector('canvas[data-qa-id="pane-top-canvas"]') ||
    document.querySelector('canvas[data-name="pane-top-canvas"]') ||
    document.querySelector('canvas[data-name="pane-canvas"]') ||
    document.querySelector('.chart-container canvas');

  if (!canvas) {
    alert('Chart canvas not found.\nMake sure you are on a TradingView chart page.');
    console.warn('[TV-EXPORTER] canvas not found');
    scanInProgress = false;
    return;
  }

  const rect = canvas.getBoundingClientRect();

  // 2) Activate chart by simulating mouse entering
  await activateChartHover(canvas);
  await sleep(200);

  // 3) Check legend works for at least one bar
  const testOhlc = getCurrentOHLCFromLegend();
  if (!testOhlc) {
    alert('OHLC legend not found.\nMove your mouse over a bar and try again.');
    console.warn('[TV-EXPORTER] legend not found after activateChartHover');
    scanInProgress = false;
    return;
  }

  // Table title (symbol / timeframe / exchange)
  const tableTitle = getTableTitle();

  // 4) Horizontal scan
  const maxSteps = 400;
  const steps = Math.min(maxSteps, Math.floor(rect.width));

  if (steps < 20) {
    alert('Chart is too small for a meaningful scan.');
    scanInProgress = false;
    return;
  }

  const rows = [];
  let lastKey = null;

  for (let i = 2; i < steps - 2; i++) {
    const x = rect.left + (rect.width * i) / steps;
    const y = rect.top + rect.height * 0.4; // good vertical line

    const evt = new MouseEvent('mousemove', {
      bubbles: true,
      cancelable: true,
      view: window,
      clientX: x,
      clientY: y
    });

    canvas.dispatchEvent(evt);

    await sleep(35);

    const ohlc = getCurrentOHLCFromLegend();
    if (!ohlc) continue;

    const key = `${ohlc.O}|${ohlc.H}|${ohlc.L}|${ohlc.C}|${ohlc.Vol || ''}`;
    if (key !== lastKey) {
      lastKey = key;
      rows.push(ohlc);
    }

    const progress = (i - 2) / (steps - 4);
    reportProgress(Math.max(0, Math.min(1, progress)));
  }

  if (!rows.length) {
    alert('No data collected.\nTry zooming/scrolling the chart and scan again.');
    console.warn('[TV-EXPORTER] no rows collected');
    scanInProgress = false;
    return;
  }

  // --- NO LIMIT: always export all rows ---
  const effectiveRows = rows;

  // 5) Attach timestamps captured from TradingView's own data stream.
  requestCapturedTvBars();
  await sleep(120);
  const timestampMatches = attachTimestampsToRows(rows, capturedTvBars);
  console.log(`[TV-EXPORTER] timestamp match: ${timestampMatches}/${rows.length} rows.`);

  // 6) Build CSV (comma-delimited, no thousand separators, IST timestamps)
  const stripCommas = (v) => String(v == null ? '' : v).replace(/,/g, '');

  let csv = '';

  // Title as a comment line – safe for most CSV parsers
  if (tableTitle) {
    csv += `# Title: ${String(tableTitle).replace(/,/g, '')}\n`;
  }

  csv += 'Index,Timestamp IST,Unix Timestamp,Open,High,Low,Close,Volume\n';

  effectiveRows.forEach((r, idx) => {
    const unixTs = Number.isFinite(Number(r.timestamp)) ? Number(r.timestamp) : '';
    const timeText = unixTs !== '' ? formatIstTimestamp(unixTs) : '';
    const open = stripCommas(r.O);
    const high = stripCommas(r.H);
    const low = stripCommas(r.L);
    const close = stripCommas(r.C);
    const volume = stripCommas(r.Vol);

    csv += `${idx + 1},${timeText},${unixTs},${open},${high},${low},${close},${volume}\n`;
  });

  downloadCsv('tradingview_ohlc_scan.csv', csv);
  console.log(`[TV-EXPORTER] exported ${effectiveRows.length} rows (isPro=${isPro})`);

  // final progress 100%
  reportProgress(1);

  // notify popup that scan finished
  try {
    if (chrome && chrome.runtime && chrome.runtime.sendMessage) {
      chrome.runtime.sendMessage({
        type: 'TV_SCAN_FINISHED',
        success: true,
        exportedRows: effectiveRows.length,
        totalRows: rows.length,
        isPro
      });
    }
  } catch (e) { }

  // After export, if FREE, show unlock section (hidden because isPro=true)
  if (!isPro) {
    showUnlockSection();
  }

  scanInProgress = false;
}

// --- UI: UNLOCK SECTION IN PAGE + FLOATING BUTTON ---

let unlockSection = null;

function showUnlockSection() {
  if (!unlockSection) return;
  if (isPro) {
    unlockSection.style.display = 'none';
    return;
  }
  unlockSection.style.display = 'block';
}

function hideUnlockSection() {
  if (!unlockSection) return;
  unlockSection.style.display = 'none';
}

function createUnlockSection() {
  if (unlockSection) return;

  const div = document.createElement('div');
  div.id = 'tv-unlock-section';
  div.innerHTML = `
    <div style="font-size:11px; margin-bottom:4px;">
      Free version: only the first 25 rows are exported.
    </div>
    <div style="display:flex; gap:4px; margin-bottom:4px;">
      <input id="tv-unlock-input" type="text" placeholder="Enter unlock code"
             style="flex:1; padding:2px 4px; font-size:11px; border-radius:3px; border:1px solid #ccc; background:#ffffff; color:#000000;">
      <button id="tv-unlock-send"
              style="padding:2px 6px; font-size:11px; border:none; border-radius:3px; background:#43a047; color:#fff; cursor:pointer;">
        Activate
      </button>
    </div>
    <a href="https://store.payproglobal.com/checkout?products[1][id]=120158" target="_blank"
       style="font-size:11px; color:#bbdefb; text-decoration:underline;">
      Purchase Unlock Code
    </a>
  `;

  Object.assign(div.style, {
    position: 'fixed',
    bottom: '140px',
    right: '20px',
    zIndex: 99999,
    padding: '6px 8px',
    background: 'rgba(33, 33, 33, 0.95)',
    color: '#fff',
    borderRadius: '4px',
    boxShadow: '0 2px 6px rgba(0,0,0,0.4)',
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    width: '260px',
    fontSize: '11px',
    display: 'none'
  });

  document.body.appendChild(div);
  unlockSection = div;

  const input = div.querySelector('#tv-unlock-input');
  const btnSend = div.querySelector('#tv-unlock-send');

  btnSend.addEventListener('click', () => {
    const val = (input.value || '').trim();
    if (!val) {
      alert('Please enter an unlock code.');
      return;
    }
    if (val === UNLOCK_CODE) {
      isPro = true;
      saveProStatus();
      hideUnlockSection();
      alert('PRO version activated! Unlimited export is now enabled.');
      try {
        if (chrome && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({ type: 'TV_SET_PRO', value: true });
        }
      } catch (e) { }
    } else {
      alert('Invalid unlock code.');
    }
  });
}

function createExportButton() {
  if (document.getElementById('tv-export-ohlc-btn')) return;

  const btn = document.createElement('button');
  btn.id = 'tv-export-ohlc-btn';

  btn.innerHTML = `
    <span id="tv-export-label">Export Chart Data → CSV</span>
    <div id="tv-export-progress">
      <div id="tv-export-progress-bar"></div>
    </div>
  `;

  Object.assign(btn.style, {
    position: 'fixed',
    bottom: '90px',
    right: '20px',
    zIndex: 99999,
    padding: '8px 14px 10px 14px',
    background: '#1e88e5',
    color: '#fff',
    borderRadius: '4px',
    border: 'none',
    fontSize: '13px',
    cursor: 'pointer',
    boxShadow: '0 2px 6px rgba(0,0,0,0.3)',
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    overflow: 'hidden'
  });

  const progressContainer = btn.querySelector('#tv-export-progress');
  const progressBar = btn.querySelector('#tv-export-progress-bar');
  const labelSpan = btn.querySelector('#tv-export-label');

  Object.assign(progressContainer.style, {
    position: 'absolute',
    left: '0',
    bottom: '0',
    width: '100%',
    height: '3px',
    background: 'rgba(255,255,255,0.25)'
  });

  Object.assign(progressBar.style, {
    width: '0%',
    height: '100%',
    background: 'rgba(255,255,255,0.9)',
    transition: 'width 0.08s linear'
  });

  btn.addEventListener('click', () => {
    if (btn.dataset.busy === '1') return;

    btn.dataset.busy = '1';
    labelSpan.textContent = 'Scanning...';
    progressBar.style.width = '0%';

    scanChartAndExport((p) => {
      const percent = Math.round(p * 100);
      progressBar.style.width = `${percent}%`;
      labelSpan.textContent = `Scanning... ${percent}%`;
    }).finally(() => {
      btn.dataset.busy = '0';
      labelSpan.textContent = 'Export Chart Data → CSV';
      setTimeout(() => {
        progressBar.style.width = '0%';
      }, 500);
    });
  });

  document.body.appendChild(btn);

  // unlock section in-page
  createUnlockSection();
  if (isPro) hideUnlockSection();
}

// --- MESSAGING: handle popup commands ---

if (chrome && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || !msg.type) return;

    if (msg.type === 'TV_START_SCAN') {
      scanChartAndExport(); // popup will get progress via runtime messages
    } else if (msg.type === 'TV_SET_PRO') {
      if (msg.value === true) {
        isPro = true;
        saveProStatus();
        hideUnlockSection();
      }
    }
  });
}

// --- INIT ---

// Auto-scan: wait for the chart to be ready, then trigger scan
async function waitForChartAndScan() {
  const maxWait = 15000; // 15 seconds max
  const interval = 500;
  let elapsed = 0;

  while (elapsed < maxWait) {
    const canvas =
      document.querySelector('canvas[data-qa-id="pane-top-canvas"]') ||
      document.querySelector('canvas[data-name="pane-top-canvas"]');
    const legend = document.querySelector('[data-qa-id="legend-series-item"]');

    if (canvas && legend) {
      // Clean the autoScan param from URL without reload
      const url = new URL(window.location.href);
      url.searchParams.delete('tvExportAutoScan');
      history.replaceState(null, '', url.toString());

      // Small extra delay to let candles render
      await sleep(2000);

      // Click the export button if it exists
      const btn = document.getElementById('tv-export-ohlc-btn');
      if (btn) btn.click();
      return;
    }

    await sleep(interval);
    elapsed += interval;
  }

  console.warn('[TV-EXPORTER] auto-scan timeout: chart not ready after ' + maxWait + 'ms');
}

function init() {
  loadProStatus(() => {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', createExportButton);
    } else {
      createExportButton();
    }

    // If redirected from a symbol page, auto-start scan
    if (isChartPage() && new URLSearchParams(window.location.search).has('tvExportAutoScan')) {
      waitForChartAndScan();
    }
  });
}

init();
