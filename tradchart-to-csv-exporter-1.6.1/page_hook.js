// page_hook.js - runs in TradingView MAIN world at document_start.
// Passive WebSocket observer: it does not alter messages or send anything.
(() => {
  if (window.__tvExporterWsHookInstalled) return;
  window.__tvExporterWsHookInstalled = true;

  const NativeWebSocket = window.WebSocket;
  if (!NativeWebSocket) return;

  const barsByKey = new Map();
  const MAX_BARS = 20000;

  function addBar(v) {
    if (!Array.isArray(v) || v.length < 5) return;
    const t = Number(v[0]);
    const o = Number(v[1]);
    const h = Number(v[2]);
    const l = Number(v[3]);
    const c = Number(v[4]);
    const vol = v.length > 5 && v[5] != null ? Number(v[5]) : null;
    if (![t, o, h, l, c].every(Number.isFinite)) return;

    // Series/indicators can share timestamps. OHLC arrays have at least the first 5 numeric fields.
    const key = `${t}|${o}|${h}|${l}|${c}|${Number.isFinite(vol) ? vol : ''}`;
    barsByKey.set(key, { t, o, h, l, c, v: Number.isFinite(vol) ? vol : null });

    if (barsByKey.size > MAX_BARS) {
      const oldest = barsByKey.keys().next().value;
      if (oldest !== undefined) barsByKey.delete(oldest);
    }
  }

  function parsePayload(payload) {
    if (!payload || payload.m !== 'timescale_update' || !Array.isArray(payload.p)) return;
    const seriesMap = payload.p[1];
    if (!seriesMap || typeof seriesMap !== 'object') return;

    for (const series of Object.values(seriesMap)) {
      if (!series || !Array.isArray(series.s)) continue;
      for (const item of series.s) {
        if (item && Array.isArray(item.v)) addBar(item.v);
      }
    }
  }

  function parseWsData(data) {
    if (typeof data !== 'string') return;

    // TradingView framing: ~m~<payloadLength>~m~<payload>.
    // Parse by declared length so nested JSON objects do not confuse the parser.
    let pos = 0;
    let parsedAny = false;
    while (pos < data.length) {
      const prefix = data.indexOf('~m~', pos);
      if (prefix < 0) break;
      const lenStart = prefix + 3;
      const lenEnd = data.indexOf('~m~', lenStart);
      if (lenEnd < 0) break;

      const len = parseInt(data.slice(lenStart, lenEnd), 10);
      if (!Number.isFinite(len) || len < 1) {
        pos = lenEnd + 3;
        continue;
      }

      const payloadStart = lenEnd + 3;
      const payloadText = data.slice(payloadStart, payloadStart + len);
      pos = payloadStart + len;

      if (!payloadText || payloadText[0] !== '{') continue;
      try {
        parsePayload(JSON.parse(payloadText));
        parsedAny = true;
      } catch (_) { }
    }

    // Fallback for an unframed JSON payload.
    if (!parsedAny && data.includes('timescale_update')) {
      const trimmed = data.trim();
      if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
        try { parsePayload(JSON.parse(trimmed)); } catch (_) { }
      }
    }
  }

  function publishSnapshot() {
    try {
      const bars = Array.from(barsByKey.values())
        .sort((a, b) => a.t - b.t)
        .slice(-MAX_BARS);
      window.postMessage({
        source: 'tv-exporter-page-hook',
        type: 'TV_EXPORTER_WS_BARS',
        bars
      }, '*');
    } catch (_) { }
  }

  function WrappedWebSocket(url, protocols) {
    const ws = protocols === undefined
      ? new NativeWebSocket(url)
      : new NativeWebSocket(url, protocols);

    try {
      const urlText = String(url || '');
      if (/tradingview\.com/i.test(urlText)) {
        ws.addEventListener('message', (event) => {
          try {
            parseWsData(event.data);
            // No continuous postMessage flood; content script asks for a snapshot when needed.
          } catch (_) { }
        });
      }
    } catch (_) { }

    return ws;
  }

  // Preserve WebSocket static constants and prototype semantics.
  WrappedWebSocket.prototype = NativeWebSocket.prototype;
  Object.defineProperties(WrappedWebSocket, {
    CONNECTING: { value: NativeWebSocket.CONNECTING },
    OPEN: { value: NativeWebSocket.OPEN },
    CLOSING: { value: NativeWebSocket.CLOSING },
    CLOSED: { value: NativeWebSocket.CLOSED }
  });

  try { window.WebSocket = WrappedWebSocket; } catch (_) { return; }

  window.addEventListener('message', (event) => {
    if (event.source !== window || !event.data) return;
    if (event.data.source === 'tv-exporter-content' && event.data.type === 'TV_EXPORTER_REQUEST_WS_BARS') {
      publishSnapshot();
    }
  });
})();
