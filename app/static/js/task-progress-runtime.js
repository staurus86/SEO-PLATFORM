let pollInterval;
let taskTerminalHandled = false;
let statusRequestInFlight = false;
let lastProgressStateKey = '';
const PROGRESS_STAGE_ORDER = ['queue', 'fetch', 'render', 'analyze', 'done'];
const TASK_STATUS_POLL_MS = 5000;
const TASK_STATUS_WS_WATCHDOG_MS = 15000;

// ---------------------------------------------------------------------------
// WebSocket real-time updates (falls back to polling when unavailable)
// ---------------------------------------------------------------------------
let _wsHandle = null;

function _clearTaskPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

function _startTaskPolling(intervalMs) {
    _clearTaskPolling();
    pollInterval = setInterval(checkTaskStatus, intervalMs);
}

function _disposeTaskStatusWatchers() {
    _clearTaskPolling();
    if (_wsHandle) {
        _wsHandle.close();
        _wsHandle = null;
    }
}

function connectTaskWebSocket(tid, onMessage, hooks = {}) {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = protocol + '//' + location.host + '/ws/tasks/' + tid;
    var ws = null;
    var pingTimer = null;

    try {
        ws = new WebSocket(wsUrl);
        ws.onopen = function() {
            console.log('[WS] Connected for task:', tid);
            if (typeof hooks.onOpen === 'function') hooks.onOpen();
            pingTimer = setInterval(function() {
                if (ws && ws.readyState === WebSocket.OPEN) ws.send('ping');
            }, 30000);
        };
        ws.onmessage = function(event) {
            if (event.data === 'pong') return;
            try {
                var data = JSON.parse(event.data);
                onMessage(data);
            } catch(e) { /* ignore non-JSON */ }
        };
        ws.onclose = function() {
            if (pingTimer) clearInterval(pingTimer);
            pingTimer = null;
            console.log('[WS] Disconnected, falling back to polling');
            if (typeof hooks.onClose === 'function') hooks.onClose();
            ws = null;
        };
        ws.onerror = function() {
            if (ws) ws.close();
        };
    } catch(e) {
        console.log('[WS] Not available, using polling');
    }

    return {
        close: function() { if (ws) ws.close(); if (pingTimer) clearInterval(pingTimer); },
        isConnected: function() { return ws && ws.readyState === WebSocket.OPEN; }
    };
}

function addTaskToLocalHistory(item) {
    try {
        if (typeof window.addToHistory === 'function') {
            window.addToHistory(item);
            return;
        }
        const HISTORY_KEY = 'seo_tools_history';
        const MAX_HISTORY_ITEMS = 10;
        const raw = localStorage.getItem(HISTORY_KEY);
        const history = raw ? JSON.parse(raw) : [];
        history.unshift(item);
        localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, MAX_HISTORY_ITEMS)));
    } catch (e) {
        console.error('Error writing history:', e);
    }
}

const BOT_TREND_HISTORY_KEY = 'seo_bot_check_trends_v1';
const BOT_TREND_HISTORY_LIMIT = 300;

function getBotTrendHistory() {
    try {
        const raw = localStorage.getItem(BOT_TREND_HISTORY_KEY);
        const rows = raw ? JSON.parse(raw) : [];
        return Array.isArray(rows) ? rows : [];
    } catch (e) {
        console.error('Error reading bot trend history:', e);
        return [];
    }
}

function saveBotTrendHistory(rows) {
    try {
        localStorage.setItem(BOT_TREND_HISTORY_KEY, JSON.stringify(rows.slice(0, BOT_TREND_HISTORY_LIMIT)));
    } catch (e) {
        console.error('Error saving bot trend history:', e);
    }
}

function extractDomain(value) {
    try {
        return new URL(String(value || '')).hostname.toLowerCase();
    } catch (e) {
        return '';
    }
}

function sanitizeFilenamePart(value) {
    return String(value || 'site')
        .trim()
        .replace(/^https?:\/\//i, '')
        .replace(/[^a-zA-Z0-9._-]+/g, '_')
        .replace(/^_+|_+$/g, '') || 'site';
}

function buildFilenameTimestamp() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return [
        now.getFullYear(),
        pad(now.getMonth() + 1),
        pad(now.getDate())
    ].join('-') + '_' + [
        pad(now.getHours()),
        pad(now.getMinutes()),
        pad(now.getSeconds())
    ].join('-');
}

function buildReportFilename(prefix, extension, sourceUrl = '') {
    const domain = sanitizeFilenamePart(extractDomain(sourceUrl) || sourceUrl || 'site');
    return `${prefix}-${domain}-${buildFilenameTimestamp()}.${extension}`;
}

function filenameFromResponse(response, fallbackPrefix, extension, sourceUrl = '') {
    const cd = response.headers.get('Content-Disposition') || response.headers.get('content-disposition') || '';
    const match = cd.match(/filename=([^;]+)/i);
    return match ? match[1].replace(/\"/g, '') : buildReportFilename(fallbackPrefix, extension, sourceUrl);
}

function saveBotTrendSnapshot(result) {
    try {
        const r = result.results || result;
        const summary = r.summary || {};
        const url = result.url || '';
        const domain = extractDomain(url);
        if (!domain) return null;

        const snapshot = {
            task_id: result.task_id || taskId,
            timestamp: new Date().toISOString(),
            url,
            domain,
            total: Number(summary.total || 0),
            crawlable: Number(summary.crawlable || 0),
            renderable: Number(summary.renderable || 0),
            accessible: Number(summary.accessible || 0),
            indexable: Number(summary.indexable || 0),
            non_indexable: Number(summary.non_indexable || 0),
            avg_response_time_ms: Number(summary.avg_response_time_ms || 0),
            issues_total: Number(summary.issues_total || 0),
            critical_issues: Number(summary.critical_issues || 0),
            warning_issues: Number(summary.warning_issues || 0),
            info_issues: Number(summary.info_issues || 0),
            waf_cdn_detected: Number(summary.waf_cdn_detected || 0),
            retry_profile: r.retry_profile || 'standard',
            criticality_profile: r.criticality_profile || 'balanced',
            sla_profile: r.sla_profile || 'standard',
        };

        const history = getBotTrendHistory();
        const filtered = history.filter((x) => String(x.task_id || '') !== String(snapshot.task_id));
        filtered.unshift(snapshot);
        saveBotTrendHistory(filtered);
        return snapshot;
    } catch (e) {
        console.error('Error saving bot snapshot:', e);
        return null;
    }
}

function getBotSnapshotsForUrl(url) {
    const domain = extractDomain(url);
    if (!domain) return [];
    return getBotTrendHistory()
        .filter((x) => String(x.domain || '') === domain)
        .sort((a, b) => String(b.timestamp || '').localeCompare(String(a.timestamp || '')));
}

function formatTrendDelta(current, prev) {
    const c = Number(current || 0);
    const p = Number(prev || 0);
    if (!Number.isFinite(c) || !Number.isFinite(p)) return 'н/д';
    const d = c - p;
    return `${d > 0 ? '+' : ''}${d}`;
}

function formatEngineLabel(engine) {
    const value = String(engine || 'legacy').toLowerCase();
    if (value === 'legacy') return 'базовый';
    if (value === 'legacy-fallback') return 'базовый (fallback)';
    return value;
}

function formatProfileLabel(profile) {
    const value = String(profile || '').toLowerCase();
    if (value === 'mobile') return 'мобильный';
    if (value === 'desktop') return 'десктоп';
    return profile || '-';
}

function formatPolicyProfile(value, type) {
    const v = String(value || '').toLowerCase();
    if (!v) return 'н/д';
    if (type === 'retry') {
        if (v === 'standard') return 'стандартный';
        if (v === 'aggressive') return 'агрессивный';
        if (v === 'strict') return 'строгий';
    }
    if (type === 'criticality') {
        if (v === 'balanced') return 'сбалансированный';
        if (v === 'strict') return 'строгий';
        if (v === 'aggressive') return 'агрессивный';
    }
    if (type === 'sla') {
        if (v === 'standard') return 'стандартный';
        if (v === 'strict') return 'строгий';
    }
    return value;
}
