// content.js

// ---------- Utility ----------
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// ---------- Open Strategy Tester & List of Trades ----------
async function ensureTradeListOpen() {
  // 1. Open bottom panel if collapsed
  const backtestTab = document.querySelector('[data-qa-id="backtesting"]');
  if (backtestTab && backtestTab.getAttribute('data-active') !== 'true') {
    backtestTab.click();
    await sleep(1500);
  }

  // 2. Click the "List of Trades" tab (or similar)
  const tabs = Array.from(document.querySelectorAll('button, [role="tab"]'));
  const tradesTab = tabs.find(el => {
    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
    return t.includes('list of trades') || t === 'trades';
  });
  if (tradesTab) {
    tradesTab.click();
    await sleep(1200);
  }

  // 3. Wait for the trade table to appear
  const maxWait = 10000;
  let elapsed = 0;
  while (elapsed < maxWait) {
    const tbody = document.querySelector('tbody.ka-tbody');
    if (tbody && tbody.querySelectorAll('tr[data-qa-id^="row-"]').length > 0) {
      return true;
    }
    await sleep(300);
    elapsed += 300;
  }
  return false;
}

// ---------- Find scrollable ancestor ----------
function findScrollable(el) {
  while (el && el !== document.body) {
    const style = window.getComputedStyle(el);
    if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
      return el;
    }
    el = el.parentElement;
  }
  return null;
}

// ---------- Scroll and collect all rows ----------
async function scrapeAllTradeRows() {
  const tbody = document.querySelector('tbody.ka-tbody');
  if (!tbody) throw new Error('Trade list tbody not found.');

  let scroller = findScrollable(tbody);
  if (!scroller) {
    // fallback: try common TradingView class names
    scroller = document.querySelector('[class*="tableWrapper"]') ||
               document.querySelector('[class*="reportContent"]') ||
               tbody.parentElement;
  }

  // Ensure we start at the top
  scroller.scrollTop = 0;
  await sleep(500);

  const seen = new Map(); // key = trade number
  let lastScrollTop = -1;
  let stableCount = 0;

  for (let step = 0; step < 500; step++) {
    // Extract currently rendered rows
    const rows = tbody.querySelectorAll('tr[data-qa-id^="row-"]');
    rows.forEach(row => {
      const tradeNo = row.getAttribute('data-qa-id').replace('row-', '');
      if (!seen.has(tradeNo)) {
        const data = parseTradeRow(row, tradeNo);
        if (data) seen.set(tradeNo, data);
      }
    });

    // Scroll down
    const prevScroll = scroller.scrollTop;
    scroller.scrollTop += scroller.clientHeight * 0.8;
    await sleep(400);

    if (scroller.scrollTop === prevScroll) {
      stableCount++;
      if (stableCount > 3) break; // no more scrolling possible
    } else {
      stableCount = 0;
    }
  }

  const sorted = Array.from(seen.values()).sort((a, b) => Number(a.tradeNo) - Number(b.tradeNo));
  return sorted;
}

// ---------- Normalise Unicode minus ----------
function normaliseMinus(text) {
  if (!text) return '';
  return text.replace(/\u2212/g, '-');
}

// ---------- Parse a single <tr> ----------
function parseTradeRow(row, tradeNo) {
  try {
    // Direction
    const dirSpan = row.querySelector('[data-qa-id="column-trade-number"] span.short-fsfBeuzD');
    const direction = dirSpan ? dirSpan.innerText.trim() : '';

    // Double cells helper
    const getDouble = (qaId) => {
      const td = row.querySelector(`[data-qa-id="${qaId}"]`);
      if (!td) return { first: '', second: '' };
      const cells = td.querySelectorAll('.doubleCell-EOHtcR6g > .cell-EOHtcR6g');
      return {
        first: cells[0] ? cells[0].innerText.trim() : '',
        second: cells[1] ? cells[1].innerText.trim() : ''
      };
    };

    // Dates: first = Exit, second = Entry
    const dates = getDouble('column-datetime');
    const exitDate = dates.first === 'Open' ? 'Open' : dates.first;
    const entryDate = dates.second;

    // Prices: first = Exit, second = Entry
    const prices = getDouble('column-price');
    const extractPrice = (text) => {
      if (!text || text === '—') return '';
      const match = text.match(/([0-9,.]+)/);
      return match ? match[1] : text;
    };
    const exitPrice = extractPrice(prices.first);
    const entryPrice = extractPrice(prices.second);

    // Position size
    const posDiv = row.querySelector('[data-qa-id="column-position-size"] .positionSize-JoU6mm3y');
    let positionSize = '';
    if (posDiv) {
      const divs = posDiv.querySelectorAll('div');
      positionSize = divs[0] ? divs[0].innerText.trim() : '';
    }

    // Profit
    const profitTd = row.querySelector('[data-qa-id="column-profit"]');
    let profit = '';
    if (profitTd) {
      const valDiv = profitTd.querySelector('.value-COAm5E_5');
      profit = valDiv ? valDiv.innerText.trim() : profitTd.innerText.trim();
    }

    // Return
    const returnTd = row.querySelector('[data-qa-id="column-return"]');
    let returnVal = '';
    if (returnTd) {
      const valDiv = returnTd.querySelector('.value-COAm5E_5');
      returnVal = valDiv ? valDiv.innerText.trim() : returnTd.innerText.trim();
    }

    return {
      tradeNo,
      direction,
      entryDate,
      exitDate,
      entryPrice,
      exitPrice,
      positionSize,
      profit: normaliseMinus(profit),
      return: normaliseMinus(returnVal)
    };
  } catch (e) {
    console.warn('Failed to parse row', tradeNo, e);
    return null;
  }
}

// ---------- Build CSV ----------
function buildCSV(rows) {
  const headers = [
    'Trade Number',
    'Direction',
    'Entry Date',
    'Exit Date',
    'Entry Price',
    'Exit Price',
    'Position Size',
    'Profit',
    'Return'
  ];
  const escape = (v) => {
    const s = String(v == null ? '' : v);
    return s.includes(',') || s.includes('\n') || s.includes('"')
      ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers.map(escape).join(',')];
  rows.forEach(r => {
    lines.push([
      r.tradeNo,
      r.direction,
      r.entryDate,
      r.exitDate,
      r.entryPrice,
      r.exitPrice,
      r.positionSize,
      r.profit,
      r.return
    ].map(escape).join(','));
  });
  return lines.join('\n');
}

// ---------- Download ----------
function downloadCSV(csv, filename) {
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

// ---------- Main export function ----------
async function exportTradesToCSV() {
  const opened = await ensureTradeListOpen();
  if (!opened) {
    throw new Error('Could not open the Strategy Tester trade list. Please open it manually (bottom panel → Strategy Tester → List of Trades).');
  }

  const rows = await scrapeAllTradeRows();
  if (!rows.length) throw new Error('No trades found.');

  const csv = buildCSV(rows);
  downloadCSV(csv, `TradingView_Trades_${new Date().toISOString().slice(0,10)}.csv`);
  return rows.length;
}

// ---------- Message listener for popup ----------
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'exportTrades') {
    exportTradesToCSV()
      .then(count => sendResponse({ success: true, count }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true; // keep channel open for async
  }
});