let pollInterval;
let taskTerminalHandled = false;
let statusRequestInFlight = false;
let lastProgressStateKey = '';
const PROGRESS_STAGE_ORDER = ['queue', 'fetch', 'render', 'analyze', 'done'];

// ---------------------------------------------------------------------------
// WebSocket real-time updates (falls back to polling when unavailable)
// ---------------------------------------------------------------------------
let _wsHandle = null;

function connectTaskWebSocket(tid, onMessage) {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = protocol + '//' + location.host + '/ws/tasks/' + tid;
    var ws = null;
    var pingTimer = null;

    try {
        ws = new WebSocket(wsUrl);
        ws.onopen = function() {
            console.log('[WS] Connected for task:', tid);
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

function _cp1251ByteForChar(ch) {
    const code = ch.charCodeAt(0);
    if (code <= 0xFF) return code;
    if (ch === 'Ё') return 0xA8;
    if (ch === 'ё') return 0xB8;
    if (code >= 0x0410 && code <= 0x044F) return code - 0x350;
    return null;
}

function _decodeMixedCp1251Utf8(str) {
    const bytes = [];
    for (const ch of String(str || '')) {
        const b = _cp1251ByteForChar(ch);
        if (b === null) return null;
        bytes.push(b);
    }
    try {
        return new TextDecoder('utf-8', { fatal: true }).decode(new Uint8Array(bytes));
    } catch {
        return null;
    }
}

function _looksMojibake(value) {
    if (!value) return false;
    if (/[в][Ђ„]/.test(value) || /[Ѓљњћџ]/.test(value)) return true;
    if (/[РС][^\s]/.test(value) || /Ð.|Ñ.|â.|Ã./.test(value)) return true;
    for (const ch of value) {
        const code = ch.charCodeAt(0);
        if (code >= 0x80 && code <= 0x9F) return true;
    }
    return false;
}

function repairMojibakeText(value) {
    if (typeof value !== 'string' || !value) return value;
    let current = value;
    for (let i = 0; i < 3; i++) {
        const candidates = [];
        const mixed = _decodeMixedCp1251Utf8(current);
        if (mixed && mixed !== current) candidates.push(mixed);
        try {
            const bytes = [];
            for (const ch of current) {
                const code = ch.charCodeAt(0);
                if (code > 0xFF) throw new Error('non-latin1');
                bytes.push(code);
            }
            const latin = new TextDecoder('utf-8', { fatal: true }).decode(new Uint8Array(bytes));
            if (latin && latin !== current) candidates.push(latin);
        } catch {}
        if (!candidates.length) break;
        const currentBad = (_looksMojibake(current) ? 1 : 0) + (current.match(/[РСÐÑ]/g) || []).length;
        let best = current;
        let bestBad = currentBad;
        for (const cand of candidates) {
            const bad = (_looksMojibake(cand) ? 1 : 0) + (cand.match(/[РСÐÑ]/g) || []).length;
            if (bad < bestBad) {
                best = cand;
                bestBad = bad;
            }
        }
        if (best !== current && (currentBad > bestBad || _looksMojibake(current))) {
            current = best;
            continue;
        }
        break;
    }
    return current;
}

function normalizeMojibakeDeep(input) {
    if (typeof input === 'string') {
        return repairMojibakeText(input);
    }
    if (Array.isArray(input)) {
        return input.map((item) => normalizeMojibakeDeep(item));
    }
    if (input && typeof input === 'object') {
        const out = {};
        for (const [key, value] of Object.entries(input)) {
            out[key] = normalizeMojibakeDeep(value);
        }
        return out;
    }
    return input;
}

document.addEventListener('DOMContentLoaded', function() {
    checkTaskStatus();
    pollInterval = setInterval(checkTaskStatus, 1500);

    // Try to establish WebSocket for real-time updates
    if (typeof taskId !== 'undefined' && taskId) {
        _wsHandle = connectTaskWebSocket(taskId, function(wsData) {
            if (taskTerminalHandled) {
                if (_wsHandle) _wsHandle.close();
                return;
            }
            // WebSocket delivers a partial or full update — do a fresh poll
            // to get the complete payload (result can be large, WS sends slim updates).
            checkTaskStatus();
        });
    }
});

function _deriveStage(data) {
    const msg = String(data.status_message || '').toLowerCase();
    const p = Number(data.progress || 0);
    if (data.status === 'SUCCESS') return 'done';
    if (data.status === 'FAILURE') return 'analyze';
    if (msg.includes('queue')) return 'queue';
    if (msg.includes('fetch') || msg.includes('http') || msg.includes('no-js')) return 'fetch';
    if (msg.includes('render') || msg.includes('playwright') || msg.includes('screenshot')) return 'render';
    if (msg.includes('diff') || msg.includes('score') || msg.includes('analysis')) return 'analyze';
    if (p >= 95) return 'done';
    if (p >= 75) return 'analyze';
    if (p >= 50) return 'render';
    if (p >= 20) return 'fetch';
    return 'queue';
}

function updateStageRail(stage) {
    const elements = document.querySelectorAll('#progress-stages [data-stage]');
    if (!elements.length) return;
    const active = stage && PROGRESS_STAGE_ORDER.includes(stage) ? stage : 'queue';
    const activeIndex = PROGRESS_STAGE_ORDER.indexOf(active);
    elements.forEach((el) => {
        const token = el.getAttribute('data-stage');
        const idx = PROGRESS_STAGE_ORDER.indexOf(token);
        const dot = el.querySelector('span.w-2\\.5');
        el.classList.remove('bg-slate-50', 'text-slate-600', 'border-slate-200');
        el.classList.remove('bg-blue-50', 'text-blue-700', 'border-blue-200');
        el.classList.remove('bg-green-50', 'text-green-700', 'border-green-200');
        if (idx < activeIndex) {
            el.classList.add('bg-green-50', 'text-green-700', 'border-green-200');
            if (dot) dot.className = 'w-2.5 h-2.5 rounded-full bg-green-500';
        } else if (idx === activeIndex) {
            el.classList.add('bg-blue-50', 'text-blue-700', 'border-blue-200');
            if (dot) dot.className = 'w-2.5 h-2.5 rounded-full bg-blue-500';
        } else {
            el.classList.add('bg-slate-50', 'text-slate-600', 'border-slate-200');
            if (dot) dot.className = 'w-2.5 h-2.5 rounded-full bg-slate-300';
        }
    });
}

function _parseIsoDate(value) {
    const raw = String(value || '').trim();
    if (!raw) return null;
    const dt = new Date(raw);
    return Number.isNaN(dt.getTime()) ? null : dt;
}

function _formatElapsed(ms) {
    const totalSec = Math.max(0, Math.floor(Number(ms || 0) / 1000));
    const hours = Math.floor(totalSec / 3600);
    const minutes = Math.floor((totalSec % 3600) / 60);
    const seconds = totalSec % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return hours > 0
        ? `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
        : `${pad(minutes)}:${pad(seconds)}`;
}

function _formatDateTime(value) {
    const dt = _parseIsoDate(value);
    if (!dt) return '-';
    return dt.toLocaleString('ru-RU', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function _formatDuration(ms) {
    const totalSec = Math.max(0, Math.floor(Number(ms || 0) / 1000));
    if (totalSec < 60) return `${totalSec} сек`;
    const hours = Math.floor(totalSec / 3600);
    const minutes = Math.floor((totalSec % 3600) / 60);
    if (hours > 0) return `${hours} ч ${minutes} мин`;
    return `${minutes} мин`;
}

function _formatTimeOnly(value) {
    const dt = value instanceof Date ? value : _parseIsoDate(value);
    if (!dt) return '-';
    return dt.toLocaleTimeString('ru-RU', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}

function _formatHeartbeatAge(value) {
    const dt = _parseIsoDate(value);
    if (!dt) return '-';
    const diffMs = Date.now() - dt.getTime();
    if (diffMs < 0) return 'только что';
    const sec = Math.floor(diffMs / 1000);
    if (sec < 5) return 'только что';
    if (sec < 60) return `${sec} сек назад`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min} мин назад`;
    const hours = Math.floor(min / 60);
    return `${hours} ч назад`;
}

function _formatTaskType(taskType) {
    const value = String(taskType || '').trim().toLowerCase();
    const labels = {
        unified_audit: 'Full SEO Audit',
        robots_check: 'Robots.txt',
        sitemap_validate: 'Sitemap',
        bot_check: 'Bot Check',
        mobile_check: 'Mobile Audit',
        render_audit: 'Render Audit',
        site_audit_pro: 'Site Audit Pro',
        onpage_audit: 'OnPage Audit',
        clusterizer: 'Clusterizer',
        redirect_checker: 'Redirect Checker',
        core_web_vitals: 'Core Web Vitals',
        link_profile_audit: 'Link Profile',
        site_analyze: 'Site Analyze',
    };
    return labels[value] || taskType || '-';
}

function _formatTaskStatus(status) {
    const value = String(status || '').trim().toUpperCase();
    const labels = {
        PENDING: 'PENDING',
        RUNNING: 'RUNNING',
        SUCCESS: 'SUCCESS',
        FAILURE: 'FAILURE',
    };
    return labels[value] || value || '-';
}

function _computeExecutionStats(data, elapsedMs) {
    const meta = data.progress_meta || {};
    const isRedirectChecker = data.task_type === 'redirect_checker';
    const isBatchLike = Number(meta.total_pages || 0) > 0;

    if (isRedirectChecker) {
        const total = Number(meta.scenario_count || 0);
        const done = Number(meta.current_scenario_index || 0);
        if (total <= 0 || done <= 0 || elapsedMs <= 0) {
            return { rate: '-', eta: '-', etaMs: 0, finishAt: '-', currentStep: meta.current_scenario_title || meta.current_step || '-' };
        }
        const perMinute = done / (elapsedMs / 60000);
        const remaining = Math.max(0, total - done);
        const etaMs = perMinute > 0 ? (remaining / perMinute) * 60000 : 0;
        const finishAt = remaining > 0 && etaMs > 0 ? _formatTimeOnly(new Date(Date.now() + etaMs)) : 'скоро';
        return {
            rate: `${perMinute.toFixed(perMinute >= 10 ? 0 : 1)} сцен./мин`,
            eta: remaining > 0 ? _formatDuration(etaMs) : 'почти готово',
            etaMs,
            finishAt,
            currentStep: meta.current_scenario_title || meta.current_step || '-',
        };
    }

    if (isBatchLike) {
        const total = Number(meta.total_pages || 0);
        const done = Number(meta.processed_pages || 0);
        if (total <= 0 || done <= 0 || elapsedMs <= 0) {
            return {
                rate: '-',
                eta: '-',
                etaMs: 0,
                finishAt: '-',
                currentStep: meta.current_step || meta.current_url || data.status_message || '-',
            };
        }
        const perMinute = done / (elapsedMs / 60000);
        const remaining = Math.max(0, total - done);
        const etaMs = perMinute > 0 ? (remaining / perMinute) * 60000 : 0;
        const finishAt = remaining > 0 && etaMs > 0 ? _formatTimeOnly(new Date(Date.now() + etaMs)) : 'скоро';
        return {
            rate: `${perMinute.toFixed(perMinute >= 10 ? 0 : 1)} стр./мин`,
            eta: remaining > 0 ? _formatDuration(etaMs) : 'почти готово',
            etaMs,
            finishAt,
            currentStep: meta.current_step || meta.current_url || data.status_message || '-',
        };
    }

    return {
        rate: '-',
        eta: '-',
        etaMs: 0,
        finishAt: '-',
        currentStep: meta.current_step || meta.current_scenario_title || meta.current_url || data.status_message || '-',
    };
}

function _formatProgressStage(taskType, progressMeta, data) {
    const stage = String(progressMeta.current_stage || '').trim().toLowerCase();
    const redirectMap = {
        redirect_checks: 'Redirect checks',
        done: 'Готово',
        failed: 'Ошибка'
    };
    if (taskType === 'redirect_checker' && stage) {
        const scenarios = Number(progressMeta.scenario_count || 0);
        const label = redirectMap[stage] || stage;
        return scenarios > 0 ? `${label} · ${scenarios} сценариев` : label;
    }
    if (stage) return stage;
    if (data.status === 'RUNNING') return 'Выполнение';
    return 'Ожидание';
}

function _toggleProgressMetaCard(cardId, isVisible) {
    const el = document.getElementById(cardId);
    if (!el) return;
    el.classList.toggle('hidden', !isVisible);
}

async function checkTaskStatus() {
    if (taskTerminalHandled || statusRequestInFlight) {
        return;
    }
    statusRequestInFlight = true;
    try {
        const response = await fetch(`/api/tasks/${taskId}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = normalizeMojibakeDeep(await response.json());
        
        const progressMeta = data.progress_meta || {};
        const nextProgressKey = [
            data.status || '',
            String(data.progress ?? ''),
            data.status_message || '',
            String(progressMeta.current_stage ?? ''),
            String(progressMeta.heartbeat_at ?? ''),
            String(data.created_at ?? ''),
            String(data.started_at ?? ''),
            String(data.updated_at ?? ''),
            String(data.task_type ?? ''),
            String(progressMeta.processed_pages ?? ''),
            String(progressMeta.total_pages ?? ''),
            String(progressMeta.queue_size ?? ''),
            progressMeta.current_url || '',
        ].join('|');
        if (nextProgressKey !== lastProgressStateKey || data.status === 'RUNNING' || data.status === 'PENDING') {
            updateProgress(data);
            lastProgressStateKey = nextProgressKey;
        }
        
        if (data.status === 'SUCCESS') {
            taskTerminalHandled = true;
            clearInterval(pollInterval);
            if (_wsHandle) _wsHandle.close();
            showResults(data.result);
        } else if (data.status === 'FAILURE') {
            taskTerminalHandled = true;
            clearInterval(pollInterval);
            if (_wsHandle) _wsHandle.close();
            showError(data);
        }
    } catch (error) {
        console.error('Error checking task status:', error);
    } finally {
        statusRequestInFlight = false;
    }
}

function updateProgress(data) {
    const progressBar = document.getElementById('progress-bar');
    const statusTitle = document.getElementById('status-title');
    const statusMessage = document.getElementById('status-message');
    const statusIcon = document.getElementById('status-icon');
    const progressMetaWrap = document.getElementById('progress-meta');
    const processedEl = document.getElementById('progress-processed');
    const totalEl = document.getElementById('progress-total');
    const queueEl = document.getElementById('progress-queue');
    const currentUrlEl = document.getElementById('progress-current-url');
    const taskTypeEl = document.getElementById('progress-task-type');
    const taskStatusEl = document.getElementById('progress-task-status');
    const startedAtEl = document.getElementById('progress-started-at');
    const updatedAtEl = document.getElementById('progress-updated-at');
    const stepEl = document.getElementById('progress-step');
    const rateEl = document.getElementById('progress-rate');
    const etaEl = document.getElementById('progress-eta');
    const finishAtEl = document.getElementById('progress-finish-at');
    const elapsedEl = document.getElementById('progress-elapsed');
    const stageEl = document.getElementById('progress-stage-label');
    const heartbeatEl = document.getElementById('progress-heartbeat');
    const progressMeta = data.progress_meta || {};
    const createdAt = _parseIsoDate(data.created_at);
    const elapsedMs = createdAt ? (Date.now() - createdAt.getTime()) : 0;
    const isRedirectChecker = data.task_type === 'redirect_checker';
    const currentUrl = progressMeta.current_url || data.url || '-';
    const hasHeartbeat = Boolean(progressMeta.heartbeat_at);
    const showLifecycleMeta = data.status !== 'SUCCESS' && data.status !== 'FAILURE';
    const executionStats = _computeExecutionStats(data, elapsedMs);

    if (data.status === 'SUCCESS') {
        statusTitle.textContent = 'Готово';
        statusMessage.textContent = data.status_message || 'Анализ успешно завершен';
        progressBar.style.width = '100%';
        progressBar.classList.remove('bg-blue-500');
        progressBar.classList.add('bg-green-500');
        statusIcon.innerHTML = '<i class="fas fa-check-circle text-green-500"></i>';
        progressMetaWrap.classList.add('hidden');
        _toggleProgressMetaCard('progress-task-type-card', false);
        _toggleProgressMetaCard('progress-task-status-card', false);
        _toggleProgressMetaCard('progress-started-card', false);
        _toggleProgressMetaCard('progress-updated-card', false);
        _toggleProgressMetaCard('progress-elapsed-card', false);
        _toggleProgressMetaCard('progress-stage-card', false);
        _toggleProgressMetaCard('progress-heartbeat-card', false);
        _toggleProgressMetaCard('progress-step-card', false);
        _toggleProgressMetaCard('progress-rate-card', false);
        _toggleProgressMetaCard('progress-eta-card', false);
        _toggleProgressMetaCard('progress-finish-card', false);
        updateStageRail('done');
        return;
    }

    if (data.status === 'FAILURE') {
        statusTitle.textContent = 'Ошибка';
        statusMessage.textContent = data.error || data.status_message || 'Анализ завершился с ошибкой';
        progressBar.style.width = `${data.progress || 100}%`;
        progressBar.classList.remove('bg-blue-500');
        progressBar.classList.add('bg-red-500');
        statusIcon.innerHTML = '<i class="fas fa-times-circle text-red-500"></i>';
        progressMetaWrap.classList.add('hidden');
        _toggleProgressMetaCard('progress-task-type-card', false);
        _toggleProgressMetaCard('progress-task-status-card', false);
        _toggleProgressMetaCard('progress-started-card', false);
        _toggleProgressMetaCard('progress-updated-card', false);
        _toggleProgressMetaCard('progress-elapsed-card', false);
        _toggleProgressMetaCard('progress-stage-card', false);
        _toggleProgressMetaCard('progress-heartbeat-card', false);
        _toggleProgressMetaCard('progress-step-card', false);
        _toggleProgressMetaCard('progress-rate-card', false);
        _toggleProgressMetaCard('progress-eta-card', false);
        _toggleProgressMetaCard('progress-finish-card', false);
        updateStageRail('analyze');
        return;
    }

    const p = Number.isFinite(Number(data.progress)) ? Number(data.progress) : 10;
    statusTitle.textContent = data.status === 'RUNNING' ? 'Выполняется...' : 'В очереди...';
    statusMessage.textContent = data.status_message || (data.status === 'RUNNING' ? 'Идет анализ' : 'Задача ожидает в очереди');
    progressBar.style.width = `${Math.max(5, Math.min(95, p))}%`;
    progressBar.classList.remove('bg-green-500', 'bg-red-500');
    progressBar.classList.add('bg-blue-500');
    statusIcon.innerHTML = '<i class="fas fa-spinner fa-spin text-blue-500"></i>';

    if (taskTypeEl) taskTypeEl.textContent = _formatTaskType(data.task_type);
    if (taskStatusEl) taskStatusEl.textContent = _formatTaskStatus(data.status);
    if (startedAtEl) startedAtEl.textContent = _formatDateTime(data.started_at || data.created_at);
    if (updatedAtEl) updatedAtEl.textContent = _formatDateTime(data.updated_at || data.created_at);
    if (elapsedEl) elapsedEl.textContent = _formatElapsed(elapsedMs);
    if (stageEl) stageEl.textContent = _formatProgressStage(data.task_type, progressMeta, data);
    if (heartbeatEl) heartbeatEl.textContent = hasHeartbeat ? _formatHeartbeatAge(progressMeta.heartbeat_at) : 'ожидание';
    if (stepEl) stepEl.textContent = executionStats.currentStep || '-';
    if (rateEl) rateEl.textContent = executionStats.rate || '-';
    if (etaEl) etaEl.textContent = executionStats.eta || '-';
    if (finishAtEl) finishAtEl.textContent = executionStats.finishAt || '-';

    const isSitePro = (data.task_type === 'site_audit_pro');
    const isUnifiedAudit = (data.task_type === 'unified_audit');
    const isCwvBatch = (data.task_type === 'core_web_vitals') && Number(progressMeta.total_pages || 0) > 1;
    const totalPages = Number(progressMeta.total_pages || 0);
    if (showLifecycleMeta) {
        _toggleProgressMetaCard('progress-task-type-card', true);
        _toggleProgressMetaCard('progress-task-status-card', true);
        _toggleProgressMetaCard('progress-started-card', true);
        _toggleProgressMetaCard('progress-updated-card', true);
        _toggleProgressMetaCard('progress-elapsed-card', true);
        _toggleProgressMetaCard('progress-stage-card', true);
        _toggleProgressMetaCard('progress-heartbeat-card', isRedirectChecker || hasHeartbeat);
        _toggleProgressMetaCard('progress-step-card', true);
        _toggleProgressMetaCard('progress-rate-card', executionStats.rate !== '-');
        _toggleProgressMetaCard('progress-eta-card', executionStats.eta !== '-');
        _toggleProgressMetaCard('progress-finish-card', executionStats.finishAt !== '-');
    }
    if ((isSitePro || isCwvBatch) && totalPages > 0) {
        const processedPages = Number(progressMeta.processed_pages || 0);
        const queueSize = Number(progressMeta.queue_size || 0);
        processedEl.textContent = String(processedPages);
        totalEl.textContent = String(totalPages);
        queueEl.textContent = String(queueSize);
        currentUrlEl.textContent = currentUrl;
        _toggleProgressMetaCard('progress-processed-card', true);
        _toggleProgressMetaCard('progress-total-card', true);
        _toggleProgressMetaCard('progress-queue-card', true);
        _toggleProgressMetaCard('progress-current-url-card', true);
        progressMetaWrap.classList.remove('hidden');
    } else if (isRedirectChecker) {
        processedEl.textContent = String(Number(progressMeta.scenario_count || 17));
        totalEl.textContent = String(Number(progressMeta.scenario_count || 17));
        queueEl.textContent = String(data.progress || 0) + '%';
        currentUrlEl.textContent = currentUrl;
        _toggleProgressMetaCard('progress-processed-card', true);
        _toggleProgressMetaCard('progress-total-card', true);
        _toggleProgressMetaCard('progress-queue-card', true);
        _toggleProgressMetaCard('progress-current-url-card', true);
        const processedCardLabel = document.querySelector('#progress-processed-card div');
        const totalCardLabel = document.querySelector('#progress-total-card div');
        const queueCardLabel = document.querySelector('#progress-queue-card div');
        if (processedCardLabel) processedCardLabel.textContent = 'Сценариев';
        if (totalCardLabel) totalCardLabel.textContent = 'План';
        if (queueCardLabel) queueCardLabel.textContent = 'Прогресс';
        progressMetaWrap.classList.remove('hidden');
    } else {
        const processedCardLabel = document.querySelector('#progress-processed-card div');
        const totalCardLabel = document.querySelector('#progress-total-card div');
        const queueCardLabel = document.querySelector('#progress-queue-card div');
        if (processedCardLabel) processedCardLabel.textContent = 'Обработано';
        if (totalCardLabel) totalCardLabel.textContent = 'Всего';
        if (queueCardLabel) queueCardLabel.textContent = 'Очередь';
        _toggleProgressMetaCard('progress-processed-card', false);
        _toggleProgressMetaCard('progress-total-card', false);
        _toggleProgressMetaCard('progress-queue-card', false);
        _toggleProgressMetaCard('progress-current-url-card', isRedirectChecker || isUnifiedAudit);
        currentUrlEl.textContent = currentUrl;
        progressMetaWrap.classList.toggle('hidden', !showLifecycleMeta);
    }
    updateStageRail(_deriveStage(data));
}

function showResults(data) {
    document.getElementById('progress-section').classList.add('hidden');
    document.getElementById('results-section').classList.remove('hidden');
    const result = normalizeMojibakeDeep(data);
    lastTaskResult = result;
    if (result.task_type !== 'link_profile_audit') {
        linkProfileRenderPayload = null;
        linkProfileRowsLimitByScope = {};
    }
    
    // Store data for download functions (include task_id from API response)
    if (result.task_type === 'robots_check') {
        robotsData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
        console.log('Robots data stored:', robotsData);
    } else if (result.task_type === 'sitemap_validate') {
        sitemapData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type === 'bot_check') {
        botData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type === 'mobile_check') {
        mobileData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type === 'render_audit') {
        renderData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type === 'site_audit_pro') {
        siteAuditProData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type === 'onpage_audit') {
        onpageData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type === 'clusterizer') {
        clusterizerData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type === 'redirect_checker') {
        redirectCheckerData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type === 'core_web_vitals') {
        coreWebVitalsData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type === 'link_profile_audit') {
        linkProfileData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
        linkProfileRenderPayload = result;
        linkProfileRowsLimitByScope = {};
    } else if (result.task_type === 'unified_audit') {
        unifiedAuditData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    } else if (result.task_type && result.task_type.startsWith('batch_')) {
        batchResultsData = {
            ...result,
            task_id: result.task_id || taskId,
            url: result.url || ''
        };
    }

    const resultsContent = document.getElementById('results-content');
    
    // Generate results HTML based on task type
    if (result.task_type === 'site_analyze') {
        resultsContent.innerHTML = generateSiteAnalysisHTML(result);
    } else if (result.task_type === 'robots_check') {
        resultsContent.innerHTML = generateRobotsHTML(result);
    } else if (result.task_type === 'sitemap_validate') {
        resultsContent.innerHTML = generateSitemapHTMLV2(result);
    } else if (result.task_type === 'bot_check') {
        saveBotTrendSnapshot(result);
        resultsContent.innerHTML = generateBotHTML(result);
        setTimeout(() => applyBotTableControls(), 0);
    } else if (result.task_type === 'render_audit') {
        resultsContent.innerHTML = generateRenderAuditHTML(result);
    } else if (result.task_type === 'mobile_check') {
        resultsContent.innerHTML = generateMobileCheckHTML(result);
    } else if (result.task_type === 'site_audit_pro') {
        resultsContent.innerHTML = generateSiteAuditProHTML(result);
        initSiteAuditProIssuesExplorer(result);
    } else if (result.task_type === 'onpage_audit') {
        resultsContent.innerHTML = generateOnPageAuditHTML(result);
        filterOnpageIssues('all');
    } else if (result.task_type === 'clusterizer') {
        resultsContent.innerHTML = generateClusterizerHTML(result);
    } else if (result.task_type === 'redirect_checker') {
        resultsContent.innerHTML = generateRedirectCheckerHTML(result);
    } else if (result.task_type === 'core_web_vitals') {
        resultsContent.innerHTML = generateCoreWebVitalsHTML(result);
    } else if (result.task_type === 'link_profile_audit') {
        resultsContent.innerHTML = generateLinkProfileAuditHTML(result);
        switchLinkProfileAuditTab('executive');
    } else if (result.task_type === 'unified_audit') {
        resultsContent.innerHTML = generateUnifiedAuditHTML(result);
    } else if (result.task_type && result.task_type.startsWith('batch_')) {
        resultsContent.innerHTML = generateBatchResultsHTML(result);
    } else {
        resultsContent.innerHTML = generateGenericHTML(result);
    }

    // Initialize Chart.js charts after HTML is rendered
    setTimeout(() => initChartsForTool(result.task_type, result), 50);
}

// ─────────────────────────────────────────────────────────────────────────────
// Chart.js integration — creates charts after result HTML is injected into DOM
// ─────────────────────────────────────────────────────────────────────────────
function initChartsForTool(taskType, result) {
    if (typeof Chart === 'undefined' || typeof createScoreGauge === 'undefined') return;
    try {
        const r = result.results || result.result || result;

        if (taskType === 'robots_check') {
            const score = Number(r.quality_score ?? 0);
            createScoreGauge('ds-chart-robots-score', score, 'Robots Score');
            const crit = Array.isArray(r.critical_issues) ? r.critical_issues.length : 0;
            const warn = Array.isArray(r.warning_issues || r.warnings) ? (r.warning_issues || r.warnings).length : 0;
            const info = Array.isArray(r.info_issues) ? r.info_issues.length : 0;
            const sc = r.severity_counts || {};
            const critN = sc.critical ?? crit;
            const warnN = sc.warning ?? warn;
            const infoN = sc.info ?? info;
            if (critN + warnN + infoN > 0) {
                createBarChart('ds-chart-robots-severity', ['Critical', 'Warning', 'Info'],
                    [{data: [critN, warnN, infoN], label: 'Issues', color: ['#ef4444','#f59e0b','#3b82f6']}]);
            }
        }

        if (taskType === 'sitemap_validate') {
            const score = Number(r.quality_score ?? 0);
            createScoreGauge('ds-chart-sitemap-score', score, 'Sitemap Score');
            const totalUrls = Number(r.urls_count || 0);
            const uniqueUrls = Number(r.unique_urls_count || 0);
            const duplicates = totalUrls - uniqueUrls;
            if (totalUrls > 0) {
                createBarChart('ds-chart-sitemap-dist', ['Уникальные', 'Дубли'], [uniqueUrls, duplicates]);
            }
            const issues = r.issues || [];
            const sevCounts = r.severity_counts || {};
            const critI = sevCounts.critical ?? issues.filter(i => i.severity === 'critical').length;
            const warnI = sevCounts.warning ?? issues.filter(i => i.severity === 'warning').length;
            const infoI = sevCounts.info ?? issues.filter(i => i.severity === 'info').length;
            if (critI + warnI + infoI > 0) {
                createBarChart('ds-chart-sitemap-severity', ['Critical', 'Warning', 'Info'],
                    [{data: [critI, warnI, infoI], label: 'Issues', color: ['#ef4444','#f59e0b','#3b82f6']}]);
            }
        }

        if (taskType === 'bot_check') {
            const summary = r.summary || {};
            const crawl = Number(summary.crawlable || 0);
            const render = Number(summary.renderable || 0);
            const index = Number(summary.indexable || 0);
            const access = Number(summary.accessible || 0);
            const total = Number(summary.total || 1);
            if (total > 0) {
                createRadarChart('ds-chart-bot-radar',
                    ['Crawl', 'Render', 'Index', 'Access'],
                    [Math.round(crawl/total*100), Math.round(render/total*100), Math.round(index/total*100), Math.round(access/total*100)],
                    'Bot Accessibility');
            }
        }

        if (taskType === 'render_audit') {
            const overallScore = Number(r.summary?.score ?? 0);
            createScoreGauge('ds-chart-render-raw', overallScore, 'Overall');
            const variants = Array.isArray(r.variants) ? r.variants : [];
            if (variants.length > 0) {
                const labels = variants.map(v => v.variant_label || v.variant_id || 'Variant');
                const scores = variants.map(v => Number(v.metrics?.score ?? 0));
                createBarChart('ds-chart-render-rendered', labels,
                    [{data: scores, label: 'Score', color: '#3b82f6'}]);
            }
        }

        if (taskType === 'mobile_check') {
            const devices = r.device_results || [];
            if (Array.isArray(devices) && devices.length > 0) {
                const labels = devices.map(d => d.device_name || 'Device');
                const loadTimes = devices.map(d => Number(d.load_time_ms || 0));
                createBarChart('ds-chart-mobile-radar', labels,
                    [{data: loadTimes, label: 'Загрузка (мс)', color: '#0e7490'}]);
                const friendly = devices.filter(d => d.mobile_friendly).length;
                const notFriendly = devices.length - friendly;
                createPieChart('ds-chart-mobile-compat', ['Mobile-Friendly', 'Проблемы'], [friendly, notFriendly], true);
            }
        }

        if (taskType === 'onpage_audit') {
            const heatmap = r.heatmap || {};
            const heatLabels = Object.keys(heatmap);
            const heatValues = heatLabels.map(k => Number(heatmap[k]?.score ?? 0));
            if (heatLabels.length > 0) {
                createRadarChart('ds-chart-onpage-radar', heatLabels, heatValues, 'Quality');
            }
            const overallScore = Number(r.score ?? r.summary?.score ?? 0);
            createScoreGauge('ds-chart-onpage-score', overallScore, 'OnPage Score');
            const keywords = r.keywords || [];
            if (keywords.length > 0) {
                const topKw = keywords.slice(0, 8);
                const kwLabels = topKw.map(k => k.keyword || '');
                const kwValues = topKw.map(k => Number(k.density_pct ?? 0));
                createBarChart('ds-chart-onpage-density', kwLabels,
                    [{data: kwValues, label: 'Плотность %', color: '#3b82f6'}]);
            }
        }

        if (taskType === 'clusterizer') {
            const clusters = r.clusters || [];
            if (Array.isArray(clusters) && clusters.length > 0) {
                const top = clusters.slice(0, 20);
                const labels = top.map((c, i) => c.cluster_label || c.representative || `Cluster ${i+1}`);
                const values = top.map(c => Number(c.size || c.keywords?.length || 0));
                createHorizontalBar('ds-chart-cluster-bar', labels, values, '#06b6d4');
            }
            const intentDist = r.intent_distribution || {};
            const intentLabels = Object.keys(intentDist);
            const intentValues = intentLabels.map(k => Number(intentDist[k] || 0));
            if (intentLabels.length > 0) {
                createPieChart('ds-chart-cluster-intent', intentLabels, intentValues, true);
            }
        }

        if (taskType === 'site_audit_pro') {
            // Compute insight pillars from raw data (same logic as generateSiteAuditProHTML)
            const pages = r.pages || [];
            const issues = r.issues || [];
            const pipeline = r.pipeline || {};
            const metrics = pipeline.metrics || {};
            const totalPagesBase = Math.max(1, Number((r.summary || {}).total_pages ?? pages.length ?? 0));

            const highBoilerplate = pages.filter(p => Number(p.boilerplate_percent || 0) >= 45).length;
            const keywordStuffing = pages.filter(p => Number(p.keyword_stuffing_score || 0) >= 3).length;
            const hiddenContent = pages.filter(p => p.hidden_content === true).length;
            const contentHygiene = Math.max(0, 100 - Math.min(100, Math.round(((highBoilerplate + keywordStuffing + hiddenContent) / totalPagesBase) * 100)));

            const aiHighRisk = pages.filter(p => Number(p.ai_score || p.ai_risk_score || 0) >= 60).length;
            const aiTrust = Math.max(0, 100 - Math.min(100, Math.round((aiHighRisk / totalPagesBase) * 100)));

            const crawlRiskHigh = issues.filter(i => {
                const c = String(i.code || '').toLowerCase();
                return (c.includes('redirect') || c.includes('crawl_budget') || c.includes('http_status')) && String(i.severity || '').toLowerCase() === 'critical';
            }).length;
            const indexingStability = Math.max(0, 100 - Math.min(100, Math.round(((crawlRiskHigh + Number(metrics.non_https_pages || 0)) / totalPagesBase) * 100)));

            const accessibility = Math.max(0, 100 - Math.min(100, Math.round((Number(metrics.pages_without_alt || 0) / totalPagesBase) * 100)));

            const labels = ['Content Hygiene', 'AI Trust', 'Indexing', 'Accessibility'];
            const values = [contentHygiene, aiTrust, indexingStability, accessibility];
            if (values.some(v => v > 0)) {
                createRadarChart('ds-chart-sitepro-radar', labels, values, 'Quality Profile');
            }
        }

        if (taskType === 'link_profile_audit') {
            const summary = (r.results || r).summary || {};
            const dofollow = Number(summary.dofollow || 0);
            const nofollow = Number(summary.nofollow || 0);
            if (dofollow + nofollow > 0) {
                createPieChart('ds-chart-lp-dofollow', ['Dofollow', 'Nofollow'], [dofollow, nofollow], true);
            }
            const anchorBreakdown = (r.results || r).anchor_breakdown || {};
            const anchorLabels = Object.keys(anchorBreakdown).slice(0, 8);
            const anchorValues = anchorLabels.map(k => Number(anchorBreakdown[k] || 0));
            if (anchorLabels.length > 0) {
                createPieChart('ds-chart-lp-anchors', anchorLabels, anchorValues, false);
            }
        }

        if (taskType === 'redirect_checker') {
            const scenarios = r.scenarios || r.results || [];
            if (Array.isArray(scenarios)) {
                let passed = 0, warn = 0, err = 0;
                scenarios.forEach(s => {
                    const st = String(s.status || s.result || '').toLowerCase();
                    if (st === 'passed' || st === 'pass' || st === 'ok') passed++;
                    else if (st === 'warning' || st === 'warn') warn++;
                    else err++;
                });
                if (passed + warn + err > 0) {
                    createBarChart('ds-chart-redirect-summary', ['Passed', 'Warning', 'Error'],
                        [{data: [passed, warn, err], label: 'Scenarios', color: ['#10b981','#f59e0b','#ef4444']}]);
                }
            }
        }

        if (taskType === 'core_web_vitals') {
            const metrics = r.metrics || {};
            const lcp = Number(metrics.lcp?.field_value_ms ?? metrics.lcp?.lab_value_ms ?? 0);
            const inp = Number(metrics.inp?.field_value_ms ?? metrics.inp?.lab_value_ms ?? 0);
            const cls = Number(metrics.cls?.field_value ?? metrics.cls?.lab_value ?? 0);
            if (lcp > 0 || cls > 0) {
                createBarChart('ds-chart-cwv-metrics', ['LCP (ms)', 'INP (ms)', 'CLS (×100)'],
                    [{data: [Math.round(lcp), Math.round(inp), Math.round(cls * 100)], label: 'CWV', color: ['#0ea5e9','#8b5cf6','#f59e0b']}]);
            }
            const perfScore = Number(r.summary?.performance_score ?? 0);
            if (perfScore > 0) {
                createScoreGauge('ds-chart-cwv-score', perfScore, 'Performance');
            }
        }

        if (taskType === 'unified_audit') {
            const overallScore = Number(r.overall_score ?? 0);
            if (overallScore > 0) {
                createScoreGauge('ds-chart-unified-overall', overallScore, 'Overall');
            }
            const scores = r.scores || {};
            const scoreLabels = Object.keys(scores);
            const scoreValues = scoreLabels.map(k => Number(scores[k] || 0));
            if (scoreLabels.length > 0) {
                const readableLabels = scoreLabels.map(k => k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()));
                createBarChart('ds-chart-unified-scores', readableLabels,
                    [{data: scoreValues, label: 'Score', color: '#0f4c81'}]);
            }
        }

    } catch (e) {
        console.warn('Chart init error:', e);
    }
}

function showError(data) {
    document.getElementById('progress-section').classList.add('hidden');
    document.getElementById('error-section').classList.remove('hidden');
    document.getElementById('error-message').textContent = data.error || 'Неизвестная ошибка';
}

// Global variables for robots data
let robotsData = null;
let sitemapData = null;
let botData = null;
let mobileData = null;
let renderData = null;
let siteAuditProData = null;
let siteAuditProIssuesState = { issues: [], filtered: [], filter: 'all', query: '', limit: 20, sort: 'severity', owner: 'all' };
let onpageData = null;
let clusterizerData = null;
let redirectCheckerData = null;
let redirectCheckerFilter = 'all';
let coreWebVitalsData = null;
let linkProfileData = null;
let linkProfileRenderPayload = null;
let linkProfileRowsLimitByScope = {};
const LINK_PROFILE_DEFAULT_ROWS = 100;
const LINK_PROFILE_ROWS_STEP = 200;
let unifiedAuditData = null;
let batchResultsData = null;
let lastTaskResult = null;
let sitemapExportUrls = [];
let sitemapDuplicateLines = [];
let sitemapFilePreviewUrls = [];

function generateTextReport() {
    if (!robotsData) return 'Нет данных для отчета';
    const r = robotsData.results || robotsData;
    const groups = r.groups_detail || r.groups || [];

    let totalDisallow = 0;
    let totalAllow = 0;
    groups.forEach(g => {
        totalDisallow += (g.disallow || []).length;
        totalAllow += (g.allow || []).length;
    });

    let report = 'ОТЧЕТ АУДИТА ROBOTS.TXT\n';
    report += '='.repeat(60) + '\n';
    report += 'URL сайта: ' + (robotsData.url || '') + '\n';
    report += 'Сформирован: ' + new Date().toLocaleString() + '\n';
    report += '='.repeat(60) + '\n\n';

    report += 'СВОДКА\n' + '-'.repeat(30) + '\n';
    report += 'robots.txt найден: ' + (r.robots_txt_found ? 'да' : 'нет') + '\n';
    report += 'HTTP-статус: ' + (r.status_code ?? 'н/д') + '\n';
    report += 'Оценка качества: ' + (r.quality_score ?? 'н/д') + ' (' + (r.quality_grade || 'н/д') + ')\n';
    report += 'Готово к продакшну: ' + (r.production_ready ? 'да' : 'нет') + '\n';
    report += 'User-Agent групп: ' + (r.user_agents || 0) + '\n';
    report += 'Правила Disallow: ' + totalDisallow + '\n';
    report += 'Правила Allow: ' + totalAllow + '\n';
    report += 'Размер файла: ' + (r.content_length || 0) + ' байт\n';
    report += 'Строк: ' + (r.lines_count || 0) + '\n\n';

    const severity = r.severity_counts || {};
    report += 'КРИТИЧНОСТЬ\n' + '-'.repeat(30) + '\n';
    report += 'Критично: ' + (severity.critical ?? (r.critical_issues || r.issues || []).length) + '\n';
    report += 'Предупреждений: ' + (severity.warning ?? (r.warning_issues || r.warnings || []).length) + '\n';
    report += 'Инфо: ' + (severity.info ?? (r.info_issues || []).length) + '\n\n';

    const issues = r.critical_issues || r.issues || [];
    if (issues.length > 0) {
        report += 'КРИТИЧЕСКИЕ ПРОБЛЕМЫ\n' + '-'.repeat(30) + '\n';
        issues.forEach(i => report += '! ' + i + '\n');
        report += '\n';
    }

    const warnings = r.warning_issues || r.warnings || [];
    if (warnings.length > 0) {
        report += 'ПРЕДУПРЕЖДЕНИЯ\n' + '-'.repeat(30) + '\n';
        warnings.forEach(w => report += '- ' + w + '\n');
        report += '\n';
    }

    const infoIssues = r.info_issues || [];
    if (infoIssues.length > 0) {
        report += 'ИНФО\n' + '-'.repeat(30) + '\n';
        infoIssues.forEach(i => report += '- ' + i + '\n');
        report += '\n';
    }

    const sitemaps = (r.sitemaps || []).map(s => s && s.url ? s.url : s).filter(Boolean);
    if (sitemaps.length > 0) {
        report += 'SITEMAP-ФАЙЛЫ\n' + '-'.repeat(30) + '\n';
        sitemaps.forEach(sm => report += '- ' + sm + '\n');
        report += '\n';
    }

    const sitemapChecks = r.sitemap_checks || [];
    if (sitemapChecks.length > 0) {
        report += 'ПРОВЕРКИ URL ИЗ SITEMAP\n' + '-'.repeat(30) + '\n';
        sitemapChecks.forEach(check => {
            const status = check.ok === true ? 'ОК' : (check.ok === false ? 'ОШИБКА' : 'ПРОПУЩЕНО');
            const code = check.status_code ? ` HTTP ${check.status_code}` : '';
            const err = check.error ? ` | ${check.error}` : '';
            report += `- ${status}: ${check.url || ''}${code}${err}\n`;
        });
        report += '\n';
    }

    if (groups.length > 0) {
        report += 'ПРАВИЛА ПО ГРУППАМ\n' + '-'.repeat(30) + '\n';
        groups.forEach((group, idx) => {
            report += `\nГруппа ${idx + 1}: ${(group.user_agents || []).join(', ')}\n`;
            (group.disallow || []).forEach(d => report += `  Disallow: ${d.path} (строка ${d.line})\n`);
            (group.allow || []).forEach(a => report += `  Allow: ${a.path} (строка ${a.line})\n`);
        });
        report += '\n';
    }

    const syntaxErrors = r.syntax_errors || [];
    if (syntaxErrors.length > 0) {
        report += 'СИНТАКСИЧЕСКИЕ ОШИБКИ\n' + '-'.repeat(30) + '\n';
        syntaxErrors.forEach(err => {
            report += `- строка ${err.line || '?'}: ${err.error || ''}\n`;
            if (err.content) report += `  контент: ${err.content}\n`;
        });
        report += '\n';
    }

    const recommendations = r.recommendations || [];
    if (recommendations.length > 0) {
        report += 'РЕКОМЕНДАЦИИ\n' + '-'.repeat(30) + '\n';
        recommendations.forEach(rec => report += '- ' + rec + '\n');
        report += '\n';
    }

    const topFixes = r.top_fixes || [];
    if (topFixes.length > 0) {
        report += 'ТОП ИСПРАВЛЕНИЙ\n' + '-'.repeat(30) + '\n';
        topFixes.forEach(fix => {
            report += '[' + String((fix.priority || 'medium')).toUpperCase() + '] ' + (fix.title || 'Исправление') + '\n';
            if (fix.why) report += '  Почему: ' + fix.why + '\n';
            if (fix.action) report += '  Действие: ' + fix.action + '\n';
        });
        report += '\n';
    }

    const httpStatusAnalysis = r.http_status_analysis || {};
    const httpNotes = httpStatusAnalysis.notes || [];
    if (httpNotes.length > 0) {
        report += 'АНАЛИЗ HTTP-СТАТУСА\n' + '-'.repeat(30) + '\n';
        report += 'Статус: ' + (httpStatusAnalysis.status_code ?? r.status_code ?? 'н/д') + '\n';
        httpNotes.forEach(note => report += '- ' + note + '\n');
        report += '\n';
    }

    const unsupported = r.unsupported_directives || [];
    if (unsupported.length > 0) {
        report += 'НЕПОДДЕРЖИВАЕМЫЕ ДИРЕКТИВЫ\n' + '-'.repeat(30) + '\n';
        unsupported.forEach(item => {
            report += `- строка ${item.line || '?'}: ${item.directive || ''}${item.value ? `: ${item.value}` : ''}\n`;
        });
        report += '\n';
    }

    const hostValidation = r.host_validation || {};
    const hostWarnings = hostValidation.warnings || [];
    const hostList = hostValidation.hosts || r.hosts || [];
    if (hostList.length > 0 || hostWarnings.length > 0) {
        report += 'ПРОВЕРКА HOST\n' + '-'.repeat(30) + '\n';
        if (hostList.length > 0) report += 'Hosts: ' + hostList.join(', ') + '\n';
        hostWarnings.forEach(w => report += '- ' + w + '\n');
        report += '\n';
    }

    const conflictScan = r.directive_conflicts || {};
    const conflictDetails = conflictScan.details || [];
    if (conflictDetails.length > 0) {
        report += 'КОНФЛИКТЫ ДИРЕКТИВ\n' + '-'.repeat(30) + '\n';
        conflictDetails.forEach(c => {
            const marker = c.path || c.groups || '';
            report += `- ${c.type || 'conflict'} | ua=${c.user_agent || ''}${marker ? ` | ${marker}` : ''}\n`;
        });
        report += '\n';
    }

    const longestMatch = r.longest_match_analysis || {};
    const longestNotes = longestMatch.notes || [];
    if (longestNotes.length > 0) {
        report += 'ПРИМЕЧАНИЯ LONGEST-MATCH\n' + '-'.repeat(30) + '\n';
        longestNotes.forEach(n => report += '- ' + n + '\n');
        report += '\n';
    }

    const paramRecs = r.param_recommendations || [];
    if (paramRecs.length > 0) {
        report += 'РЕКОМЕНДАЦИИ ПО YANDEX CLEAN-PARAM\n' + '-'.repeat(30) + '\n';
        paramRecs.forEach(n => report += '- ' + n + '\n');
        report += '\n';
    }

    const rawContent = r.raw_content || '';
    if (rawContent) {
        report += 'СЫРОЙ ROBOTS.TXT\n' + '-'.repeat(30) + '\n';
        report += rawContent + '\n';
    }

    return report;
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        const notification = document.getElementById('copy-notification');
        notification.classList.remove('translate-y-20');
        setTimeout(() => {
            notification.classList.add('translate-y-20');
        }, 2000);
    }).catch(err => {
        console.error('Clipboard copy error:', err);
    });
}

function escapeHtml(text) {
    return String(text ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function sanitizeHttpUrl(rawUrl) {
    if (!rawUrl) return '';
    try {
        const parsed = new URL(rawUrl, window.location.origin);
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            return '';
        }
        return parsed.href;
    } catch (_) {
        return '';
    }
}

function formatRobotsRawWithLineNumbers(rawText) {
    const lines = String(rawText || '').replace(/\r\n/g, '\n').split('\n');
    return lines.map((line, idx) => {
        const n = String(idx + 1).padStart(4, ' ');
        return `<div class="flex"><span class="select-none text-gray-500 pr-4 w-14 text-right border-r border-gray-700 mr-4">${n}</span><span class="whitespace-pre-wrap break-all flex-1">${escapeHtml(line)}</span></div>`;
    }).join('');
}

function copyCurrentTaskJson() {
    if (!lastTaskResult) {
        alert('Нет данных для копирования');
        return;
    }
    copyToClipboard(JSON.stringify(lastTaskResult, null, 2));
}

function copyOnpageRecommendations() {
    if (!lastTaskResult || lastTaskResult.task_type !== 'onpage_audit') {
        alert('Нет данных OnPage для копирования');
        return;
    }
    const r = (lastTaskResult.results || {});
    const recs = (r.recommendations || []);
    if (!recs.length) {
        alert('Пока нет рекомендаций');
        return;
    }
    const lines = recs.map((item, idx) => `${idx + 1}. ${item}`);
    copyToClipboard(lines.join('\n'));
}

function copyOnpageTopFixes() {
    if (!lastTaskResult || lastTaskResult.task_type !== 'onpage_audit') {
        alert('Нет данных OnPage для копирования');
        return;
    }
    const r = (lastTaskResult.results || {});
    const queue = (r.priority_queue || []);
    const fallbackFixes = (r.top_fixes || []);
    const list = queue.length > 0
        ? queue.slice(0, 10).map((q, idx) =>
            `${idx + 1}. [${(q.bucket || 'Позже').toUpperCase()}] ${(q.title || q.code || 'Проблема')} | Критичность: ${(q.severity || 'info').toUpperCase()} | Приоритет: ${q.priority_score ?? 0} | Трудозатраты: ${q.effort ?? 0}`
        )
        : fallbackFixes.slice(0, 10).map((f, idx) =>
            `${idx + 1}. [${String((f.priority || 'medium')).toUpperCase()}] ${f.title || 'Исправление'}${f.action ? ` | Действие: ${f.action}` : ''}`
        );
    if (!list.length) {
        alert('Топ исправлений пока недоступен');
        return;
    }
    copyToClipboard(list.join('\n'));
}

function filterOnpageIssues(mode) {
    const wrapper = document.getElementById('onpage-issues-list');
    if (!wrapper) return;
    const cards = wrapper.querySelectorAll('[data-issue-severity]');
    let visibleCount = 0;
    cards.forEach((card) => {
        const severity = String(card.getAttribute('data-issue-severity') || 'info').toLowerCase();
        const shouldShow = mode === 'all' ? true : severity === mode;
        card.classList.toggle('hidden', !shouldShow);
        if (shouldShow) visibleCount += 1;
    });
    const counter = document.getElementById('onpage-issues-count');
    if (counter) {
        counter.textContent = `${visibleCount} / ${cards.length}`;
    }
    const buttons = document.querySelectorAll('[data-onpage-issues-filter]');
    buttons.forEach((btn) => {
        const isActive = btn.getAttribute('data-onpage-issues-filter') === mode;
        btn.classList.toggle('bg-slate-900', isActive);
        btn.classList.toggle('text-white', isActive);
        btn.classList.toggle('bg-white', !isActive);
        btn.classList.toggle('text-slate-700', !isActive);
    });
}

function filterSiteAuditProIssues() {
    const source = Array.isArray(siteAuditProIssuesState.issues) ? siteAuditProIssuesState.issues : [];
    const query = String(siteAuditProIssuesState.query || '').trim().toLowerCase();
    const mode = String(siteAuditProIssuesState.filter || 'all').toLowerCase();
    const owner = String(siteAuditProIssuesState.owner || 'all');
    const ownerHintByCode = (code) => {
        const codeL = String(code || '').toLowerCase();
        if (/(title|meta|h1|keyword|content|ai_|duplicate_)/.test(codeL)) return 'Content+SEO';
        if (/(schema|structured|hreflang|canonical|index|http_status)/.test(codeL)) return 'SEO+Dev';
        if (/(security|cache|compression|https|crawl_budget|redirect)/.test(codeL)) return 'Dev+Infra';
        return 'SEO';
    };
    return source.filter((issue) => {
        const severity = String(issue.severity || 'info').toLowerCase();
        if (mode !== 'all' && severity !== mode) return false;
        if (owner !== 'all' && ownerHintByCode(issue.code) !== owner) return false;
        if (!query) return true;
        const haystack = [
            issue.code,
            issue.title,
            issue.details,
            issue.url,
            issue.severity,
        ].map(v => String(v || '').toLowerCase()).join(' ');
        return haystack.includes(query);
    });
}

function renderSiteAuditProIssuesExplorer() {
    const wrapper = document.getElementById('sitepro-issues-list');
    if (!wrapper) return;

    const filtered = filterSiteAuditProIssues();
    const sorted = [...filtered];
    if (siteAuditProIssuesState.sort === 'url') {
        sorted.sort((a, b) => String(a.url || '').localeCompare(String(b.url || '')));
    } else {
        const rank = { critical: 0, warning: 1, info: 2 };
        sorted.sort((a, b) => {
            const sa = String(a.severity || 'info').toLowerCase();
            const sb = String(b.severity || 'info').toLowerCase();
            const bySeverity = (rank[sa] ?? 3) - (rank[sb] ?? 3);
            if (bySeverity !== 0) return bySeverity;
            return String(a.code || '').localeCompare(String(b.code || ''));
        });
    }
    siteAuditProIssuesState.filtered = sorted;
    const limit = Number(siteAuditProIssuesState.limit || 20);
    const visible = sorted.slice(0, limit);

    const counter = document.getElementById('sitepro-issues-count');
    if (counter) counter.textContent = `${visible.length} / ${sorted.length}`;

    const buttons = document.querySelectorAll('[data-sitepro-issues-filter]');
    buttons.forEach((btn) => {
        const btnMode = btn.getAttribute('data-sitepro-issues-filter') || 'all';
        const isActive = btnMode === siteAuditProIssuesState.filter;
        btn.classList.toggle('bg-slate-900', isActive);
        btn.classList.toggle('text-white', isActive);
        btn.classList.toggle('bg-white', !isActive);
        btn.classList.toggle('text-slate-700', !isActive);
    });

    if (!visible.length) {
        wrapper.innerHTML = '<div class="text-sm text-gray-500">По выбранным фильтрам проблемы не найдены.</div>';
        return;
    }

    wrapper.innerHTML = visible.map((issue) => {
        const severity = String(issue.severity || 'info').toLowerCase();
        const classes = severity === 'critical'
            ? { card: 'border-red-500 bg-red-50', badge: 'bg-red-100 text-red-700' }
            : severity === 'warning'
                ? { card: 'border-yellow-500 bg-yellow-50', badge: 'bg-yellow-100 text-yellow-700' }
                : { card: 'border-blue-500 bg-blue-50', badge: 'bg-blue-100 text-blue-700' };
        return `
            <div class="text-sm border-l-4 ${classes.card} p-3 rounded-r mb-2">
                <div class="flex flex-wrap items-center gap-2">
                    <span class="px-2 py-0.5 rounded-full text-xs font-semibold ${classes.badge}">${escapeHtml(severity.toUpperCase())}</span>
                    <span class="font-semibold">${escapeHtml(issue.code || 'issue')}</span>
                </div>
                <div class="mt-1">${escapeHtml(issue.title || '')}</div>
                ${issue.details ? `<div class="text-gray-700 mt-1">${escapeHtml(issue.details).replace(/\n/g, '<br>')}</div>` : ''}
                ${issue.url ? `<div class="text-gray-600 mt-1 break-all">${escapeHtml(issue.url)}</div>` : ''}
            </div>
        `;
    }).join('');
}

function setSiteAuditProIssuesFilter(mode) {
    siteAuditProIssuesState.filter = String(mode || 'all').toLowerCase();
    renderSiteAuditProIssuesExplorer();
}

function updateSiteAuditProIssuesQuery(value) {
    siteAuditProIssuesState.query = String(value || '');
    renderSiteAuditProIssuesExplorer();
}

function updateSiteAuditProIssuesLimit(value) {
    const parsed = Number(value || 20);
    siteAuditProIssuesState.limit = Number.isFinite(parsed) ? parsed : 20;
    renderSiteAuditProIssuesExplorer();
}

function updateSiteAuditProIssuesSort(value) {
    siteAuditProIssuesState.sort = String(value || 'severity');
    renderSiteAuditProIssuesExplorer();
}

function updateSiteAuditProIssuesOwner(value) {
    siteAuditProIssuesState.owner = String(value || 'all');
    renderSiteAuditProIssuesExplorer();
}

function applySiteProOwnerPreset(owner) {
    siteAuditProIssuesState.owner = String(owner || 'all');
    const ownerInput = document.getElementById('sitepro-issues-owner');
    if (ownerInput) ownerInput.value = siteAuditProIssuesState.owner;
    renderSiteAuditProIssuesExplorer();
}

function applySiteProIssuePreset(query, mode = 'all') {
    siteAuditProIssuesState.filter = String(mode || 'all').toLowerCase();
    siteAuditProIssuesState.query = String(query || '');
    const queryInput = document.getElementById('sitepro-issues-search');
    if (queryInput) queryInput.value = siteAuditProIssuesState.query;
    renderSiteAuditProIssuesExplorer();
}

function initSiteAuditProIssuesExplorer(result) {
    const r = result.results || {};
    const issues = Array.isArray(r.issues) ? r.issues : [];
    siteAuditProIssuesState = {
        issues,
        filtered: issues,
        filter: 'all',
        query: '',
        limit: 20,
        sort: 'severity',
        owner: 'all',
    };
    const queryInput = document.getElementById('sitepro-issues-search');
    if (queryInput) queryInput.value = '';
    const limitInput = document.getElementById('sitepro-issues-limit');
    if (limitInput) limitInput.value = '20';
    const sortInput = document.getElementById('sitepro-issues-sort');
    if (sortInput) sortInput.value = 'severity';
    const ownerInput = document.getElementById('sitepro-issues-owner');
    if (ownerInput) ownerInput.value = 'all';
    renderSiteAuditProIssuesExplorer();
}

function downloadTextReport() {
    const report = generateTextReport();
    const blob = new Blob([report], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = buildReportFilename('robots-report', 'txt', robotsData?.url || '');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function downloadLinesAsTxt(lines, filename) {
    const content = (lines || []).join('\n');
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = href;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(href);
}

function downloadLinesAsTxtParts(lines, baseFilename, partSize = 25000) {
    const arr = lines || [];
    if (arr.length === 0) return;
    let part = 1;
    for (let i = 0; i < arr.length; i += partSize) {
        const slice = arr.slice(i, i + partSize);
        const partName = `${baseFilename}_part-${String(part).padStart(3, '0')}.txt`;
        downloadLinesAsTxt(slice, partName);
        part += 1;
    }
}

function downloadCurrentSitemapUrls(baseFilename) {
    downloadLinesAsTxt(sitemapExportUrls, `${baseFilename}.txt`);
}

function downloadCurrentSitemapUrlsParts(baseFilename, partSize = 25000) {
    downloadLinesAsTxtParts(sitemapExportUrls, baseFilename, partSize);
}

function downloadCurrentSitemapDuplicates(filename) {
    downloadLinesAsTxt(sitemapDuplicateLines, filename);
}

function downloadSitemapFilePreview(index, filename) {
    const lines = (sitemapFilePreviewUrls[index] || []);
    downloadLinesAsTxt(lines, filename);
}

async function downloadSitemapXlsxReport() {
    if (!sitemapData) {
        alert('Нет данных sitemap для экспорта');
        return;
    }
    try {
        const response = await fetch('/api/export/sitemap-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: sitemapData.task_id || taskId })
        });
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'sitemap-report', 'xlsx', sitemapData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания sitemap xlsx:', error);
        alert('Не удалось скачать XLSX-отчет по sitemap');
    }
}

async function downloadSitemapDocxReport() {
    if (!sitemapData) {
        alert('Нет данных sitemap для экспорта');
        return;
    }
    try {
        const response = await fetch('/api/export/sitemap-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: sitemapData.task_id || taskId })
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.error || 'Не удалось сформировать DOCX-отчет по sitemap');
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = href;
        a.download = filenameFromResponse(response, 'sitemap-report', 'docx', sitemapData?.url || '');
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания sitemap docx:', error);
        alert(error.message || 'Не удалось скачать DOCX-отчет по sitemap');
    }
}

async function runSitemapCheckFromRobots(sitemapUrl) {
    if (!sitemapUrl) {
        alert('Пустой URL sitemap');
        return;
    }
    try {
        const response = await fetch('/api/tasks/sitemap-validate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: sitemapUrl })
        });
        const data = await response.json();
        if (!response.ok || !data.task_id) {
            throw new Error(data.error || data.detail || `HTTP ${response.status}`);
        }
        addTaskToLocalHistory({
            taskId: data.task_id,
            tool: 'sitemap-validate',
            url: sitemapUrl,
            status: data.status || 'SUCCESS',
            timestamp: new Date().toISOString()
        });
        window.location.href = `/results/${data.task_id}`;
    } catch (error) {
        console.error('Ошибка запуска валидации sitemap:', error);
        alert('Не удалось запустить валидацию sitemap: ' + (error.message || error));
    }
}

async function downloadBotXlsxReport() {
    if (!botData) {
        alert('Нет данных проверки ботов');
        return;
    }
    try {
        const response = await fetch('/api/export/bot-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: botData.task_id || taskId })
        });
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'bot-report', 'xlsx', botData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания bot XLSX:', error);
        alert('Не удалось скачать XLSX-отчет по ботам');
    }
}

async function downloadBotDocxReport() {
    if (!botData) {
        alert('Нет данных проверки ботов');
        return;
    }
    try {
        const response = await fetch('/api/export/bot-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: botData.task_id || taskId })
        });
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'bot-report', 'docx', botData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания bot DOCX:', error);
        alert('Не удалось скачать DOCX-отчет по ботам');
    }
}

function copyBotPlaybooksAsJira() {
    if (!botData) {
        alert('Нет данных проверки ботов');
        return;
    }
    const r = botData.results || botData;
    const playbooks = r.playbooks || [];
    if (playbooks.length === 0) {
        alert('Нет плейбуков для экспорта в Jira');
        return;
    }

    const bucketName = (score) => {
        const n = Number(score || 0);
        if (n >= 20) return 'Сейчас';
        if (n >= 12) return 'Далее';
        return 'Позже';
    };

    const blocks = playbooks.slice(0, 20).map((p, idx) => {
        const actions = (p.actions || []).slice(0, 6).map((a) => `- ${a}`).join('\n') || '- Определить шаги внедрения';
        const priority = Number(p.priority_score || 0);
        const owner = p.owner || 'Команда';
        const blockerCode = p.blocker_code || 'general';
        const title = p.title || `Исправление доступности ботов ${idx + 1}`;
        return [
            `Сводка: [Проверка доступности ботов] ${title}`,
            `Labels: bot-check,${String(blockerCode).toLowerCase().replace(/[^a-z0-9_-]+/g, '-')},${bucketName(priority).toLowerCase()}`,
            `Приоритет: ${priority}`,
            `Владелец: ${owner}`,
            'Описание:',
            `- Источник: Проверка доступности ботов`,
            `- Код блокера: ${blockerCode}`,
            `- Бакет спринта: ${bucketName(priority)}`,
            '- План действий:',
            actions,
        ].join('\n');
    });

    const jiraText = blocks.join('\n\n---\n\n');
    copyToClipboard(jiraText);
}

function copyBotOwnerTasks(owner) {
    if (!botData) {
        alert('Нет данных проверки ботов');
        return;
    }
    const r = botData.results || botData;
    const byOwner = ((r.action_center || {}).by_owner || {});
    const rows = byOwner[owner] || [];
    if (!rows.length) {
        alert('Нет задач для выбранного владельца');
        return;
    }
    const text = rows.map((p, idx) => {
        const actions = (p.actions || []).map((a) => `- ${a}`).join('\n');
        return `${idx + 1}. ${p.title || 'Действие'} (приоритет ${p.priority_score || 0})\n${actions}`;
    }).join('\n\n');
    copyToClipboard(text);
}

function downloadBotTrendHistoryJson() {
    if (!botData) {
        alert('Нет данных проверки ботов');
        return;
    }
    const url = botData.url || '';
    const snapshots = getBotSnapshotsForUrl(url);
    if (!snapshots.length) {
        alert('Для этого домена нет истории тренда');
        return;
    }
    const payload = {
        url,
        domain: extractDomain(url),
        generated_at: new Date().toISOString(),
        snapshots,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.download = buildReportFilename('bot-trends', 'json', url || payload.domain || '');
    a.href = href;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(href);
}

async function downloadWordReport() {
    if (!robotsData) {
        alert('Нет данных отчета');
        return;
    }
    
    try {
        const response = await fetch('/api/export/robots', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ task_id: robotsData.task_id || robotsData.url })
        });
        
        if (response.ok) {
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filenameFromResponse(response, 'robots-report', 'docx', robotsData?.url || '');
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } else {
            const error = await response.json();
            alert('Ошибка генерации отчета: ' + (error.error || response.statusText));
        }
    } catch (error) {
        console.error('Ошибка скачивания Word-отчета:', error);
        alert('Ошибка скачивания отчета');
    }
}

async function downloadMobileDocxReport() {
    if (!mobileData) {
        alert('Нет данных мобильной проверки');
        return;
    }
    try {
        const response = await fetch('/api/export/mobile-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: mobileData.task_id || taskId })
        });
        if (!response.ok) {
            const payloadType = response.headers.get('content-type') || '';
            if (payloadType.includes('application/json')) {
                const err = await response.json();
                throw new Error(err.error || err.detail || `HTTP ${response.status}`);
            }
            const rawText = await response.text();
            throw new Error(rawText || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'mobile-report', 'docx', mobileData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания mobile DOCX:', error);
        alert('Не удалось скачать DOCX-отчет');
    }
}

async function downloadMobileXlsxReport() {
    if (!mobileData) {
        alert('Нет данных мобильной проверки');
        return;
    }
    try {
        const response = await fetch('/api/export/mobile-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: mobileData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'mobile-issues', 'xlsx', mobileData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания мобильного XLSX:', error);
        alert(error.message || 'Не удалось скачать XLSX-отчет');
    }
}

async function downloadRenderDocxReport() {
    if (!renderData) {
        alert('Нет данных рендер-аудита');
        return;
    }
    try {
        const response = await fetch('/api/export/render-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: renderData.task_id || taskId })
        });
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'render-report', 'docx', renderData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания render DOCX:', error);
        alert('Не удалось скачать DOCX-отчет');
    }
}

async function downloadRenderXlsxReport() {
    if (!renderData) {
        alert('Нет данных рендер-аудита');
        return;
    }
    try {
        const response = await fetch('/api/export/render-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: renderData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'render-issues', 'xlsx', renderData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания render XLSX:', error);
        alert(error.message || 'Не удалось скачать XLSX-отчет');
    }
}

async function downloadSiteAuditProXlsxReport() {
    if (!siteAuditProData) {
        alert('Нет данных Site Audit Pro');
        return;
    }
    try {
        const response = await fetch('/api/export/site-audit-pro-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: siteAuditProData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'site-audit-pro', 'xlsx', siteAuditProData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания Site Audit Pro XLSX:', error);
        alert(error.message || 'Не удалось скачать XLSX-отчет');
    }
}

async function downloadSiteAuditProDocxReport() {
    if (!siteAuditProData) {
        alert('Нет данных Site Audit Pro');
        return;
    }
    try {
        const response = await fetch('/api/export/site-audit-pro-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: siteAuditProData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'site-audit-pro', 'docx', siteAuditProData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания Site Audit Pro DOCX:', error);
        alert(error.message || 'Не удалось скачать DOCX-отчет');
    }
}

async function downloadOnpageDocxReport() {
    if (!onpageData) {
        alert('Нет данных OnPage-аудита');
        return;
    }
    try {
        const response = await fetch('/api/export/onpage-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: onpageData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'onpage-report', 'docx', onpageData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания OnPage DOCX:', error);
        alert(error.message || 'Не удалось скачать DOCX-отчет');
    }
}

async function downloadOnpageXlsxReport() {
    if (!onpageData) {
        alert('Нет данных OnPage-аудита');
        return;
    }
    try {
        const response = await fetch('/api/export/onpage-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: onpageData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'onpage-report', 'xlsx', onpageData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания OnPage XLSX:', error);
        alert(error.message || 'Не удалось скачать XLSX-отчет');
    }
}

async function downloadCoreWebVitalsDocxReport() {
    if (!coreWebVitalsData) {
        alert('Нет данных Core Web Vitals');
        return;
    }
    try {
        const response = await fetch('/api/export/core-web-vitals-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: coreWebVitalsData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'core-web-vitals', 'docx', coreWebVitalsData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания Core Web Vitals DOCX:', error);
        alert(error.message || 'Не удалось скачать DOCX-отчет');
    }
}

async function downloadCoreWebVitalsXlsxReport() {
    if (!coreWebVitalsData) {
        alert('Нет данных Core Web Vitals');
        return;
    }
    try {
        const response = await fetch('/api/export/core-web-vitals-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: coreWebVitalsData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'core-web-vitals', 'xlsx', coreWebVitalsData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания Core Web Vitals XLSX:', error);
        alert(error.message || 'Не удалось скачать XLSX-отчет');
    }
}

function downloadCoreWebVitalsJsonReport() {
    if (!coreWebVitalsData) {
        alert('Нет данных Core Web Vitals');
        return;
    }
    const payload = {
        task_id: coreWebVitalsData.task_id || taskId,
        exported_at: new Date().toISOString(),
        data: coreWebVitalsData,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.download = buildReportFilename('core-web-vitals', 'json', coreWebVitalsData?.url || '');
    a.href = href;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(href);
}

function downloadCoreWebVitalsCsvReport() {
    if (!coreWebVitalsData) {
        alert('Нет данных Core Web Vitals');
        return;
    }
    const resultData = coreWebVitalsData.result || coreWebVitalsData;
    const r = resultData.results || resultData;
    const lines = [];
    const toCsv = (value) => {
        const text = String(value == null ? '' : value);
        if (text.includes('"') || text.includes(',') || text.includes('\n')) {
            return `"${text.replace(/"/g, '""')}"`;
        }
        return text;
    };
    const mode = String(r.mode || 'single').toLowerCase();
    if (mode === 'competitor') {
        lines.push(['url', 'role', 'status', 'score', 'cwv', 'lcp_ms', 'inp_ms', 'cls', 'score_delta_vs_primary', 'lcp_delta_ms_vs_primary', 'inp_delta_ms_vs_primary', 'cls_delta_vs_primary', 'top_focus', 'error'].join(','));
        const primary = r.primary || {};
        const primarySummary = primary.summary || {};
        const primaryMetrics = primary.metrics || {};
        lines.push([
            primary.url || '',
            'primary',
            primary.status || '',
            primarySummary.performance_score ?? '',
            primarySummary.core_web_vitals_status || '',
            primaryMetrics.lcp?.field_value_ms ?? primaryMetrics.lcp?.lab_value_ms ?? '',
            primaryMetrics.inp?.field_value_ms ?? primaryMetrics.inp?.lab_value_ms ?? '',
            primaryMetrics.cls?.field_value ?? primaryMetrics.cls?.lab_value ?? '',
            '',
            '',
            '',
            '',
            ((primary.opportunities || [])[0] || {}).title || ((primary.recommendations || [])[0] || ''),
            primary.error || '',
        ].map(toCsv).join(','));
        (r.comparison_rows || []).forEach((row) => {
            lines.push([
                row.url || '',
                'competitor',
                row.status || '',
                row.score ?? '',
                row.cwv_status || '',
                row.lcp_ms ?? '',
                row.inp_ms ?? '',
                row.cls ?? '',
                row.score_delta_vs_primary ?? '',
                row.lcp_delta_ms_vs_primary ?? '',
                row.inp_delta_ms_vs_primary ?? '',
                row.cls_delta_vs_primary ?? '',
                row.top_focus || '',
                row.error || '',
            ].map(toCsv).join(','));
        });
    } else if (mode === 'batch' || Array.isArray(r.sites)) {
        lines.push(['url', 'status', 'score', 'cwv', 'lcp_ms', 'inp_ms', 'cls', 'top_focus', 'error'].join(','));
        const sites = Array.isArray(r.sites) ? r.sites : [];
        sites.forEach((site) => {
            const metrics = site.metrics || {};
            const summary = site.summary || {};
            const opps = Array.isArray(site.opportunities) ? site.opportunities : [];
            const topFocus = (opps[0] || {}).title || ((site.recommendations || [])[0] || '');
            const lcp = metrics.lcp?.field_value_ms ?? metrics.lcp?.lab_value_ms ?? '';
            const inp = metrics.inp?.field_value_ms ?? metrics.inp?.lab_value_ms ?? '';
            const cls = metrics.cls?.field_value ?? metrics.cls?.lab_value ?? '';
            const row = [
                site.url || '',
                site.status || '',
                summary.performance_score ?? '',
                summary.core_web_vitals_status || '',
                lcp,
                inp,
                cls,
                topFocus,
                site.error || '',
            ];
            lines.push(row.map(toCsv).join(','));
        });
    } else {
        const summary = r.summary || {};
        const metrics = r.metrics || {};
        lines.push(['url', 'strategy', 'performance_score', 'cwv_status', 'lcp_ms', 'inp_ms', 'cls', 'fcp_ms', 'ttfb_ms'].join(','));
        lines.push([
            resultData.url || r.url || '',
            r.strategy || '',
            summary.performance_score ?? '',
            summary.core_web_vitals_status || '',
            metrics.lcp?.field_value_ms ?? metrics.lcp?.lab_value_ms ?? '',
            metrics.inp?.field_value_ms ?? metrics.inp?.lab_value_ms ?? '',
            metrics.cls?.field_value ?? metrics.cls?.lab_value ?? '',
            metrics.fcp?.lab_value_ms ?? '',
            metrics.ttfb?.lab_value_ms ?? '',
        ].map(toCsv).join(','));
        lines.push('');
        lines.push(['opportunity_id', 'title', 'priority', 'score', 'savings_ms', 'savings_kib', 'display_value'].join(','));
        (r.opportunities || []).forEach((opp) => {
            lines.push([
                opp.id || '',
                opp.title || '',
                opp.priority || '',
                opp.score ?? '',
                opp.savings_ms ?? '',
                opp.savings_kib ?? '',
                opp.display_value || '',
            ].map(toCsv).join(','));
        });
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.download = buildReportFilename('core-web-vitals', 'csv', coreWebVitalsData?.url || '');
    a.href = href;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(href);
}

function copyCoreWebVitalsSummary() {
    if (!coreWebVitalsData) {
        alert('Нет данных Core Web Vitals');
        return;
    }
    const resultData = coreWebVitalsData.result || coreWebVitalsData;
    const r = resultData.results || resultData;
    const mode = String(r.mode || 'single').toLowerCase();
    if (mode === 'competitor') {
        const summary = r.summary || {};
        const benchmark = r.benchmark || {};
        const primary = r.primary || {};
        const primarySummary = primary.summary || {};
        const lines = [
            `Core Web Vitals Competitor Analysis`,
            `Primary: ${summary.primary_url || primary.url || ''}`,
            `Primary score: ${primarySummary.performance_score ?? summary.primary_score ?? 'n/a'}`,
            `Primary CWV: ${primarySummary.core_web_vitals_status || summary.primary_cwv_status || 'unknown'}`,
            `Primary rank: ${summary.primary_rank || 'n/a'}`,
            `Leader: ${summary.market_leader_url || benchmark.market_leader_url || 'n/a'}`,
            `Competitors total: ${summary.competitors_total ?? ((r.competitors || []).length ?? 0)}`,
        ];
        const gaps = (r.gaps_for_primary || []).slice(0, 5);
        if (gaps.length) {
            lines.push('');
            lines.push('Primary gaps:');
            gaps.forEach((item, idx) => lines.push(`${idx + 1}. ${item}`));
        }
        copyToClipboard(lines.join('\n'));
        return;
    }
    if (mode === 'batch' || Array.isArray(r.sites)) {
        const summary = r.summary || {};
        const lines = [
            `Core Web Vitals Batch`,
            `URLs: ${summary.total_urls || 0}`,
            `Success: ${summary.successful_urls || 0}`,
            `Errors: ${summary.failed_urls || 0}`,
            `Avg score: ${summary.average_performance_score ?? 'n/a'}`,
            `CWV status: ${summary.core_web_vitals_status || 'unknown'}`,
        ];
        const common = (r.common_opportunities || []).slice(0, 5);
        if (common.length) {
            lines.push('');
            lines.push('Top common opportunities:');
            common.forEach((item, idx) => {
                lines.push(`${idx + 1}. ${item.title || item.id} (count: ${item.count || 0})`);
            });
        }
        copyToClipboard(lines.join('\n'));
        return;
    }
    const summary = r.summary || {};
    const lines = [
        `Core Web Vitals`,
        `URL: ${resultData.url || r.url || ''}`,
        `Strategy: ${String(r.strategy || '').toUpperCase()}`,
        `Score: ${summary.performance_score ?? 'n/a'}`,
        `CWV: ${summary.core_web_vitals_status || 'unknown'}`,
        `Health: ${summary.health_index ?? 'n/a'} (${summary.risk_level || 'n/a'})`,
    ];
    const plan = (r.action_plan || []).slice(0, 5);
    if (plan.length) {
        lines.push('');
        lines.push('Action plan:');
        plan.forEach((item, idx) => {
            lines.push(`${idx + 1}. [${item.priority || 'P3'}] ${item.action || ''}`);
        });
    }
    copyToClipboard(lines.join('\n'));
}

function renderMarkdownLinks(text) {
    const source = String(text || '');
    const pattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
    let out = '';
    let lastIndex = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
        out += escapeHtml(source.slice(lastIndex, match.index));
        const href = sanitizeHttpUrl(match[2]);
        const label = escapeHtml(match[1]);
        if (href) {
            out += `<a href="${href}" target="_blank" rel="noopener noreferrer" class="text-cyan-700 hover:text-cyan-900 underline">${label}</a>`;
        } else {
            out += escapeHtml(match[0]);
        }
        lastIndex = pattern.lastIndex;
    }
    out += escapeHtml(source.slice(lastIndex));
    return out;
}

async function downloadClusterizerXlsxReport() {
    if (!clusterizerData) {
        alert('Нет данных кластеризатора');
        return;
    }
    try {
        const response = await fetch('/api/export/clusterizer-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: clusterizerData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.download = filenameFromResponse(response, 'clusterizer-report', 'xlsx', clusterizerData?.url || '');
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
    } catch (error) {
        console.error('Ошибка скачивания clusterizer XLSX:', error);
        alert(error.message || 'Не удалось скачать XLSX-отчет');
    }
}

function setLinkProfileExportState(kind, isLoading) {
    const buttonId = kind === 'docx' ? 'lp-export-docx-btn' : 'lp-export-xlsx-btn';
    const btn = document.getElementById(buttonId);
    if (!btn) return;
    btn.disabled = Boolean(isLoading);
    btn.setAttribute('aria-busy', isLoading ? 'true' : 'false');
    btn.classList.toggle('opacity-60', Boolean(isLoading));
    btn.classList.toggle('cursor-not-allowed', Boolean(isLoading));
    const label = btn.querySelector('[data-lp-export-label]');
    if (label) {
        label.textContent = isLoading ? 'Формирование...' : (kind === 'docx' ? 'DOCX' : 'XLSX');
    }
}

async function downloadLinkProfileDocxReport() {
    if (!linkProfileData) {
        alert('Нет данных аудита ссылочного профиля');
        return;
    }
    setLinkProfileExportState('docx', true);
    setLinkProfileFeedback('Формируем DOCX-отчет...', 'info');
    try {
        const response = await fetch('/api/export/link-profile-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: linkProfileData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const filename = filenameFromResponse(response, 'link-profile-report', 'docx', linkProfileData?.url || '');
        a.download = filename;
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
        setLinkProfileFeedback(`DOCX готов: ${filename}`, 'success');
    } catch (error) {
        console.error('Ошибка скачивания Link Profile DOCX:', error);
        setLinkProfileFeedback(error.message || 'Ошибка загрузки DOCX', 'error');
        alert(error.message || 'Не удалось скачать DOCX-отчет');
    } finally {
        setLinkProfileExportState('docx', false);
    }
}

async function downloadLinkProfileXlsxReport() {
    if (!linkProfileData) {
        alert('Нет данных аудита ссылочного профиля');
        return;
    }
    setLinkProfileExportState('xlsx', true);
    setLinkProfileFeedback('Формируем XLSX-отчет...', 'info');
    try {
        const response = await fetch('/api/export/link-profile-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: linkProfileData.task_id || taskId })
        });
        const payloadType = response.headers.get('content-type') || '';
        if (!response.ok || payloadType.includes('application/json')) {
            const err = await response.json();
            throw new Error(err.error || `HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const filename = filenameFromResponse(response, 'link-profile-report', 'xlsx', linkProfileData?.url || '');
        a.download = filename;
        a.href = href;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(href);
        setLinkProfileFeedback(`XLSX готов: ${filename}`, 'success');
    } catch (error) {
        console.error('Ошибка скачивания Link Profile XLSX:', error);
        setLinkProfileFeedback(error.message || 'Ошибка загрузки XLSX', 'error');
        alert(error.message || 'Не удалось скачать XLSX-отчет');
    } finally {
        setLinkProfileExportState('xlsx', false);
    }
}

let mobileLightboxItems = [];
let mobileLightboxIndex = 0;

function ensureMobileLightbox() {
    let overlay = document.getElementById('mobile-lightbox');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'mobile-lightbox';
    overlay.className = 'fixed inset-0 bg-black/90 z-50 hidden items-center justify-center p-4';
    overlay.innerHTML = `
        <button id="mobile-lightbox-close" class="absolute top-4 right-4 text-white text-2xl px-3 py-1 bg-black/40 rounded">&times;</button>
        <button id="mobile-lightbox-prev" class="absolute left-4 text-white text-3xl px-3 py-1 bg-black/40 rounded">&lsaquo;</button>
        <img id="mobile-lightbox-image" class="max-w-full max-h-full rounded-lg shadow-2xl" src="" alt="screenshot">
        <button id="mobile-lightbox-next" class="absolute right-4 text-white text-3xl px-3 py-1 bg-black/40 rounded">&rsaquo;</button>
        <div id="mobile-lightbox-caption" class="absolute bottom-4 left-1/2 -translate-x-1/2 text-white bg-black/40 px-4 py-2 rounded"></div>
    `;
    document.body.appendChild(overlay);
    document.getElementById('mobile-lightbox-close').onclick = () => overlay.classList.add('hidden');
    document.getElementById('mobile-lightbox-prev').onclick = () => showMobileLightboxImage(mobileLightboxIndex - 1);
    document.getElementById('mobile-lightbox-next').onclick = () => showMobileLightboxImage(mobileLightboxIndex + 1);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.add('hidden'); });
    return overlay;
}

function showMobileLightboxImage(index) {
    if (!mobileLightboxItems.length) return;
    if (index < 0) index = mobileLightboxItems.length - 1;
    if (index >= mobileLightboxItems.length) index = 0;
    mobileLightboxIndex = index;
    const item = mobileLightboxItems[index];
    document.getElementById('mobile-lightbox-image').src = item.src;
    document.getElementById('mobile-lightbox-caption').textContent = item.caption || '';
}

function openMobileLightbox(index) {
    const overlay = ensureMobileLightbox();
    showMobileLightboxImage(index);
    overlay.classList.remove('hidden');
    overlay.classList.add('flex');
}

function renderSimpleTable(rows, emptyText = 'Нет данных') {
    if (!Array.isArray(rows) || rows.length === 0) {
        return `<div class="text-sm text-gray-500">${escapeHtml(emptyText)}</div>`;
    }
    const cleanRows = rows.filter((row) => {
        if (!row || typeof row !== 'object') return false;
        return Object.values(row).some((value) => value !== null && value !== undefined && String(value).trim() !== '');
    });
    if (!cleanRows.length) {
        return `<div class="text-sm text-gray-500">${escapeHtml(emptyText)}</div>`;
    }
    const cols = Array.from(
        new Set(cleanRows.flatMap((row) => Object.keys(row || {})).filter((name) => String(name || '').trim() !== ''))
    );
    if (!cols.length) {
        return `<div class="text-sm text-gray-500">${escapeHtml(emptyText)}</div>`;
    }
    return `
        <div class="overflow-auto max-h-[560px] rounded-lg border border-slate-200">
            <table class="w-full min-w-[640px]">
                <thead>
                    <tr class="text-left text-xs text-slate-500 border-b">
                        ${cols.map((c) => `<th class="py-2 pr-3">${escapeHtml(c)}</th>`).join('')}
                    </tr>
                </thead>
                <tbody>
                    ${cleanRows.map((row) => `
                        <tr class="border-b border-slate-100">
                            ${cols.map((c) => `<td class="py-2 pr-3 text-sm">${escapeHtml(row[c] ?? '')}</td>`).join('')}
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function renderTableGroup(group, fallback = 'Нет данных', maxRows = 100) {
    const title = group && group.title ? String(group.title) : '';
    const rows = group && Array.isArray(group.rows) ? group.rows : [];
    const limitedRows = Number.isFinite(maxRows) && maxRows > 0 ? rows.slice(0, maxRows) : rows;
    const isTruncated = limitedRows.length < rows.length;
    return `
        <div class="bg-white rounded-xl shadow-md p-6">
            ${title ? `<div class="mb-3 flex items-center justify-between gap-3"><h4 class="font-semibold">${escapeHtml(title)}</h4><span class="text-xs text-slate-500">Строк: ${escapeHtml(rows.length)}</span></div>` : ''}
            ${renderSimpleTable(limitedRows, fallback)}
            ${isTruncated ? `<div class="mt-2 text-xs text-slate-500">Показаны первые ${limitedRows.length} строк. Все данные в выгрузке XLSX, где обрабатываются строки по лимиту.</div>` : ''}
        </div>
    `;
}

function renderTableGroups(groups, fallback = 'Нет данных', maxRows = 100) {
    const ready = (Array.isArray(groups) ? groups : []).filter((group) => Array.isArray(group?.rows) && group.rows.length > 0);
    if (!ready.length) {
        return `
            <div class="bg-white rounded-xl shadow-md p-6">
                <div class="text-sm text-slate-500">${escapeHtml(fallback)}</div>
            </div>
        `;
    }
    return ready.map((group) => renderTableGroup(group, fallback, maxRows)).join('');
}

function getLinkProfileRowsLimit(scope) {
    const normalizedScope = String(scope || '').trim().toLowerCase();
    if (!normalizedScope) return LINK_PROFILE_DEFAULT_ROWS;
    const v = Number(linkProfileRowsLimitByScope[normalizedScope] || LINK_PROFILE_DEFAULT_ROWS);
    return Number.isFinite(v) && v > 0 ? v : LINK_PROFILE_DEFAULT_ROWS;
}

function setLinkProfileFeedback(message, tone = 'info') {
    const el = document.getElementById('link-profile-live-status');
    if (!el) return;
    const text = String(message || '').trim();
    el.textContent = text;
    el.classList.remove('text-sky-700', 'text-emerald-700', 'text-rose-700', 'text-amber-700');
    if (tone === 'success') {
        el.classList.add('text-emerald-700');
    } else if (tone === 'error') {
        el.classList.add('text-rose-700');
    } else if (tone === 'warning') {
        el.classList.add('text-amber-700');
    } else {
        el.classList.add('text-sky-700');
    }
}

function renderLinkProfileTableGroups(scope, groups, fallback = 'Нет данных') {
    const normalizedScope = String(scope || '').trim().toLowerCase();
    const ready = (Array.isArray(groups) ? groups : []).filter((group) => Array.isArray(group?.rows) && group.rows.length > 0);
    if (!ready.length) {
        return `
            <div class="bg-white rounded-xl shadow-md p-6">
                <div class="text-sm text-slate-500 mb-3">${escapeHtml(fallback)}</div>
                <div class="flex flex-wrap gap-2">
                    ${normalizedScope !== 'plan' ? `<button type="button" onclick="switchLinkProfileAuditTab('plan')" class="px-3 py-1.5 text-xs rounded-lg bg-slate-900 text-white hover:bg-slate-800">Открыть Plan</button>` : ''}
                    <button type="button" onclick="downloadLinkProfileXlsxReport()" class="px-3 py-1.5 text-xs rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50">Скачать полный XLSX</button>
                </div>
            </div>
        `;
    }
    const maxRows = getLinkProfileRowsLimit(normalizedScope);
    const truncated = ready.some((group) => (group.rows || []).length > maxRows);
    const totalRows = ready.reduce((acc, group) => acc + ((group.rows || []).length), 0);
    const shownRows = ready.reduce((acc, group) => acc + Math.min((group.rows || []).length, maxRows), 0);
    const groupsHtml = ready.map((group) => renderTableGroup(group, fallback, maxRows)).join('');
    const controlsHtml = truncated || maxRows > LINK_PROFILE_DEFAULT_ROWS
        ? `
            <div class="bg-white rounded-xl shadow-md p-4 flex flex-wrap items-center justify-between gap-3">
                <div class="text-xs text-slate-600">Показано ${escapeHtml(shownRows)} из ${escapeHtml(totalRows)} строк (лимит: ${escapeHtml(maxRows)})</div>
                <div class="flex flex-wrap gap-2">
                    ${truncated ? `<button type="button" onclick="expandLinkProfileRows('${escapeHtml(normalizedScope)}')" class="px-3 py-1.5 text-xs rounded-lg bg-slate-900 text-white hover:bg-slate-800">Показать еще ${LINK_PROFILE_ROWS_STEP}</button>` : ''}
                    ${maxRows > LINK_PROFILE_DEFAULT_ROWS ? `<button type="button" onclick="collapseLinkProfileRows('${escapeHtml(normalizedScope)}')" class="px-3 py-1.5 text-xs rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50">Свернуть до ${LINK_PROFILE_DEFAULT_ROWS}</button>` : ''}
                </div>
            </div>
        `
        : '';
    return `${groupsHtml}${controlsHtml}`;
}

function getActiveLinkProfileTab() {
    const root = document.getElementById('link-profile-tabs-root');
    if (!root) return 'executive';
    const activeBtn = root.querySelector('[data-lp-tab][aria-selected="true"]');
    if (activeBtn) return activeBtn.getAttribute('data-lp-tab') || 'executive';
    const visiblePanel = Array.from(root.querySelectorAll('[data-lp-panel]')).find((panel) => !panel.classList.contains('hidden'));
    return visiblePanel?.getAttribute('data-lp-panel') || 'executive';
}

function rerenderLinkProfileResult(preferredTabId = '') {
    if (!linkProfileRenderPayload) return;
    const resultsContent = document.getElementById('results-content');
    if (!resultsContent) return;
    const activeTab = preferredTabId || getActiveLinkProfileTab() || 'executive';
    resultsContent.innerHTML = generateLinkProfileAuditHTML(linkProfileRenderPayload);
    switchLinkProfileAuditTab(activeTab);
}

function expandLinkProfileRows(scope) {
    const normalizedScope = String(scope || '').trim().toLowerCase();
    if (!normalizedScope) return;
    const current = getLinkProfileRowsLimit(normalizedScope);
    linkProfileRowsLimitByScope[normalizedScope] = current + LINK_PROFILE_ROWS_STEP;
    rerenderLinkProfileResult(normalizedScope);
}

function collapseLinkProfileRows(scope) {
    const normalizedScope = String(scope || '').trim().toLowerCase();
    if (!normalizedScope) return;
    linkProfileRowsLimitByScope[normalizedScope] = LINK_PROFILE_DEFAULT_ROWS;
    rerenderLinkProfileResult(normalizedScope);
}

function normalizeLinkProfileRedirectRows(rows) {
    if (!Array.isArray(rows) || !rows.length) return [];
    return rows.map((row) => ({
        'Referring page URL': row?.['Referring page URL'] ?? row?.['referring page url'] ?? '',
        'Target URL': row?.['Target URL'] ?? row?.['target url'] ?? '',
        'Anchor': row?.['Anchor'] ?? row?.['anchor'] ?? '',
        'Domain Rating': row?.['Domain Rating'] ?? row?.['Domain rating'] ?? row?.['domain rating'] ?? row?.['dr'] ?? '',
        'UR': row?.['UR'] ?? row?.['ur'] ?? row?.['URL Rating'] ?? row?.['url rating'] ?? '',
        'Domain traffic': row?.['Domain traffic'] ?? row?.['domain traffic'] ?? row?.['traffic'] ?? '',
        'Nofollow': row?.['Nofollow'] ?? row?.['nofollow'] ?? '',
        'Lost status': row?.['Lost status'] ?? row?.['lost status'] ?? '',
    }));
}

function countGroupRows(groups) {
    return (Array.isArray(groups) ? groups : []).reduce((acc, group) => acc + (Array.isArray(group?.rows) ? group.rows.length : 0), 0);
}

function switchLinkProfileAuditTab(tabId) {
    const root = document.getElementById('link-profile-tabs-root');
    if (!root) return;
    root.querySelectorAll('[data-lp-tab]').forEach((btn) => {
        const active = btn.getAttribute('data-lp-tab') === tabId;
        btn.classList.toggle('bg-amber-600', active);
        btn.classList.toggle('text-white', active);
        btn.classList.toggle('bg-white', !active);
        btn.classList.toggle('text-slate-700', !active);
        const badge = btn.querySelector('.lp-tab-badge');
        if (badge) {
            badge.classList.toggle('bg-white/20', active);
            badge.classList.toggle('text-white', active);
            badge.classList.toggle('border-white/30', active);
            badge.classList.toggle('bg-slate-50', !active);
            badge.classList.toggle('text-slate-700', !active);
            badge.classList.toggle('border-slate-300', !active);
        }
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
        btn.setAttribute('tabindex', active ? '0' : '-1');
    });
    root.querySelectorAll('[data-lp-panel]').forEach((panel) => {
        const show = panel.getAttribute('data-lp-panel') === tabId;
        panel.classList.toggle('hidden', !show);
        panel.hidden = !show;
        panel.setAttribute('aria-hidden', show ? 'false' : 'true');
    });
    const activeButton = root.querySelector(`[data-lp-tab="${tabId}"]`);
    if (activeButton) {
        setLinkProfileFeedback(`Открыт раздел: ${activeButton.textContent.trim()}`, 'info');
    }
}

function handleLinkProfileTabKeydown(event) {
    const key = event.key;
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(key)) return;
    const root = document.getElementById('link-profile-tabs-root');
    if (!root) return;
    const tabs = Array.from(root.querySelectorAll('[data-lp-tab]'));
    const currentIndex = tabs.findIndex((tab) => tab.getAttribute('aria-selected') === 'true');
    if (currentIndex < 0 || !tabs.length) return;
    event.preventDefault();
    let nextIndex = currentIndex;
    if (key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
    if (key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    if (key === 'Home') nextIndex = 0;
    if (key === 'End') nextIndex = tabs.length - 1;
    const targetTab = tabs[nextIndex];
    const tabId = targetTab?.getAttribute('data-lp-tab');
    if (!tabId) return;
    switchLinkProfileAuditTab(tabId);
    targetTab.focus();
}

function switchLinkProfileSubTab(scope, tabId) {
    const root = document.getElementById('link-profile-tabs-root');
    if (!root) return;
    root.querySelectorAll(`[data-lp-subtab][data-lp-subscope="${scope}"]`).forEach((btn) => {
        const active = btn.getAttribute('data-lp-subtab') === tabId;
        btn.classList.toggle('bg-slate-900', active);
        btn.classList.toggle('text-white', active);
        btn.classList.toggle('bg-white', !active);
        btn.classList.toggle('text-slate-700', !active);
    });
    root.querySelectorAll(`[data-lp-subpanel][data-lp-subscope="${scope}"]`).forEach((panel) => {
        const show = panel.getAttribute('data-lp-subpanel') === tabId;
        panel.classList.toggle('hidden', !show);
    });
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared UI helpers (Sitemap visual language applied to all tools)
// ─────────────────────────────────────────────────────────────────────────────

function buildToolHeader({ gradient, label, title, subtitle, score, scoreLabel, scoreGrade,
                           badges, metaLines, actionButtons }) {
    const pct = Math.max(0, Math.min(100, Number(score ?? 0)));
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    const ringColor = (score === null || score === undefined) ? 'var(--ds-text-muted)'
        : pct >= 70 ? '#10b981' : pct >= 50 ? '#f59e0b' : '#ef4444';
    const ringTrack = isDark ? '#334155' : '#e2e8f0';
    const ringStyle = `background:conic-gradient(${ringColor} ${pct}%,${ringTrack} ${pct}% 100%);`;
    return `
<div class="rounded-2xl overflow-hidden" style="box-shadow:var(--ds-shadow);border:1px solid var(--ds-border);">
  <div class="bg-gradient-to-r ${gradient} text-white p-6">
    <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-5">
      <div>
        ${label ? `<div class="text-xs uppercase tracking-[0.18em] text-white/70 mb-2">${label}</div>` : ''}
        <h3 class="text-2xl font-semibold mb-2">${title}</h3>
        ${subtitle ? `<div class="text-sm text-white/80 break-all">${subtitle}</div>` : ''}
        ${badges?.length ? `<div class="mt-3 flex flex-wrap gap-2">${badges.map(b =>
          `<span class="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${b.cls}">${b.text}</span>`
        ).join('')}</div>` : ''}
      </div>
      <div class="flex items-center gap-4 flex-shrink-0">
        ${score !== null && score !== undefined ? `
        <div class="ds-score-ring-lg" style="${ringStyle}">
          <div class="ds-ring-inner" style="background:rgba(15,23,42,0.8);backdrop-filter:blur(4px);">
            <div class="ds-ring-value" style="color:#fff;">${Math.round(pct)}</div>
            <div class="ds-ring-label" style="color:rgba(255,255,255,0.7);">${scoreLabel || 'оценка'}${scoreGrade ? ` (${scoreGrade})` : ''}</div>
          </div>
        </div>` : ''}
        ${metaLines?.length ? `<div class="text-xs text-white/80 space-y-1">${metaLines.map(l=>`<div>${l}</div>`).join('')}</div>` : ''}
      </div>
    </div>
  </div>
  ${actionButtons ? `<div class="p-4" style="background:var(--ds-surface);border-top:1px solid var(--ds-border);"><div class="ds-export-group">${actionButtons}</div></div>` : ''}
</div>`;
}

function buildMetricCard(label, value, sub) {
    return `<div class="ds-card" style="padding:0.75rem;animation:none;">
      <div class="text-xs" style="color:var(--ds-text-muted);">${label}</div>
      <div class="text-xl font-semibold" style="color:var(--ds-text);">${value}</div>
      ${sub ? `<div class="text-[11px]" style="color:var(--ds-text-muted);">${sub}</div>` : ''}
    </div>`;
}

function buildFindingsGrid(critical, warning, info) {
    const levelClass = { critical:'ds-findings-card severity-critical',
                         warning:'ds-findings-card severity-warning',
                         info:'ds-findings-card severity-info' };
    const levelLabel = { critical:'Критично', warning:'Предупреждение', info:'Инфо' };
    const render = (level, items) => `
      <div class="${levelClass[level]}">
        <div class="font-semibold text-sm mb-2 uppercase">${levelLabel[level]}</div>
        ${items.length ? items.slice(0,8).map(it => `
          <div class="mb-2 pb-2 border-b border-current/20 last:border-0 last:mb-0 last:pb-0">
            <div class="text-sm font-medium">${escapeHtml(String(it.title||it.issue||it.code||it))}</div>
            ${it.details ? `<div class="text-xs opacity-90">${escapeHtml(String(it.details))}</div>` : ''}
          </div>`).join('') : '<div class="text-xs opacity-75">Нет пунктов</div>'}
      </div>`;
    const total = critical.length + warning.length + info.length;
    if (!total) return '';
    return `<div class="ds-card" style="padding:1.25rem;">
      <div class="flex items-center justify-between mb-3">
        <h4 class="font-semibold" style="color:var(--ds-text);">Приоритизированные Находки</h4>
        <div class="text-xs" style="color:var(--ds-text-secondary);">критично: <span class="font-semibold">${critical.length}</span>, предупреждений: <span class="font-semibold">${warning.length}</span>, инфо: <span class="font-semibold">${info.length}</span></div>
      </div>
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-3">
        ${render('critical',critical)}${render('warning',warning)}${render('info',info)}
      </div>
    </div>`;
}

function buildRecommendations(recs) {
    if (!recs?.length) return '';
    return `<div class="ds-card" style="padding:1.25rem;">
      <h4 class="font-semibold mb-3" style="color:var(--ds-info);">Рекомендации</h4>
      <div class="space-y-2">${recs.map(r=>{
        const text = typeof r === 'string' ? r : (r.text || r.action || String(r));
        return `<div class="text-sm rounded p-2" style="color:var(--ds-info);background:var(--ds-info-bg);border:1px solid var(--ds-info-border);">${escapeHtml(text)}</div>`;
      }).join('')}</div>
    </div>`;
}

function buildActionPlan(plan) {
    if (!plan?.length) return '';
    return `<div class="ds-card" style="padding:1.25rem;">
      <h4 class="font-semibold mb-3" style="color:var(--ds-text);">План Исправлений</h4>
      <div class="space-y-2">${plan.slice(0,20).map(it=>`
        <div class="rounded-lg p-3" style="border:1px solid var(--ds-border);background:var(--ds-surface-soft);">
          <div class="text-xs mb-1" style="color:var(--ds-text-muted);">${escapeHtml(String(it.priority||'P2'))} | ${escapeHtml(String(it.owner||'SEO'))} | SLA: ${escapeHtml(String(it.sla||'н/д'))}</div>
          <div class="text-sm font-medium" style="color:var(--ds-text);">${escapeHtml(String(it.issue||it.title||''))}</div>
          <div class="text-sm" style="color:var(--ds-text-secondary);">${escapeHtml(String(it.action||it.details||''))}</div>
        </div>`).join('')}
      </div>
    </div>`;
}

// ─────────────────────────────────────────────────────────────────────────────

function generateLinkProfileAuditHTML(data) {
    const resultData = data.result || data;
    const r = resultData.results || {};
    const summary = r.summary || {};
    const validationSummary = ((r.validation || {}).summary) || {};
    const tables = r.tables || {};
    const warnings = Array.isArray(r.warnings) ? r.warnings : [];
    const errors = Array.isArray(r.errors) ? r.errors : [];
    const anchorBreakdown = r.anchor_breakdown || {};

    const summaryCards = [
        ['Домен', summary.our_domain || resultData.url || data.url || '-'],
        ['Строк ссылок', summary.rows_total ?? '-'],
        ['Уникальных доноров', summary.unique_ref_domains ?? '-'],
        ['Уникальных конкурентов', summary.unique_competitors ?? '-'],
        ['Ссылок на наш домен', summary.our_links ?? '-'],
        ['Дубликаты с нашим сайтом', summary.duplicates_with_our_site ?? '-'],
        ['Priority scored domains', summary.priority_domains_scored ?? '-'],
        ['Dofollow / Nofollow', `${summary.dofollow ?? 0} / ${summary.nofollow ?? 0}`],
        ['Dofollow %', summary.dofollow_pct ?? '-'],
        ['Lost links %', summary.lost_links_pct ?? '-'],
        ['Средний DR', summary.avg_dr ?? '-'],
    ];

    const breakdownRows = Object.entries(anchorBreakdown).map(([k, v]) => ({ type: k, count: v }));
    const prompts = r.prompts || {};
    const anchorTables = [
        { title: 'Anchor Summary', rows: tables.anchor_analysis || [] },
        { title: 'Word Analysis', rows: tables.anchor_word_analysis || [] },
        { title: 'Anchor Types', rows: breakdownRows },
        { title: 'Anchor Mix %', rows: tables.anchor_mix_pct || [] },
    ];
    const executiveGroups = [
        { title: 'Executive overview', rows: tables.executive_overview || [] },
        { title: 'KPI по нашему сайту vs среднее конкурентов', rows: tables.executive_kpi || [] },
        { title: 'Приоритеты SEO', rows: tables.priority_dashboard || [] },
        { title: 'Validation checks', rows: tables.validation_checks || [] },
    ];
    const competitorGroups = [
        { title: 'Competitor benchmark', rows: tables.competitor_benchmark || [] },
        { title: 'Рейтинг конкурентов', rows: tables.competitor_ranking || [] },
        { title: 'Качество профиля конкурентов', rows: tables.competitor_quality || [] },
        { title: 'Сырые метрики конкурентов', rows: tables.competitor_analysis || [] },
    ];
    const gapGroups = [
        { title: 'Gap donors priority', rows: tables.gap_donors_priority || [] },
        { title: 'Donor overlap matrix', rows: tables.donor_overlap_matrix || [] },
        { title: 'Ready-to-buy domains', rows: tables.ready_buy_domains || [] },
        { title: 'Priority score domains', rows: tables.priority_score_domains || [] },
    ];
    const qualityGroups = [
        { title: 'Link attributes', rows: tables.link_attributes || [] },
        { title: 'HTTP / Type / Lang / Platform', rows: tables.http_type_lang_platform || [] },
        { title: 'Target structure', rows: tables.target_structure || [] },
        { title: 'Follow/Nofollow %', rows: tables.follow_mix_pct || [] },
        { title: 'Follow domain mix %', rows: tables.follow_domain_mix_pct || [] },
    ];
    const lossGroups = [
        { title: 'Loss & recovery', rows: tables.loss_recovery || [] },
        { title: 'Lost status mix', rows: tables.lost_status_mix || [] },
        { title: 'Ссылки с редиректов', rows: normalizeLinkProfileRedirectRows(tables.raw_redirect_links || []) },
        { title: 'Ссылки с главных', rows: tables.raw_homepage_links || [] },
    ];
    const riskGroups = [
        { title: 'Risk signals', rows: tables.risk_signals || [] },
        { title: 'Проблемные дубликаты без нашего сайта', rows: tables.raw_duplicates_without_our || [] },
    ];
    const planGroups = [
        { title: 'План 30/60/90', rows: tables.action_queue_90d || [] },
        { title: 'Очередь действий', rows: tables.action_queue || [] },
    ];
    const badgePairs = [
        `Доноры: ${summary.unique_ref_domains ?? 0}`,
        `Конкуренты: ${summary.unique_competitors ?? 0}`,
        `Dofollow: ${summary.dofollow ?? 0}`,
        `Nofollow: ${summary.nofollow ?? 0}`,
        `Валидация: ok ${validationSummary.ok ?? 0} / warn ${validationSummary.warning ?? 0} / err ${validationSummary.error ?? 0}`,
    ];
    const validationErrors = Number(validationSummary.error || 0);
    const validationWarnings = Number(validationSummary.warning || 0);
    const lostPct = Number(summary.lost_links_pct || 0);
    const nofollowPct = Number(summary.nofollow_pct || 0);
    const healthScore = Math.max(0, Math.min(100, Math.round(100 - (lostPct * 0.6) - (nofollowPct * 0.35) - (validationWarnings * 2) - (validationErrors * 8))));
    const healthTone = healthScore >= 80 ? 'text-emerald-100' : healthScore >= 60 ? 'text-amber-100' : 'text-rose-100';
    const tabCounters = {
        executive: countGroupRows(executiveGroups),
        competitors: countGroupRows(competitorGroups),
        gap: countGroupRows(gapGroups),
        quality: countGroupRows(qualityGroups),
        loss: countGroupRows(lossGroups),
        anchors: countGroupRows(anchorTables),
        risks: countGroupRows(riskGroups),
        plan: countGroupRows(planGroups),
    };

    const lpActionBtns = `
        <button id="lp-export-docx-btn" onclick="downloadLinkProfileDocxReport()" class="ds-export-btn" aria-label="Скачать DOCX отчет"><i class="fas fa-file-word mr-1"></i>DOCX</button>
        <button id="lp-export-xlsx-btn" onclick="downloadLinkProfileXlsxReport()" class="ds-export-btn" aria-label="Скачать XLSX отчет"><i class="fas fa-file-excel mr-1"></i>XLSX</button>`;

    return `
        <div class="space-y-6 auditpro-results linkpro-results" id="link-profile-tabs-root">
            ${buildToolHeader({
                gradient: 'from-cyan-700 via-sky-700 to-blue-700',
                label: 'Link Profile',
                title: 'Аудит ссылочного профиля',
                subtitle: summary.our_domain || resultData.url || data.url || '',
                score: Number.isFinite(Number(healthScore)) ? Number(healthScore) : null,
                scoreLabel: 'health',
                badges: [
                    ...badgePairs.slice(0, 3).map(x => ({ cls: 'bg-white/10 border border-white/20 text-white/90', text: escapeHtml(x) })),
                    ...(validationErrors > 0 ? [{ cls: 'bg-rose-500/20 border border-rose-400/40 text-rose-100', text: `${validationErrors} val.err` }] : []),
                ],
                metaLines: [
                    `Rows: ${escapeHtml(String(summary.rows_total ?? 0))}`,
                    `Доноры: ${escapeHtml(String(summary.unique_ref_domains ?? 0))}`,
                    `Dup: ${escapeHtml(String(summary.duplicates_with_our_site ?? 0))}`,
                ],
                actionButtons: lpActionBtns,
            })}

            <div id="link-profile-live-status" class="text-xs text-slate-500 px-1" role="status" aria-live="polite" aria-atomic="true">Результаты готовы. Используйте вкладки для анализа.</div>

            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
                ${summaryCards.map(([label, value]) => buildMetricCard(escapeHtml(label), escapeHtml(String(value)))).join('')}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Dofollow / Nofollow</h4>
                    <div style="height:200px;"><canvas id="ds-chart-lp-dofollow"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Типы анкоров</h4>
                    <div style="height:200px;"><canvas id="ds-chart-lp-anchors"></canvas></div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-4 flex flex-wrap items-center justify-between gap-3">
                <div class="text-sm text-slate-700">Следующий шаг: откройте вкладку <strong>Plan</strong> и сформируйте очередь действий.</div>
                <div class="flex flex-wrap gap-2">
                    <button type="button" onclick="switchLinkProfileAuditTab('plan')" class="px-3 py-1.5 text-xs rounded-lg bg-slate-900 text-white hover:bg-slate-800">Открыть Plan</button>
                    <button type="button" onclick="downloadLinkProfileXlsxReport()" class="px-3 py-1.5 text-xs rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50">Скачать полный XLSX</button>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-4">
                <div class="flex flex-wrap gap-2" role="tablist" aria-label="Разделы аудита ссылочного профиля">
                    <button type="button" id="lp-tab-btn-executive" role="tab" aria-selected="true" aria-controls="lp-panel-executive" tabindex="0" data-lp-tab="executive" onkeydown="handleLinkProfileTabKeydown(event)" onclick="switchLinkProfileAuditTab('executive')" class="px-4 py-2 rounded-lg text-sm font-medium bg-amber-600 text-white">Executive <span class="lp-tab-badge bg-white/20 text-white border-white/30">${escapeHtml(tabCounters.executive)}</span></button>
                    <button type="button" id="lp-tab-btn-competitors" role="tab" aria-selected="false" aria-controls="lp-panel-competitors" tabindex="-1" data-lp-tab="competitors" onkeydown="handleLinkProfileTabKeydown(event)" onclick="switchLinkProfileAuditTab('competitors')" class="px-4 py-2 rounded-lg text-sm font-medium bg-white text-slate-700 border">Competitors <span class="lp-tab-badge">${escapeHtml(tabCounters.competitors)}</span></button>
                    <button type="button" id="lp-tab-btn-gap" role="tab" aria-selected="false" aria-controls="lp-panel-gap" tabindex="-1" data-lp-tab="gap" onkeydown="handleLinkProfileTabKeydown(event)" onclick="switchLinkProfileAuditTab('gap')" class="px-4 py-2 rounded-lg text-sm font-medium bg-white text-slate-700 border">Gap-доноры <span class="lp-tab-badge">${escapeHtml(tabCounters.gap)}</span></button>
                    <button type="button" id="lp-tab-btn-quality" role="tab" aria-selected="false" aria-controls="lp-panel-quality" tabindex="-1" data-lp-tab="quality" onkeydown="handleLinkProfileTabKeydown(event)" onclick="switchLinkProfileAuditTab('quality')" class="px-4 py-2 rounded-lg text-sm font-medium bg-white text-slate-700 border">Quality <span class="lp-tab-badge">${escapeHtml(tabCounters.quality)}</span></button>
                    <button type="button" id="lp-tab-btn-loss" role="tab" aria-selected="false" aria-controls="lp-panel-loss" tabindex="-1" data-lp-tab="loss" onkeydown="handleLinkProfileTabKeydown(event)" onclick="switchLinkProfileAuditTab('loss')" class="px-4 py-2 rounded-lg text-sm font-medium bg-white text-slate-700 border">Loss <span class="lp-tab-badge">${escapeHtml(tabCounters.loss)}</span></button>
                    <button type="button" id="lp-tab-btn-anchors" role="tab" aria-selected="false" aria-controls="lp-panel-anchors" tabindex="-1" data-lp-tab="anchors" onkeydown="handleLinkProfileTabKeydown(event)" onclick="switchLinkProfileAuditTab('anchors')" class="px-4 py-2 rounded-lg text-sm font-medium bg-white text-slate-700 border">Anchors <span class="lp-tab-badge">${escapeHtml(tabCounters.anchors)}</span></button>
                    <button type="button" id="lp-tab-btn-risks" role="tab" aria-selected="false" aria-controls="lp-panel-risks" tabindex="-1" data-lp-tab="risks" onkeydown="handleLinkProfileTabKeydown(event)" onclick="switchLinkProfileAuditTab('risks')" class="px-4 py-2 rounded-lg text-sm font-medium bg-white text-slate-700 border">Risks <span class="lp-tab-badge">${escapeHtml(tabCounters.risks)}</span></button>
                    <button type="button" id="lp-tab-btn-plan" role="tab" aria-selected="false" aria-controls="lp-panel-plan" tabindex="-1" data-lp-tab="plan" onkeydown="handleLinkProfileTabKeydown(event)" onclick="switchLinkProfileAuditTab('plan')" class="px-4 py-2 rounded-lg text-sm font-medium bg-white text-slate-700 border">Plan <span class="lp-tab-badge">${escapeHtml(tabCounters.plan)}</span></button>
                </div>
            </div>

            ${errors.length ? `
                <div class="bg-white rounded-xl shadow-md p-6 border border-red-200">
                    <h4 class="font-semibold text-red-700 mb-2">Ошибки</h4>
                    <ul class="list-disc pl-5 text-sm text-red-700">${errors.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>
                </div>
            ` : ''}

            ${warnings.length ? `
                <div class="bg-white rounded-xl shadow-md p-6 border border-amber-200">
                    <h4 class="font-semibold text-amber-700 mb-2">Предупреждения</h4>
                    <ul class="list-disc pl-5 text-sm text-amber-700">${warnings.map((x) => `<li>${escapeHtml(x)}</li>`).join('')}</ul>
                </div>
            ` : ''}

            <div id="lp-panel-executive" data-lp-panel="executive" role="tabpanel" aria-labelledby="lp-tab-btn-executive" aria-hidden="false" class="space-y-6">
                ${renderLinkProfileTableGroups('executive', executiveGroups, 'Нет executive-данных')}
            </div>

            <div id="lp-panel-competitors" data-lp-panel="competitors" role="tabpanel" aria-labelledby="lp-tab-btn-competitors" aria-hidden="true" class="space-y-6 hidden">
                ${renderLinkProfileTableGroups('competitors', competitorGroups, 'Нет данных конкурентов')}
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Промпт: конкуренты</h4>
                    <p class="text-sm text-slate-700 whitespace-pre-line">${escapeHtml(prompts.competitors || 'Нет данных')}</p>
                </div>
            </div>

            <div id="lp-panel-gap" data-lp-panel="gap" role="tabpanel" aria-labelledby="lp-tab-btn-gap" aria-hidden="true" class="space-y-6 hidden">
                ${renderLinkProfileTableGroups('gap', gapGroups, 'Нет gap-данных')}
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Промпт: сравнение</h4>
                    <p class="text-sm text-slate-700 whitespace-pre-line">${escapeHtml(prompts.comparison || 'Нет данных')}</p>
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Шаблон outreach</h4>
                    <p class="text-sm text-slate-700 whitespace-pre-line">${escapeHtml(prompts.outreachTemplate || 'Нет данных')}</p>
                </div>
            </div>

            <div id="lp-panel-quality" data-lp-panel="quality" role="tabpanel" aria-labelledby="lp-tab-btn-quality" aria-hidden="true" class="space-y-6 hidden">
                ${renderLinkProfileTableGroups('quality', qualityGroups, 'Нет quality-данных')}
            </div>

            <div id="lp-panel-loss" data-lp-panel="loss" role="tabpanel" aria-labelledby="lp-tab-btn-loss" aria-hidden="true" class="space-y-6 hidden">
                ${renderLinkProfileTableGroups('loss', lossGroups, 'Нет данных по потерям')}
            </div>

            <div id="lp-panel-anchors" data-lp-panel="anchors" role="tabpanel" aria-labelledby="lp-tab-btn-anchors" aria-hidden="true" class="space-y-6 hidden">
                ${renderLinkProfileTableGroups('anchors', anchorTables, 'Нет данных по анкорам')}
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Шаблон по анкорному профилю</h4>
                    <p class="text-sm text-slate-700 whitespace-pre-line">${escapeHtml(prompts.anchorTemplate || 'Нет данных')}</p>
                </div>
            </div>

            <div id="lp-panel-risks" data-lp-panel="risks" role="tabpanel" aria-labelledby="lp-tab-btn-risks" aria-hidden="true" class="space-y-6 hidden">
                ${renderLinkProfileTableGroups('risks', riskGroups, 'Нет риск-данных')}
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Шаблон риск-анализа</h4>
                    <p class="text-sm text-slate-700 whitespace-pre-line">${escapeHtml(prompts.riskTemplate || 'Нет данных')}</p>
                </div>
            </div>

            <div id="lp-panel-plan" data-lp-panel="plan" role="tabpanel" aria-labelledby="lp-tab-btn-plan" aria-hidden="true" class="space-y-6 hidden">
                ${renderLinkProfileTableGroups('plan', planGroups, 'Нет данных по плану')}
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">План действий (NLP-подсказка)</h4>
                    <p class="text-sm text-slate-700 whitespace-pre-line">${escapeHtml(prompts.plan || 'План не сформирован')}</p>
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Шаблон проверки строки ссылки</h4>
                    <p class="text-sm text-slate-700 whitespace-pre-line">${escapeHtml(prompts.rowReviewTemplate || 'Нет данных')}</p>
                </div>
            </div>
        </div>
    `;
}

function generateGenericHTML(result) {
    const safe = result || {};
    return `
        <div class="bg-white rounded-xl shadow-md p-6">
            <h3 class="text-lg font-semibold mb-3">Результат</h3>
            <pre class="text-xs bg-slate-50 border rounded p-3 overflow-auto">${escapeHtml(JSON.stringify(safe, null, 2))}</pre>
        </div>
    `;
}

function setRedirectCheckerFilter(filter) {
    redirectCheckerFilter = ['all', 'passed', 'warning', 'error'].includes(filter) ? filter : 'all';
    if (lastTaskResult && lastTaskResult.task_type === 'redirect_checker') {
        const resultsContent = document.getElementById('results-content');
        if (resultsContent) {
            resultsContent.innerHTML = generateRedirectCheckerHTML(lastTaskResult);
        }
    }
}

function copyRedirectCheckerSummary() {
    if (!redirectCheckerData) {
        alert('Нет данных Redirect Checker');
        return;
    }
    const payload = redirectCheckerData.result || redirectCheckerData;
    const r = payload.results || payload;
    const summary = r.summary || {};
    const scenarios = Array.isArray(r.scenarios) ? r.scenarios : [];
    const policy = r.applied_policy || {};
    const lines = [];
    lines.push('REDIRECT CHECKER');
    lines.push(`URL: ${payload.url || r.checked_url || ''}`);
    lines.push(`Scenarios: ${summary.total_scenarios || scenarios.length} | Passed: ${summary.passed || 0} | Warning: ${summary.warnings || 0} | Error: ${summary.errors || 0}`);
    lines.push(`Score: ${summary.quality_score || 0} (${summary.quality_grade || '-'})`);
    lines.push(
        `Policy: host=${policy.canonical_host_policy || 'auto'} | slash=${policy.trailing_slash_policy || 'auto'} | lowercase=${policy.enforce_lowercase === false ? 'off' : 'on'}`
    );
    if (Array.isArray(policy.allowed_query_params) && policy.allowed_query_params.length) {
        lines.push(`Allowed params: ${policy.allowed_query_params.join(', ')}`);
    }
    if (Array.isArray(policy.required_query_params) && policy.required_query_params.length) {
        lines.push(`Required params: ${policy.required_query_params.join(', ')}`);
    }
    lines.push('');
    scenarios.forEach((s) => {
        lines.push(`[${String(s.status || '').toUpperCase()}] ${s.id || '-'} ${s.key || '-'} ${s.title || ''}`);
        lines.push(`Duration: ${s.duration_ms || 0} ms`);
        lines.push(`Expected: ${s.expected || '-'}`);
        lines.push(`Actual: ${s.actual || '-'}`);
        if (s.recommendation) lines.push(`Fix: ${s.recommendation}`);
        lines.push('');
    });
    copyToClipboard(lines.join('\n'));
}

function _redirectCsvCell(v) {
    const text = String(v == null ? '' : v).replace(/"/g, '""');
    return `"${text}"`;
}

function downloadRedirectCheckerCsv() {
    if (!redirectCheckerData) {
        alert('Нет данных Redirect Checker');
        return;
    }
    const payload = redirectCheckerData.result || redirectCheckerData;
    const r = payload.results || payload;
    const scenarios = Array.isArray(r.scenarios) ? r.scenarios : [];
    const policy = r.applied_policy || {};
    const rows = [];
    rows.push([
        'id',
        'key',
        'scenario',
        'status',
        'what_checked',
        'expected',
        'actual',
        'duration_ms',
        'response_codes',
        'hops',
        'test_url',
        'final_url',
        'recommendation',
        'canonical_host_policy',
        'trailing_slash_policy',
        'enforce_lowercase'
    ]);
    scenarios.forEach((s) => {
        rows.push([
            s.id || '',
            s.key || '',
            s.title || '',
            s.status || '',
            s.what_checked || '',
            s.expected || '',
            s.actual || '',
            s.duration_ms || 0,
            Array.isArray(s.response_codes) ? s.response_codes.join(' -> ') : '',
            s.hops || 0,
            s.test_url || '',
            s.final_url || '',
            s.recommendation || '',
            policy.canonical_host_policy || '',
            policy.trailing_slash_policy || '',
            (policy.enforce_lowercase === false ? 'false' : 'true')
        ]);
    });
    const csvBody = rows.map((row) => row.map(_redirectCsvCell).join(',')).join('\n');
    const csv = '\ufeff' + csvBody;
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const safeDomain = sanitizeFilenamePart(extractDomain(payload.url || r.checked_url || '') || payload.url || r.checked_url || 'site');
    link.href = URL.createObjectURL(blob);
    link.download = `redirect_checker_${safeDomain}_${buildFilenameTimestamp()}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(link.href);
}

async function downloadRedirectCheckerDocx() {
    if (!redirectCheckerData) {
        alert('Нет данных Redirect Checker');
        return;
    }

    const exportTaskId = redirectCheckerData.task_id || taskId;
    if (!exportTaskId) {
        alert('Не найден task_id для экспорта DOCX');
        return;
    }

    try {
        const response = await fetch('/api/export/redirect-checker-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: exportTaskId })
        });

        if (!response.ok) {
            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }
            throw new Error((payload && payload.error) ? payload.error : 'Не удалось сформировать DOCX-отчет Redirect Checker');
        }

        const blob = await response.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filenameFromResponse(response, 'redirect-checker-report', 'docx', redirectCheckerData?.url || '');
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);
    } catch (error) {
        console.error('Ошибка скачивания Redirect Checker DOCX:', error);
        alert(error.message || 'Не удалось скачать DOCX-отчет Redirect Checker');
    }
}

async function downloadRedirectCheckerXlsx() {
    if (!redirectCheckerData) {
        alert('Нет данных Redirect Checker');
        return;
    }

    const exportTaskId = redirectCheckerData.task_id || taskId;
    if (!exportTaskId) {
        alert('Не найден task_id для экспорта XLSX');
        return;
    }

    try {
        const response = await fetch('/api/export/redirect-checker-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: exportTaskId })
        });

        if (!response.ok) {
            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }
            throw new Error((payload && payload.error) ? payload.error : 'Не удалось сформировать XLSX-отчет Redirect Checker');
        }

        const blob = await response.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filenameFromResponse(response, 'redirect-checker-report', 'xlsx', redirectCheckerData?.url || '');
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);
    } catch (error) {
        console.error('Ошибка скачивания Redirect Checker XLSX:', error);
        alert(error.message || 'Не удалось скачать XLSX-отчет Redirect Checker');
    }
}

function generateRedirectCheckerHTML(result) {
    const resultData = result.result || result;
    const r = resultData.results || resultData;
    const summary = r.summary || {};
    const selectedUa = r.selected_user_agent || {};
    const policy = r.applied_policy || {};
    const allScenarios = Array.isArray(r.scenarios) ? r.scenarios : [];
    const filter = redirectCheckerFilter || 'all';
    const scenarios = filter === 'all' ? allScenarios : allScenarios.filter((s) => (s.status || '') === filter);
    const recommendations = Array.isArray(r.recommendations) ? r.recommendations : [];

    const formatChain = (codes) => Array.isArray(codes) && codes.length ? codes.join(' -> ') : '-';
    const formatMs = (value) => `${Number(value || 0)} ms`;
    const statusClass = (status) => {
        const s = String(status || '').toLowerCase();
        if (s === 'passed') return 'passed';
        if (s === 'warning') return 'warning';
        if (s === 'error') return 'error';
        return '';
    };
    const statusIcon = (status) => {
        const s = String(status || '').toLowerCase();
        if (s === 'passed') return '<i class="fas fa-check-circle"></i>';
        if (s === 'warning') return '<i class="fas fa-exclamation-triangle"></i>';
        if (s === 'error') return '<i class="fas fa-times-circle"></i>';
        return '<i class="fas fa-info-circle"></i>';
    };

    const counters = {
        all: allScenarios.length,
        passed: allScenarios.filter((s) => String(s.status || '').toLowerCase() === 'passed').length,
        warning: allScenarios.filter((s) => String(s.status || '').toLowerCase() === 'warning').length,
        error: allScenarios.filter((s) => String(s.status || '').toLowerCase() === 'error').length,
    };
    const slowestScenarios = [...allScenarios]
        .sort((a, b) => Number(b.duration_ms || 0) - Number(a.duration_ms || 0))
        .slice(0, 5);
    const longChainScenarios = [...allScenarios]
        .filter((s) => Number(s.hops || 0) >= 2)
        .sort((a, b) => {
            const hopDelta = Number(b.hops || 0) - Number(a.hops || 0);
            if (hopDelta !== 0) return hopDelta;
            return Number(b.duration_ms || 0) - Number(a.duration_ms || 0);
        })
        .slice(0, 5);
    const notFoundRiskScenarios = [...allScenarios]
        .filter((s) => ['missing_404', 'soft_404_detection'].includes(String(s.key || '').toLowerCase()))
        .sort((a, b) => {
            const severityRank = (item) => {
                const status = String(item.status || '').toLowerCase();
                if (status === 'error') return 0;
                if (status === 'warning') return 1;
                if (status === 'passed') return 2;
                return 3;
            };
            const rankDelta = severityRank(a) - severityRank(b);
            if (rankDelta !== 0) return rankDelta;
            return Number(b.duration_ms || 0) - Number(a.duration_ms || 0);
        });

    const scenarioRows = scenarios.map((s) => `
        <tr>
            <td class="redirectpro-cell text-center font-semibold">${escapeHtml(s.id || '-')}</td>
            <td class="redirectpro-cell">
                <div class="font-semibold text-slate-900">${escapeHtml(s.title || '-')}</div>
                <div class="redirectpro-meta">${escapeHtml(s.key || '-')} | ${escapeHtml(s.what_checked || '-')}</div>
            </td>
            <td class="redirectpro-cell">
                <span class="redirectpro-status ${statusClass(s.status)}">
                    ${statusIcon(s.status)} ${escapeHtml(String(s.status || 'unknown').toUpperCase())}
                </span>
                <div class="redirectpro-meta mt-1">Хопы: <span class="font-medium">${escapeHtml(s.hops || 0)}</span></div>
                <div class="redirectpro-meta mt-1">Время: <span class="font-medium">${escapeHtml(formatMs(s.duration_ms))}</span></div>
            </td>
            <td class="redirectpro-cell">
                <div class="font-medium text-slate-800">${escapeHtml(formatChain(s.response_codes))}</div>
                <div class="redirectpro-meta mt-1">Final: ${escapeHtml(s.final_url || '-')}</div>
            </td>
            <td class="redirectpro-cell">
                <div><span class="text-slate-500">Expected:</span> ${escapeHtml(s.expected || '-')}</div>
                <div class="mt-2"><span class="text-slate-500">Actual:</span> ${escapeHtml(s.actual || '-')}</div>
            </td>
            <td class="redirectpro-cell">${escapeHtml(s.recommendation || '-')}</td>
            <td class="redirectpro-cell">
                <details class="redirectpro-details">
                    <summary>Подробнее</summary>
                    <div class="body">Test URL: ${escapeHtml(s.test_url || '-')}
Error: ${escapeHtml(s.error || '-')}
Final URL: ${escapeHtml(s.final_url || '-')}
Codes: ${escapeHtml(formatChain(s.response_codes))}
Duration: ${escapeHtml(formatMs(s.duration_ms))}
                    </div>
                </details>
            </td>
        </tr>
    `).join('');
    const slowestRows = slowestScenarios.map((s, index) => `
        <div class="flex items-start justify-between gap-4 py-3 border-b border-slate-100 last:border-b-0">
            <div class="min-w-0">
                <div class="text-xs uppercase tracking-wide text-slate-400">#${index + 1} · ${escapeHtml(s.key || '-')}</div>
                <div class="font-medium text-slate-900">${escapeHtml(s.title || '-')}</div>
                <div class="text-sm text-slate-500 truncate">${escapeHtml(s.test_url || s.final_url || '-')}</div>
            </div>
            <div class="text-right shrink-0">
                <div class="font-semibold text-slate-900">${escapeHtml(formatMs(s.duration_ms))}</div>
                <div class="text-xs text-slate-500">${escapeHtml(String(s.status || 'unknown').toUpperCase())}</div>
            </div>
        </div>
    `).join('');
    const longChainRows = longChainScenarios.map((s, index) => `
        <div class="flex items-start justify-between gap-4 py-3 border-b border-slate-100 last:border-b-0">
            <div class="min-w-0">
                <div class="text-xs uppercase tracking-wide text-slate-400">#${index + 1} · ${escapeHtml(s.key || '-')}</div>
                <div class="font-medium text-slate-900">${escapeHtml(s.title || '-')}</div>
                <div class="text-sm text-slate-500 truncate">${escapeHtml(formatChain(s.response_codes))}</div>
            </div>
            <div class="text-right shrink-0">
                <div class="font-semibold text-slate-900">${escapeHtml(String(s.hops || 0))} hops</div>
                <div class="text-xs text-slate-500">${escapeHtml(formatMs(s.duration_ms))}</div>
            </div>
        </div>
    `).join('');
    const notFoundRiskRows = notFoundRiskScenarios.map((s) => `
        <div class="flex items-start justify-between gap-4 py-3 border-b border-slate-100 last:border-b-0">
            <div class="min-w-0">
                <div class="text-xs uppercase tracking-wide text-slate-400">${escapeHtml(s.key || '-')}</div>
                <div class="font-medium text-slate-900">${escapeHtml(s.title || '-')}</div>
                <div class="text-sm text-slate-500">${escapeHtml(s.actual || s.expected || '-')}</div>
            </div>
            <div class="text-right shrink-0">
                <div class="font-semibold text-slate-900">${escapeHtml(String(s.status || 'unknown').toUpperCase())}</div>
                <div class="text-xs text-slate-500">${escapeHtml(formatMs(s.duration_ms))}</div>
            </div>
        </div>
    `).join('');

    const activeClass = (key) => (filter === key ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-700 hover:bg-slate-200');
    const rdActionBtns = `
        <button type="button" onclick="downloadRedirectCheckerXlsx()" class="ds-export-btn"><i class="fas fa-file-excel mr-1"></i>XLSX</button>
        <button type="button" onclick="downloadRedirectCheckerCsv()" class="ds-export-btn"><i class="fas fa-file-csv mr-1"></i>CSV</button>
        <button type="button" onclick="downloadRedirectCheckerDocx()" class="ds-export-btn"><i class="fas fa-file-word mr-1"></i>DOCX</button>
        <button type="button" onclick="copyRedirectCheckerSummary()" class="ds-export-btn"><i class="fas fa-copy mr-1"></i>Копировать</button>`;
    return `
        <div class="redirectpro-results space-y-6">
            ${buildToolHeader({
                gradient: 'from-emerald-700 via-green-700 to-teal-700',
                label: 'Redirect Checker',
                title: 'Анализ редиректов',
                subtitle: resultData.url || r.checked_url || '',
                score: Number.isFinite(Number(summary.quality_score)) ? Number(summary.quality_score) : null,
                scoreLabel: 'качество',
                scoreGrade: summary.quality_grade || null,
                badges: [
                    { cls: 'bg-emerald-500/20 border border-emerald-400/40 text-emerald-100', text: `${counters.passed} passed` },
                    ...(counters.warning > 0 ? [{ cls: 'bg-amber-400/20 border border-amber-300/40 text-amber-100', text: `${counters.warning} warning` }] : []),
                    ...(counters.error > 0 ? [{ cls: 'bg-rose-500/20 border border-rose-400/40 text-rose-100', text: `${counters.error} error` }] : []),
                ],
                metaLines: [
                    `UA: ${escapeHtml(selectedUa.label || '-')}`,
                    `Длительность: ${escapeHtml(String(summary.duration_ms || 0))} ms`,
                    `Policy: host=${escapeHtml(policy.canonical_host_policy || 'auto')}`,
                ],
                actionButtons: rdActionBtns,
            })}

            <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
                ${buildMetricCard('Всего', escapeHtml(String(summary.total_scenarios || counters.all || 0)))}
                ${buildMetricCard('Passed', escapeHtml(String(summary.passed || counters.passed || 0)))}
                ${buildMetricCard('Warning', escapeHtml(String(summary.warnings || counters.warning || 0)))}
                ${buildMetricCard('Error', escapeHtml(String(summary.errors || counters.error || 0)))}
                ${buildMetricCard('UA', escapeHtml(selectedUa.label || '-'))}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 xl:grid-cols-4 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Сводка редиректов</h4>
                    <div style="height:200px;"><canvas id="ds-chart-redirect-summary"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Самые медленные сценарии</h4>
                    <div class="space-y-1">${slowestRows || '<div class="text-sm text-slate-500">Нет данных по длительности</div>'}</div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Цепочки 2+ hops</h4>
                    <div class="space-y-1">${longChainRows || '<div class="text-sm text-slate-500">Длинных цепочек не обнаружено</div>'}</div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Soft-404 / wrong 404</h4>
                    <div class="space-y-1">${notFoundRiskRows || '<div class="text-sm text-slate-500">Риски 404 не обнаружены</div>'}</div>
                </div>
            </div>

            ${buildRecommendations(recommendations)}

            <div class="bg-white rounded-xl shadow-md p-6">
                <div class="flex flex-wrap gap-2 mb-4">
                    <button type="button" onclick="setRedirectCheckerFilter('all')" class="px-3 py-1.5 rounded-lg text-sm ${activeClass('all')}">Все (${counters.all})</button>
                    <button type="button" onclick="setRedirectCheckerFilter('passed')" class="px-3 py-1.5 rounded-lg text-sm ${activeClass('passed')}">Passed (${counters.passed})</button>
                    <button type="button" onclick="setRedirectCheckerFilter('warning')" class="px-3 py-1.5 rounded-lg text-sm ${activeClass('warning')}">Warning (${counters.warning})</button>
                    <button type="button" onclick="setRedirectCheckerFilter('error')" class="px-3 py-1.5 rounded-lg text-sm ${activeClass('error')}">Error (${counters.error})</button>
                </div>

                <div class="redirectpro-table-wrap">
                    <table class="redirectpro-table">
                        <colgroup>
                            <col style="width:56px;">
                            <col style="width:220px;">
                            <col style="width:130px;">
                            <col style="width:220px;">
                            <col style="width:320px;">
                            <col style="width:300px;">
                            <col style="width:130px;">
                        </colgroup>
                        <thead>
                            <tr>
                                <th class="py-2 px-3 text-left">#</th>
                                <th class="py-2 px-3 text-left">Сценарий</th>
                                <th class="py-2 px-3 text-left">Статус</th>
                                <th class="py-2 px-3 text-left">Цепочка</th>
                                <th class="py-2 px-3 text-left">Expected / Actual</th>
                                <th class="py-2 px-3 text-left">Рекомендация</th>
                                <th class="py-2 px-3 text-left">Детали</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${scenarioRows || '<tr><td colspan="7" class="py-4 px-3 text-center text-slate-500">Нет данных для выбранного фильтра</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>

        </div>
    `;
}

function generateCoreWebVitalsHTML(result) {
    const resultData = result.result || result;
    const r = resultData.results || resultData;
    const summary = r.summary || {};
    const strategy = r.strategy || 'desktop';
    const source = r.source || 'pagespeed_insights_api';
    const mode = String(r.mode || 'single').toLowerCase();
    const sites = Array.isArray(r.sites) ? r.sites : [];
    const isBatch = mode === 'batch' || sites.length > 0;

    const statusClass = (status) => {
        const s = String(status || '').toLowerCase();
        if (s === 'good') return 'border-emerald-200 bg-emerald-50 text-emerald-800';
        if (s === 'needs_improvement') return 'border-amber-200 bg-amber-50 text-amber-800';
        if (s === 'poor') return 'border-red-200 bg-red-50 text-red-800';
        if (s === 'error') return 'border-red-200 bg-red-50 text-red-800';
        return 'border-slate-200 bg-slate-50 text-slate-700';
    };

    const statusLabel = (status) => {
        const s = String(status || '').toLowerCase();
        if (s === 'good') return 'GOOD';
        if (s === 'needs_improvement') return 'NEEDS IMPROVEMENT';
        if (s === 'poor') return 'POOR';
        if (s === 'error') return 'ERROR';
        return 'UNKNOWN';
    };

    const formatMetric = (metricKey, rawValue) => {
        if (rawValue == null || rawValue === '' || !Number.isFinite(Number(rawValue))) return 'н/д';
        if (metricKey === 'cls') return Number(rawValue).toFixed(3);
        return String(Math.round(Number(rawValue)));
    };
    const fmtInt = (value) => (Number.isFinite(Number(value)) ? String(Math.round(Number(value))) : 'н/д');
    const fmt1 = (value) => (Number.isFinite(Number(value)) ? String(Math.round(Number(value) * 10) / 10) : 'н/д');
    const fmt3 = (value) => (Number.isFinite(Number(value)) ? Number(value).toFixed(3) : 'н/д');

    const scoreBadgeClass = (rawScore) => {
        const score = Number(rawScore);
        if (!Number.isFinite(score)) return 'border-slate-200 bg-slate-50 text-slate-700';
        if (score >= 90) return 'border-emerald-200 bg-emerald-50 text-emerald-800';
        if (score >= 50) return 'border-amber-200 bg-amber-50 text-amber-800';
        return 'border-red-200 bg-red-50 text-red-800';
    };
    const exportButtons = `
        <div class="flex flex-wrap gap-2">
            <button type="button" onclick="downloadCoreWebVitalsDocxReport()" class="ds-export-btn">
                <i class="fas fa-file-word mr-1"></i>DOCX
            </button>
            <button type="button" onclick="downloadCoreWebVitalsXlsxReport()" class="ds-export-btn">
                <i class="fas fa-file-excel mr-1"></i>XLSX
            </button>
            <button type="button" onclick="downloadCoreWebVitalsCsvReport()" class="ds-export-btn">
                <i class="fas fa-file-csv mr-1"></i>CSV
            </button>
            <button type="button" onclick="downloadCoreWebVitalsJsonReport()" class="ds-export-btn">
                <i class="fas fa-file-code mr-1"></i>JSON
            </button>
            <button type="button" onclick="copyCoreWebVitalsSummary()" class="ds-export-btn">
                <i class="fas fa-copy mr-1"></i>Копировать
            </button>
        </div>
    `;

    if (mode === 'competitor') {
        const competitors = Array.isArray(r.competitors) ? r.competitors : (sites.length > 1 ? sites.slice(1) : []);
        const primary = r.primary || (sites[0] || {});
        const primarySummary = primary.summary || {};
        const primaryMetrics = primary.metrics || {};
        const benchmark = r.benchmark || {};
        const comparisonRows = Array.isArray(r.comparison_rows) ? r.comparison_rows : [];
        const gaps = Array.isArray(r.gaps_for_primary) ? r.gaps_for_primary : [];
        const strengths = Array.isArray(r.strengths_of_primary) ? r.strengths_of_primary : [];
        const commonOpportunities = Array.isArray(r.common_opportunities) ? r.common_opportunities : [];
        const actionPlan = Array.isArray(r.action_plan) ? r.action_plan : [];
        const recommendations = Array.isArray(r.recommendations) ? r.recommendations : [];
        const competitorSummary = r.summary || {};

        const primaryScore = primarySummary.performance_score;
        const primaryCwv = primarySummary.core_web_vitals_status || competitorSummary.primary_cwv_status || 'unknown';
        const primaryLcp = primaryMetrics.lcp?.field_value_ms ?? primaryMetrics.lcp?.lab_value_ms;
        const primaryInp = primaryMetrics.inp?.field_value_ms ?? primaryMetrics.inp?.lab_value_ms;
        const primaryCls = primaryMetrics.cls?.field_value ?? primaryMetrics.cls?.lab_value;

        const deltaBadge = (value, positiveIsGood = true, unit = '') => {
            const num = Number(value);
            if (!Number.isFinite(num)) return '<span class="text-slate-400">-</span>';
            const sign = num > 0 ? '+' : '';
            const absGood = positiveIsGood ? num >= 0 : num <= 0;
            const cls = absGood ? 'text-emerald-700' : 'text-red-700';
            const display = `${sign}${Math.round(num * 10) / 10}${unit}`;
            return `<span class="${cls} font-semibold">${escapeHtml(display)}</span>`;
        };

        const rows = comparisonRows.map((row, idx) => {
            const status = String(row.status || 'error').toLowerCase();
            const cwv = row.cwv_status || 'unknown';
            return `
                <tr class="border-b align-top">
                    <td class="py-2 px-3 text-xs text-slate-500">${idx + 1}</td>
                    <td class="py-2 px-3 font-medium break-all">${escapeHtml(row.url || '-')}</td>
                    <td class="py-2 px-3"><span class="inline-flex px-2 py-1 rounded border text-xs font-semibold ${statusClass(cwv)}">${statusLabel(cwv)}</span></td>
                    <td class="py-2 px-3"><span class="inline-flex px-2 py-1 rounded border text-xs font-semibold ${scoreBadgeClass(row.score)}">${escapeHtml(row.score ?? 'н/д')}</span></td>
                    <td class="py-2 px-3">${escapeHtml(formatMetric('lcp', row.lcp_ms))} <span class="text-xs text-slate-400">ms</span></td>
                    <td class="py-2 px-3">${escapeHtml(formatMetric('inp', row.inp_ms))} <span class="text-xs text-slate-400">ms</span></td>
                    <td class="py-2 px-3">${escapeHtml(formatMetric('cls', row.cls))}</td>
                    <td class="py-2 px-3">${deltaBadge(row.score_delta_vs_primary, false, ' score')}</td>
                    <td class="py-2 px-3">${deltaBadge(row.lcp_delta_ms_vs_primary, true, ' ms')}</td>
                    <td class="py-2 px-3">${deltaBadge(row.inp_delta_ms_vs_primary, true, ' ms')}</td>
                    <td class="py-2 px-3">${deltaBadge(row.cls_delta_vs_primary, true, '')}</td>
                    <td class="py-2 px-3 text-xs text-slate-700">${escapeHtml(row.top_focus || row.error || (status === 'success' ? '-' : 'Scan failed'))}</td>
                </tr>
            `;
        }).join('');

        return `
            <div class="space-y-6 cwvpro-results">
                ${buildToolHeader({
                    gradient: 'from-sky-700 via-blue-700 to-indigo-700',
                    label: 'Core Web Vitals: Конкуренты',
                    title: 'Анализ конкурентов',
                    subtitle: competitorSummary.primary_url || primary.url || '',
                    score: Number.isFinite(Number(primaryScore)) ? Number(primaryScore) : null,
                    scoreLabel: 'Primary',
                    badges: [
                        { cls: `${statusClass(primaryCwv)} border`, text: `CWV: ${statusLabel(primaryCwv)}` },
                        { cls: 'bg-white/10 border border-white/20 text-white/90', text: `${fmtInt(competitorSummary.competitors_total || competitors.length)} конкурентов` },
                        { cls: 'bg-white/10 border border-white/20 text-white/90', text: `Strategy: ${escapeHtml(String(strategy).toUpperCase())}` },
                    ],
                    metaLines: [
                        `Rank: ${escapeHtml(String(competitorSummary.primary_rank || '-'))}`,
                        `LCP: ${fmt1(primaryLcp)} ms / INP: ${fmt1(primaryInp)} ms`,
                        `CLS: ${fmt3(primaryCls)}`,
                    ],
                    actionButtons: exportButtons,
                })}

                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${buildMetricCard('Total URLs', fmtInt(competitorSummary.total_urls))}
                    ${buildMetricCard('Success', fmtInt(competitorSummary.successful_urls))}
                    ${buildMetricCard('Errors', fmtInt(competitorSummary.failed_urls))}
                    ${buildMetricCard('Конкурентов', fmtInt(competitorSummary.competitors_total || competitors.length))}
                </div>

                <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                    <h4 class="font-semibold mb-3">Сравнение с конкурентами</h4>
                    <div class="overflow-auto border rounded-lg">
                        <table class="w-full min-w-[1450px] text-sm">
                            <thead>
                                <tr class="text-left text-xs text-slate-500 border-b bg-slate-50">
                                    <th class="py-2 px-3">#</th>
                                    <th class="py-2 px-3">URL</th>
                                    <th class="py-2 px-3">CWV</th>
                                    <th class="py-2 px-3">Score</th>
                                    <th class="py-2 px-3">LCP</th>
                                    <th class="py-2 px-3">INP</th>
                                    <th class="py-2 px-3">CLS</th>
                                    <th class="py-2 px-3">Δ Score vs primary</th>
                                    <th class="py-2 px-3">Δ LCP vs primary</th>
                                    <th class="py-2 px-3">Δ INP vs primary</th>
                                    <th class="py-2 px-3">Δ CLS vs primary</th>
                                    <th class="py-2 px-3">Фокус</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${rows || '<tr><td colspan="12" class="py-4 px-3 text-center text-slate-500">Нет данных по конкурентам</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                        <h4 class="font-semibold mb-3">Где primary отстаёт</h4>
                        ${gaps.length > 0
                            ? `<ul class="space-y-2 text-sm text-slate-700">${gaps.map((item) => `<li class="border-l-4 border-red-400 pl-3">${escapeHtml(item)}</li>`).join('')}</ul>`
                            : '<div class="text-sm text-slate-500">Критичных отставаний не выявлено.</div>'
                        }
                    </div>
                    <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                        <h4 class="font-semibold mb-3">Сильные стороны primary</h4>
                        ${strengths.length > 0
                            ? `<ul class="space-y-2 text-sm text-slate-700">${strengths.map((item) => `<li class="border-l-4 border-emerald-400 pl-3">${escapeHtml(item)}</li>`).join('')}</ul>`
                            : '<div class="text-sm text-slate-500">Явных преимуществ над конкурентами пока нет.</div>'
                        }
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                        <h4 class="font-semibold mb-3">Частые проблемы у конкурентов</h4>
                        ${commonOpportunities.length > 0 ? `
                            <div class="overflow-auto border rounded-lg">
                                <table class="w-full min-w-[620px] text-sm">
                                    <thead>
                                        <tr class="text-left text-xs text-slate-500 border-b bg-slate-50">
                                            <th class="py-2 px-3">Проблема</th>
                                            <th class="py-2 px-3">Group</th>
                                            <th class="py-2 px-3">Count</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${commonOpportunities.map((item) => `
                                            <tr class="border-b">
                                                <td class="py-2 px-3">${escapeHtml(item.title || item.id || '-')}</td>
                                                <td class="py-2 px-3">${escapeHtml(item.group || '-')}</td>
                                                <td class="py-2 px-3">${fmtInt(item.count)}</td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            </div>
                        ` : '<div class="text-sm text-slate-500">Недостаточно данных для группировки проблем конкурентов.</div>'}
                    </div>

                    ${buildActionPlan(actionPlan)}
                </div>

                ${buildRecommendations(recommendations)}
            </div>
        `;
    }

    if (isBatch) {
        const batchSummary = r.summary || {};
        const statusCounts = batchSummary.status_counts || {};
        const totalUrls = Number(batchSummary.total_urls ?? sites.length ?? 0);
        const successUrls = Number(batchSummary.successful_urls ?? 0);
        const failedUrls = Number(batchSummary.failed_urls ?? Math.max(0, totalUrls - successUrls));
        const avgScore = batchSummary.average_performance_score;
        const medianScore = batchSummary.median_performance_score;
        const minScore = batchSummary.min_performance_score;
        const maxScore = batchSummary.max_performance_score;
        const metricsAvg = batchSummary.metrics_average || {};
        const categoriesAvg = batchSummary.categories_average || {};
        const riskCounts = batchSummary.risk_counts || {};
        const commonOpportunities = Array.isArray(r.common_opportunities) ? r.common_opportunities : [];
        const batchActionPlan = Array.isArray(r.action_plan) ? r.action_plan : [];
        const priorityUrls = Array.isArray(r.priority_urls) ? r.priority_urls : [];
        const recommendations = Array.isArray(r.recommendations) ? r.recommendations : [];
        const sortedSites = [...sites].sort((a, b) => {
            const rank = (site) => {
                if (String(site?.status || '').toLowerCase() !== 'success') return 0;
                const cwv = String(site?.summary?.core_web_vitals_status || 'unknown').toLowerCase();
                if (cwv === 'poor') return 1;
                if (cwv === 'needs_improvement') return 2;
                if (cwv === 'unknown') return 3;
                return 4;
            };
            const rankDelta = rank(a) - rank(b);
            if (rankDelta !== 0) return rankDelta;
            const scoreA = Number(a?.summary?.performance_score ?? 101);
            const scoreB = Number(b?.summary?.performance_score ?? 101);
            return scoreA - scoreB;
        });

        const rows = sortedSites.map((site, idx) => {
            const status = String(site.status || 'error').toLowerCase();
            const url = site.url || '-';
            if (status !== 'success') {
                return `
                    <tr class="border-b align-top">
                        <td class="py-2 px-3 text-xs text-slate-500">${idx + 1}</td>
                        <td class="py-2 px-3 font-medium break-all">${escapeHtml(url)}</td>
                        <td class="py-2 px-3"><span class="inline-flex px-2 py-1 rounded border text-xs font-semibold ${statusClass('error')}">${statusLabel('error')}</span></td>
                        <td class="py-2 px-3 text-slate-500">-</td>
                        <td class="py-2 px-3 text-slate-500">-</td>
                        <td class="py-2 px-3 text-slate-500">-</td>
                        <td class="py-2 px-3 text-slate-500">-</td>
                        <td class="py-2 px-3 text-xs text-red-700">${escapeHtml(site.error || 'Scan failed')}</td>
                    </tr>
                `;
            }

            const siteSummary = site.summary || {};
            const metrics = site.metrics || {};
            const score = siteSummary.performance_score;
            const cwv = siteSummary.core_web_vitals_status || 'unknown';
            const lcp = metrics.lcp?.field_value_ms ?? metrics.lcp?.lab_value_ms;
            const inp = metrics.inp?.field_value_ms ?? metrics.inp?.lab_value_ms;
            const cls = metrics.cls?.field_value ?? metrics.cls?.lab_value;
            const opportunities = Array.isArray(site.opportunities) ? site.opportunities : [];
            opportunities.sort((x, y) => Number(x?.score ?? 1) - Number(y?.score ?? 1));
            const topItem = opportunities[0];
            const fallbackRec = Array.isArray(site.recommendations) ? site.recommendations[0] : '';
            const focusText = topItem?.title || fallbackRec || '-';

            return `
                <tr class="border-b align-top">
                    <td class="py-2 px-3 text-xs text-slate-500">${idx + 1}</td>
                    <td class="py-2 px-3 font-medium break-all">${escapeHtml(url)}</td>
                    <td class="py-2 px-3"><span class="inline-flex px-2 py-1 rounded border text-xs font-semibold ${statusClass(cwv)}">${statusLabel(cwv)}</span></td>
                    <td class="py-2 px-3"><span class="inline-flex px-2 py-1 rounded border text-xs font-semibold ${scoreBadgeClass(score)}">${escapeHtml(score ?? 'н/д')}</span></td>
                    <td class="py-2 px-3">${escapeHtml(formatMetric('lcp', lcp))} <span class="text-xs text-slate-400">ms</span></td>
                    <td class="py-2 px-3">${escapeHtml(formatMetric('inp', inp))} <span class="text-xs text-slate-400">ms</span></td>
                    <td class="py-2 px-3">${escapeHtml(formatMetric('cls', cls))}</td>
                    <td class="py-2 px-3 text-xs text-slate-700">${escapeHtml(focusText)}</td>
                </tr>
            `;
        }).join('');

        return `
            <div class="space-y-6 cwvpro-results">
                ${buildToolHeader({
                    gradient: 'from-sky-700 via-blue-700 to-indigo-700',
                    label: 'Core Web Vitals: Batch',
                    title: 'CWV Scanner: Batch',
                    score: Number.isFinite(Number(avgScore)) ? Number(avgScore) : null,
                    scoreLabel: 'Avg',
                    badges: [
                        { cls: `${statusClass(batchSummary.core_web_vitals_status)} border`, text: `CWV: ${statusLabel(batchSummary.core_web_vitals_status)}` },
                        { cls: 'bg-white/10 border border-white/20 text-white/90', text: `${fmtInt(totalUrls)} URLs` },
                        { cls: 'bg-white/10 border border-white/20 text-white/90', text: `Strategy: ${escapeHtml(String(strategy).toUpperCase())}` },
                    ],
                    metaLines: [
                        `Success: ${fmtInt(successUrls)} / ${fmtInt(totalUrls)}`,
                        `Avg LCP: ${fmt1(metricsAvg.lcp_ms)} ms`,
                        `Avg CLS: ${fmt3(metricsAvg.cls)}`,
                    ],
                    actionButtons: exportButtons,
                })}

                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${buildMetricCard('Total URLs', fmtInt(totalUrls))}
                    ${buildMetricCard('Success', fmtInt(successUrls))}
                    ${buildMetricCard('Avg Score', fmt1(avgScore))}
                    ${buildMetricCard('Errors', fmtInt(failedUrls))}
                </div>

                <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                    <h4 class="font-semibold mb-3">Детали по URL</h4>
                    <div class="overflow-auto border rounded-lg">
                        <table class="w-full min-w-[1100px] text-sm">
                            <thead>
                                <tr class="text-left text-xs text-slate-500 border-b bg-slate-50">
                                    <th class="py-2 px-3">#</th>
                                    <th class="py-2 px-3">URL</th>
                                    <th class="py-2 px-3">CWV</th>
                                    <th class="py-2 px-3">Score</th>
                                    <th class="py-2 px-3">LCP</th>
                                    <th class="py-2 px-3">INP</th>
                                    <th class="py-2 px-3">CLS</th>
                                    <th class="py-2 px-3">Приоритетный фокус</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${rows || '<tr><td colspan="8" class="py-4 px-3 text-center text-slate-500">Нет данных по URL</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                        <h4 class="font-semibold mb-3">Частые проблемы</h4>
                        ${commonOpportunities.length > 0 ? `
                            <div class="overflow-auto border rounded-lg">
                                <table class="w-full min-w-[640px] text-sm">
                                    <thead>
                                        <tr class="text-left text-xs text-slate-500 border-b bg-slate-50">
                                            <th class="py-2 px-3">Проблема</th>
                                            <th class="py-2 px-3">URL count</th>
                                            <th class="py-2 px-3">Critical/High</th>
                                            <th class="py-2 px-3">Savings</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${commonOpportunities.map((item) => `
                                            <tr class="border-b">
                                                <td class="py-2 px-3">${escapeHtml(item.title || item.id || '-')}</td>
                                                <td class="py-2 px-3">${fmtInt(item.count)}</td>
                                                <td class="py-2 px-3">${fmtInt(item.critical_count)} / ${fmtInt(item.high_count)}</td>
                                                <td class="py-2 px-3">${fmt1(item.total_savings_ms)} ms, ${fmt1(item.total_savings_kib)} KiB</td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            </div>
                        ` : '<div class="text-sm text-slate-500">Нет данных по common opportunities.</div>'}
                    </div>
                    <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                        <h4 class="font-semibold text-slate-800 mb-3">Batch Action Plan</h4>
                        ${batchActionPlan.length > 0 ? `
                            <div class="space-y-2">${batchActionPlan.slice(0, 20).map((item) => `
                                <div class="border rounded-lg p-3 bg-slate-50">
                                    <div class="text-xs text-slate-600 mb-1">${escapeHtml(item.priority || 'P2')} | затронуто URL: ${fmtInt(item.affected_urls)}</div>
                                    <div class="text-sm font-medium text-slate-800">${escapeHtml(item.action || '-')}</div>
                                </div>`).join('')}
                            </div>
                        ` : '<div class="text-sm text-slate-500">Action plan не сформирован.</div>'}
                        ${priorityUrls.length > 0 ? `
                            <div class="mt-4">
                                <div class="text-sm font-semibold text-slate-700 mb-2">Priority URLs</div>
                                <div class="space-y-1 text-xs text-slate-700">
                                    ${priorityUrls.map((item) => `<div class="rounded border border-slate-200 p-2 bg-slate-50"><span class="font-semibold break-all">${escapeHtml(item.url || '-')}</span><br><span class="text-slate-500">${escapeHtml(item.reason || '')}</span></div>`).join('')}
                                </div>
                            </div>
                        ` : ''}
                    </div>
                </div>

                ${buildRecommendations(recommendations)}
            </div>
        `;
    }

    const metrics = r.metrics || {};
    const categories = r.categories || {};
    const diagnostics = r.diagnostics || {};
    const resourceSummary = r.resource_summary || {};
    const thirdParty = r.third_party || {};
    const analysis = r.analysis || {};
    const pageContext = r.page_context || {};
    const actionPlan = Array.isArray(r.action_plan) ? r.action_plan : [];
    const opportunities = Array.isArray(r.opportunities) ? r.opportunities : [];
    opportunities.sort((a, b) => Number(a?.score ?? 1) - Number(b?.score ?? 1));
    const recommendations = Array.isArray(r.recommendations) ? r.recommendations : [];
    const score = summary.performance_score;
    const cwvStatus = String(summary.core_web_vitals_status || 'unknown');

    const metricCards = [
        { key: 'lcp', title: 'LCP', value: metrics.lcp?.field_value_ms ?? metrics.lcp?.lab_value_ms, unit: 'ms', status: metrics.lcp?.status },
        { key: 'inp', title: 'INP', value: metrics.inp?.field_value_ms ?? metrics.inp?.lab_value_ms, unit: 'ms', status: metrics.inp?.status },
        { key: 'cls', title: 'CLS', value: metrics.cls?.field_value ?? metrics.cls?.lab_value, unit: '', status: metrics.cls?.status },
        { key: 'fcp', title: 'FCP', value: metrics.fcp?.lab_value_ms, unit: 'ms', status: metrics.fcp?.status },
        { key: 'ttfb', title: 'TTFB', value: metrics.ttfb?.lab_value_ms, unit: 'ms', status: metrics.ttfb?.status },
        { key: 'speed_index', title: 'Speed Index', value: metrics.speed_index?.lab_value_ms, unit: 'ms', status: metrics.speed_index?.status },
        { key: 'tbt', title: 'TBT', value: metrics.tbt?.lab_value_ms, unit: 'ms', status: metrics.tbt?.status },
    ];

    const priorityLabel = (value) => {
        const token = String(value || '').toLowerCase();
        if (token === 'critical') return 'CRITICAL';
        if (token === 'high') return 'HIGH';
        if (token === 'medium') return 'MEDIUM';
        const raw = Number(value);
        if (!Number.isFinite(raw)) return 'INFO';
        if (raw < 0.5) return 'CRITICAL';
        if (raw < 0.75) return 'HIGH';
        return 'MEDIUM';
    };

    const priorityClass = (value) => {
        const token = String(value || '').toLowerCase();
        if (token === 'critical') return 'border-red-200 bg-red-50 text-red-800';
        if (token === 'high') return 'border-amber-200 bg-amber-50 text-amber-800';
        if (token === 'medium') return 'border-sky-200 bg-sky-50 text-sky-800';
        const raw = Number(value);
        if (!Number.isFinite(raw)) return 'border-slate-200 bg-slate-50 text-slate-700';
        if (raw < 0.5) return 'border-red-200 bg-red-50 text-red-800';
        if (raw < 0.75) return 'border-amber-200 bg-amber-50 text-amber-800';
        return 'border-sky-200 bg-sky-50 text-sky-800';
    };

    return `
        <div class="space-y-6 cwvpro-results">
            ${buildToolHeader({
                gradient: 'from-sky-700 via-blue-700 to-indigo-700',
                label: 'Core Web Vitals Scanner',
                title: 'Core Web Vitals',
                subtitle: resultData.url || r.url || '',
                score: Number.isFinite(Number(score)) ? Number(score) : null,
                scoreLabel: 'Performance',
                scoreGrade: summary.grade || null,
                badges: [
                    { cls: `${statusClass(cwvStatus)} border`, text: `CWV: ${statusLabel(cwvStatus)}` },
                    { cls: `${statusClass(summary.risk_level)} border`, text: `Risk: ${escapeHtml(String(summary.risk_level || 'unknown').toUpperCase())}` },
                    { cls: 'bg-white/10 border border-white/20 text-white/90', text: `Strategy: ${escapeHtml(String(strategy).toUpperCase())}` },
                ],
                metaLines: [
                    `Health Index: ${fmtInt(summary.health_index)}`,
                    `LCP: ${formatMetric('lcp', metrics.lcp?.field_value_ms ?? metrics.lcp?.lab_value_ms)} ms`,
                    `CLS: ${formatMetric('cls', metrics.cls?.field_value ?? metrics.cls?.lab_value)}`,
                ],
                actionButtons: exportButtons,
            })}

            <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
                ${buildMetricCard('Performance', fmtInt(score))}
                ${buildMetricCard('CWV', statusLabel(cwvStatus))}
                ${buildMetricCard('Health Index', fmtInt(summary.health_index))}
                ${buildMetricCard('Risk', escapeHtml(String(summary.risk_level || 'unknown').toUpperCase()))}
                ${buildMetricCard('Grade', escapeHtml(summary.grade || '-'))}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Метрики CWV</h4>
                    <div style="height:200px;"><canvas id="ds-chart-cwv-metrics"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Performance Score</h4>
                    <div style="height:200px;"><canvas id="ds-chart-cwv-score"></canvas></div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                <h4 class="font-semibold mb-3">Lighthouse categories</h4>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                    <div class="rounded-lg border p-3 ${scoreBadgeClass(categories.performance)}">Performance: <span class="font-semibold">${fmtInt(categories.performance)}</span></div>
                    <div class="rounded-lg border p-3 ${scoreBadgeClass(categories.accessibility)}">Accessibility: <span class="font-semibold">${fmtInt(categories.accessibility)}</span></div>
                    <div class="rounded-lg border p-3 ${scoreBadgeClass(categories.best_practices)}">Best Practices: <span class="font-semibold">${fmtInt(categories.best_practices)}</span></div>
                    <div class="rounded-lg border p-3 ${scoreBadgeClass(categories.seo)}">SEO: <span class="font-semibold">${fmtInt(categories.seo)}</span></div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                <h4 class="font-semibold mb-3">Метрики</h4>
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-3">
                    ${metricCards.map((item) => `
                        <div class="rounded-lg border p-3 ${statusClass(item.status)}">
                            <div class="text-xs opacity-80">${escapeHtml(item.title)}</div>
                            <div class="text-xl font-semibold mt-1">
                                ${escapeHtml(formatMetric(item.key, item.value))}
                                ${item.unit && formatMetric(item.key, item.value) !== 'н/д' ? `<span class="text-xs font-normal">${escapeHtml(item.unit)}</span>` : ''}
                            </div>
                            <div class="text-[11px] mt-1">${escapeHtml(statusLabel(item.status))}</div>
                        </div>
                    `).join('')}
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                    <h4 class="font-semibold mb-3">Технический профиль</h4>
                    <div class="grid grid-cols-2 gap-2 text-sm">
                        <div class="rounded border border-slate-200 bg-slate-50 p-2">Requests: <span class="font-semibold">${fmtInt(resourceSummary.total_requests ?? diagnostics.num_requests)}</span></div>
                        <div class="rounded border border-slate-200 bg-slate-50 p-2">Transfer: <span class="font-semibold">${fmt1(resourceSummary.total_transfer_kib)} KiB</span></div>
                        <div class="rounded border border-slate-200 bg-slate-50 p-2">Scripts: <span class="font-semibold">${fmtInt(diagnostics.num_scripts)}</span></div>
                        <div class="rounded border border-slate-200 bg-slate-50 p-2">Stylesheets: <span class="font-semibold">${fmtInt(diagnostics.num_stylesheets)}</span></div>
                        <div class="rounded border border-slate-200 bg-slate-50 p-2">Tasks > 50ms: <span class="font-semibold">${fmtInt(diagnostics.num_tasks_over_50ms)}</span></div>
                        <div class="rounded border border-slate-200 bg-slate-50 p-2">Tasks > 100ms: <span class="font-semibold">${fmtInt(diagnostics.num_tasks_over_100ms)}</span></div>
                    </div>
                    ${(resourceSummary.by_type || []).length > 0 ? `
                        <div class="mt-4 overflow-auto border rounded-lg">
                            <table class="w-full min-w-[420px] text-sm">
                                <thead>
                                    <tr class="text-left text-xs text-slate-500 border-b bg-slate-50">
                                        <th class="py-2 px-3">Resource type</th>
                                        <th class="py-2 px-3">Requests</th>
                                        <th class="py-2 px-3">Transfer</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    ${(resourceSummary.by_type || []).slice(0, 8).map((item) => `
                                        <tr class="border-b">
                                            <td class="py-2 px-3">${escapeHtml(item.resource_type || '-')}</td>
                                            <td class="py-2 px-3">${fmtInt(item.request_count)}</td>
                                            <td class="py-2 px-3">${fmt1(item.transfer_kib)} KiB</td>
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>
                        </div>
                    ` : ''}
                </div>
                <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                    <h4 class="font-semibold mb-3">3rd-party влияние</h4>
                    ${(thirdParty.top_entities || []).length > 0 ? `
                        <div class="overflow-auto border rounded-lg">
                            <table class="w-full min-w-[480px] text-sm">
                                <thead>
                                    <tr class="text-left text-xs text-slate-500 border-b bg-slate-50">
                                        <th class="py-2 px-3">Entity</th>
                                        <th class="py-2 px-3">Main thread</th>
                                        <th class="py-2 px-3">Blocking</th>
                                        <th class="py-2 px-3">Transfer</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    ${(thirdParty.top_entities || []).slice(0, 8).map((item) => `
                                        <tr class="border-b">
                                            <td class="py-2 px-3">${escapeHtml(item.entity || '-')}</td>
                                            <td class="py-2 px-3">${fmt1(item.main_thread_ms)} ms</td>
                                            <td class="py-2 px-3">${fmt1(item.blocking_ms)} ms</td>
                                            <td class="py-2 px-3">${fmt1(item.transfer_kib)} KiB</td>
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>
                        </div>
                    ` : '<div class="text-sm text-slate-500">Данные по third-party не обнаружены.</div>'}
                    <div class="mt-4 text-xs text-slate-500 space-y-1">
                        <div>Fetch time: ${escapeHtml(pageContext.fetch_time || '-')}</div>
                        <div>Final URL: ${escapeHtml(pageContext.final_url || '-')}</div>
                        <div>Lighthouse: ${escapeHtml(pageContext.lighthouse_version || '-')}</div>
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                <h4 class="font-semibold text-slate-800 mb-3">Action Plan</h4>
                ${actionPlan.length > 0 ? `
                    <div class="overflow-auto border rounded-lg">
                        <table class="w-full min-w-[760px] text-sm">
                            <thead>
                                <tr class="text-left text-xs text-slate-500 border-b bg-slate-50">
                                    <th class="py-2 px-3">Priority</th>
                                    <th class="py-2 px-3">Area</th>
                                    <th class="py-2 px-3">Owner</th>
                                    <th class="py-2 px-3">Action</th>
                                    <th class="py-2 px-3">Expected impact</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${actionPlan.map((item) => `
                                    <tr class="border-b align-top">
                                        <td class="py-2 px-3"><span class="inline-flex px-2 py-1 rounded border text-xs font-semibold ${item.priority === 'P1' ? 'border-red-200 bg-red-50 text-red-800' : item.priority === 'P2' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-sky-200 bg-sky-50 text-sky-800'}">${escapeHtml(item.priority || 'P3')}</span></td>
                                        <td class="py-2 px-3">${escapeHtml(item.area || '-')}</td>
                                        <td class="py-2 px-3">${escapeHtml(item.owner || '-')}</td>
                                        <td class="py-2 px-3">${escapeHtml(item.action || '-')}</td>
                                        <td class="py-2 px-3">${escapeHtml(item.expected_impact || '-')}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                ` : '<div class="text-sm text-slate-500">Action plan не сформирован.</div>'}
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 border border-slate-200">
                <h4 class="font-semibold mb-3">Top opportunities</h4>
                ${opportunities.length > 0 ? `
                    <div class="overflow-auto border rounded-lg">
                        <table class="w-full min-w-[980px] text-sm">
                            <thead>
                                <tr class="text-left text-xs text-slate-500 border-b bg-slate-50">
                                    <th class="py-2 px-3">Priority</th>
                                    <th class="py-2 px-3">Проблема</th>
                                    <th class="py-2 px-3">Group</th>
                                    <th class="py-2 px-3">Score</th>
                                    <th class="py-2 px-3">Savings</th>
                                    <th class="py-2 px-3">Эффект</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${opportunities.map((item) => `
                                    <tr class="border-b align-top">
                                        <td class="py-2 px-3">
                                            <span class="inline-flex px-2 py-1 rounded border text-xs font-semibold ${priorityClass(item.priority)}">${escapeHtml(priorityLabel(item.priority || item.score))}</span>
                                        </td>
                                        <td class="py-2 px-3">
                                            <div class="font-medium">${escapeHtml(item.title || '-')}</div>
                                            <div class="text-xs text-slate-500 mt-0.5">${renderMarkdownLinks(item.description || '')}</div>
                                        </td>
                                        <td class="py-2 px-3">${escapeHtml(item.group || '-')}</td>
                                        <td class="py-2 px-3">${escapeHtml(item.score ?? '-')}</td>
                                        <td class="py-2 px-3">${fmt1(item.savings_ms)} ms, ${fmt1(item.savings_kib)} KiB</td>
                                        <td class="py-2 px-3">${escapeHtml(item.display_value || '-')}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                ` : '<div class="text-sm text-slate-500">Серьёзных opportunity не найдено.</div>'}
            </div>

            ${buildRecommendations(recommendations)}
            ${(analysis.dominant_issues || []).length > 0 ? `
            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                <h4 class="font-semibold text-slate-800 mb-3">Dominant Issues</h4>
                <div class="flex flex-wrap gap-2">
                    ${(analysis.dominant_issues || []).slice(0, 8).map((item) => `<span class="px-2 py-1 rounded-full border border-slate-300 bg-slate-50 text-xs">${escapeHtml(item)}</span>`).join('')}
                </div>
            </div>` : ''}
        </div>
    `;
}

function generateRobotsHTML(data) {
    const resultData = data.result || data;
    const r = resultData.results || resultData;
    const sourceUrl = data.url || resultData.url || robotsData?.url || '';
    
    let userAgents = 'Не найдено';
    if (Array.isArray(r.user_agents)) {
        userAgents = r.user_agents.join(', ');
    } else if (typeof r.user_agents === 'number') {
        userAgents = `${r.user_agents} шт.`;
    } else if (r.user_agents) {
        userAgents = String(r.user_agents);
    }
    
    const issues = r.critical_issues || r.issues || [];
    const warnings = r.warning_issues || r.warnings || [];
    const infoIssues = r.info_issues || [];
    const sitemaps = r.sitemaps || [];
    const processedSitemaps = (sitemaps || []).map(s => s.url || s) || [];
    const sitemapChecks = r.sitemap_checks || [];
    const groups = r.groups_detail || r.groups || [];
    const recommendations = r.recommendations || [];
    const topFixes = r.top_fixes || [];
    const syntaxErrors = r.syntax_errors || [];
    const unsupportedDirectives = r.unsupported_directives || [];
    const hostValidation = r.host_validation || {};
    const hostWarnings = hostValidation.warnings || [];
    const hostList = hostValidation.hosts || r.hosts || [];
    const directiveConflicts = r.directive_conflicts || {};
    const conflictDetails = directiveConflicts.details || [];
    const longestMatch = r.longest_match_analysis || {};
    const longestMatchNotes = longestMatch.notes || [];
    const httpStatusAnalysis = r.http_status_analysis || {};
    const httpStatusNotes = httpStatusAnalysis.notes || [];
    const paramRecommendations = r.param_recommendations || [];
    const rawContent = r.raw_content || '';
    const rawWithLineNumbers = formatRobotsRawWithLineNumbers(rawContent);
    const qualityScore = Number.isFinite(r.quality_score) ? r.quality_score : 0;
    const qualityGrade = r.quality_grade || 'н/д';
    const productionReady = !!r.production_ready;
    
    let totalDisallow = 0;
    let totalAllow = 0;
    groups.forEach(g => {
        totalDisallow += (g.disallow || []).length;
        totalAllow += (g.allow || []).length;
    });
    
    const userAgentCount = r.user_agents || 0;
    const disallowRulesCount = totalDisallow;
    const allowRulesCount = totalAllow;
    
    let issuesHTML = '';
    if (issues.length > 0) {
        issuesHTML = issues.map(issue => `
            <div class="bg-red-50 border border-red-200 rounded-lg p-3 mb-2">
                <div class="flex items-center">
                    <i class="fas fa-exclamation-triangle text-red-500 mr-2"></i>
                    <span class="text-red-700 font-medium">${escapeHtml(issue)}</span>
                </div>
            </div>
        `).join('');
    }
    
    let warningsHTML = '';
    if (warnings.length > 0) {
        warningsHTML = warnings.map(warning => `
            <div class="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-2">
                <div class="flex items-center">
                    <i class="fas fa-exclamation-circle text-yellow-500 mr-2"></i>
                    <span class="text-yellow-700">${escapeHtml(warning)}</span>
                </div>
            </div>
        `).join('');
    }
    
    let sitemapsHtml = '';
    if (processedSitemaps.length > 0) {
        sitemapsHtml = processedSitemaps.map(sm => {
            const canCheck = /^https?:\/\//i.test(sm || '');
            const safeHref = sanitizeHttpUrl(sm || '');
            return `
            <div class="flex items-center gap-2 bg-green-50 border border-green-200 rounded px-3 py-2 mb-1">
                <i class="fas fa-sitemap text-green-500"></i>
                <a href="${safeHref || '#'}" target="_blank" class="text-green-700 hover:underline text-sm break-all flex-1">${escapeHtml(sm)}</a>
                <button
                    ${canCheck ? `onclick='runSitemapCheckFromRobots(${JSON.stringify(sm)})'` : 'disabled'}
                    class="text-xs px-3 py-1 rounded border ${canCheck ? 'border-blue-200 text-blue-700 hover:bg-blue-50' : 'border-gray-200 text-gray-400 cursor-not-allowed'}"
                    title="Run sitemap check in standalone sitemap tool"
                >
                    Проверить
                </button>
            </div>
        `;
        }).join('');
    }
    
    let groupsHTML = '';
    if (groups.length > 0) {
        groupsHTML = groups.map((group, idx) => `
            <div class="border rounded-lg mb-4 overflow-hidden">
                <div class="bg-gray-100 px-4 py-2 border-b">
                    <span class="font-semibold text-gray-700">Группа ${idx + 1}:</span>
                    <span class="text-gray-600 ml-2">${escapeHtml((group.user_agents || []).join(', '))}</span>
                </div>
                <div class="p-4">
                    ${group.disallow.length > 0 ? `
                        <div class="mb-3">
                            <h5 class="text-sm font-medium text-red-600 mb-2">Disallow (${group.disallow.length}):</h5>
                            <div class="max-h-40 overflow-y-auto">
                                ${group.disallow.map(d => `
                                    <div class="flex items-center text-sm font-mono bg-red-50 px-2 py-1 rounded mb-1">
                                        <span class="text-red-600 mr-2">Disallow:</span>
                                        <span class="text-gray-700 break-all">${escapeHtml(d.path)}</span>
                                        <span class="text-gray-400 ml-auto text-xs">line ${escapeHtml(d.line)}</span>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    ` : ''}
                    ${group.allow.length > 0 ? `
                        <div>
                            <h5 class="text-sm font-medium text-green-600 mb-2">Allow (${group.allow.length}):</h5>
                            <div class="max-h-40 overflow-y-auto">
                                ${group.allow.map(a => `
                                    <div class="flex items-center text-sm font-mono bg-green-50 px-2 py-1 rounded mb-1">
                                        <span class="text-green-600 mr-2">Allow:</span>
                                        <span class="text-gray-700 break-all">${escapeHtml(a.path)}</span>
                                        <span class="text-gray-400 ml-auto text-xs">line ${escapeHtml(a.line)}</span>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    ` : ''}
                </div>
            </div>
        `).join('');
    }
    
    let recommendationsHTML = '';
    if (recommendations.length > 0) {
        recommendationsHTML = recommendations.map(rec => `
            <div class="flex items-start mb-2 p-2 bg-blue-50 rounded">
                <i class="fas fa-lightbulb text-blue-500 mr-2 mt-1"></i>
                <span class="text-sm text-blue-700">${escapeHtml(rec)}</span>
            </div>
        `).join('');
    }

    let topFixesHTML = '';
    if (topFixes.length > 0) {
        topFixesHTML = topFixes.map(fix => `
            <div class="border rounded-lg p-3 mb-2 bg-indigo-50 border-indigo-200">
                <div class="text-sm font-semibold text-indigo-700">[${escapeHtml((fix.priority || 'medium').toUpperCase())}] ${escapeHtml(fix.title || 'Исправление')}</div>
                ${fix.why ? `<div class="text-sm text-indigo-700 mt-1">Почему: ${escapeHtml(fix.why)}</div>` : ''}
                ${fix.action ? `<div class="text-sm text-indigo-800 mt-1">Действие: ${escapeHtml(fix.action)}</div>` : ''}
            </div>
        `).join('');
    }

    let sitemapChecksHTML = '';
    if (sitemapChecks.length > 0) {
        sitemapChecksHTML = sitemapChecks.map(check => {
            const ok = check.ok;
            const badge = ok === true ? 'bg-green-100 text-green-700' : (ok === null ? 'bg-gray-100 text-gray-700' : 'bg-red-100 text-red-700');
            const status = ok === true ? '' : (ok === null ? 'ПРОПУЩЕНО' : 'ОШИБКА');
            const safeHref = sanitizeHttpUrl(check.url || '');
            return `
                <div class="border rounded-lg p-3 mb-2">
                    <div class="flex items-center justify-between">
                        <a href="${safeHref || '#'}" target="_blank" class="text-sm text-blue-600 hover:underline break-all">${escapeHtml(check.url)}</a>
                        <span class="text-xs px-2 py-1 rounded ${badge}">${escapeHtml(status)}</span>
                    </div>
                    <div class="text-xs text-gray-500 mt-1">
                        ${check.status_code ? `HTTP ${escapeHtml(check.status_code)}` : ''} ${check.error ? `• ${escapeHtml(check.error)}` : ''}
                    </div>
                </div>
            `;
        }).join('');
    }

    let syntaxErrorsHTML = '';
    if (syntaxErrors.length > 0) {
        syntaxErrorsHTML = syntaxErrors.map(err => `
            <div class="border rounded-lg p-3 mb-2 bg-rose-50 border-rose-200">
                <div class="text-sm text-rose-700 font-medium">Line ${err.line || '?'}</div>
                <div class="text-sm text-rose-700">${err.error || ''}</div>
                ${err.content ? `<div class="text-xs text-rose-600 mt-1 font-mono break-all">${escapeHtml(err.content)}</div>` : ''}
            </div>
        `).join('');
    }

    let unsupportedDirectivesHTML = '';
    if (unsupportedDirectives.length > 0) {
        unsupportedDirectivesHTML = unsupportedDirectives.map(item => `
            <div class="border rounded-lg p-3 mb-2 bg-amber-50 border-amber-200 text-amber-800 text-sm">
                <span class="font-semibold">Line ${escapeHtml(item.line || '?')}</span>
                : <span class="font-mono">${escapeHtml(item.directive || '')}${item.value ? `: ${escapeHtml(item.value)}` : ''}</span>
            </div>
        `).join('');
    }

    let hostValidationHTML = '';
    if (hostList.length > 0 || hostWarnings.length > 0) {
        hostValidationHTML = `
            ${hostList.length > 0 ? `<div class="text-sm text-gray-700 mb-2"><span class="font-semibold">Hosts:</span> ${escapeHtml(hostList.join(', '))}</div>` : ''}
            ${hostWarnings.map(w => `<div class="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-2 text-yellow-700 text-sm">${escapeHtml(w)}</div>`).join('')}
        `;
    }

    let conflictDetailsHTML = '';
    if (conflictDetails.length > 0) {
        conflictDetailsHTML = conflictDetails.map(item => `
            <div class="border rounded-lg p-3 mb-2 bg-orange-50 border-orange-200 text-sm text-orange-800">
                <span class="font-semibold">${escapeHtml(item.type || 'conflict')}</span>
                ${item.user_agent ? ` | UA: ${escapeHtml(item.user_agent)}` : ''}
                ${item.path ? ` | Path: ${escapeHtml(item.path)}` : ''}
                ${item.groups ? ` | Группы: ${escapeHtml(item.groups)}` : ''}
            </div>
        `).join('');
    }

    let longestMatchHTML = '';
    if (longestMatchNotes.length > 0) {
        longestMatchHTML = longestMatchNotes.map(note => `
            <div class="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-2 text-blue-700 text-sm">${escapeHtml(note)}</div>
        `).join('');
    }

    let httpStatusAnalysisHTML = '';
    if (httpStatusNotes.length > 0) {
        httpStatusAnalysisHTML = `
            <div class="text-sm text-gray-700 mb-2"><span class="font-semibold">Контекст HTTP-статуса:</span> ${escapeHtml(httpStatusAnalysis.status_code ?? r.status_code ?? 'н/д')}</div>
            ${httpStatusNotes.map(note => `<div class="bg-slate-50 border border-slate-200 rounded-lg p-3 mb-2 text-slate-700 text-sm">${escapeHtml(note)}</div>`).join('')}
        `;
    }

    let paramRecommendationsHTML = '';
    if (paramRecommendations.length > 0) {
        paramRecommendationsHTML = paramRecommendations.map(note => `
            <div class="bg-cyan-50 border border-cyan-200 rounded-lg p-3 mb-2 text-cyan-700 text-sm">${note}</div>
        `).join('');
    }
    
    const robotsActionBtns = `
        <button onclick="downloadTextReport()" class="ds-export-btn"><i class="fas fa-file-download"></i>TXT</button>
        <button onclick="downloadWordReport()" class="ds-export-btn"><i class="fas fa-file-word"></i>DOCX</button>
        <button onclick="copyToClipboard(JSON.stringify(robotsData, null, 2))" class="ds-export-btn"><i class="fas fa-copy"></i>JSON</button>
        <button onclick='copyToClipboard(${JSON.stringify(rawContent)})' class="ds-export-btn"><i class="fas fa-file-alt"></i>robots.txt</button>`;

    return `
        <div class="space-y-6 auditpro-results">
            ${buildToolHeader({
                gradient: 'from-slate-700 via-gray-700 to-zinc-800',
                label: 'Robots.txt Checker',
                title: 'Проверка Robots.txt',
                subtitle: sourceUrl,
                score: qualityScore,
                scoreLabel: 'качество',
                scoreGrade: qualityGrade,
                badges: [
                    { cls: productionReady ? 'bg-emerald-500/20 border border-emerald-400/40 text-emerald-100' : 'bg-amber-400/20 border border-amber-300/40 text-amber-100',
                      text: productionReady ? 'Production Ready' : 'Нужны правки' },
                    ...(issues.length > 0 ? [{ cls: 'bg-rose-500/20 border border-rose-400/40 text-rose-100', text: `${issues.length} ошибок` }] : []),
                    ...(warnings.length > 0 ? [{ cls: 'bg-amber-400/20 border border-amber-300/40 text-amber-100', text: `${warnings.length} предупреждений` }] : []),
                ],
                metaLines: [
                    `User-Agents: ${userAgentCount}`,
                    `Disallow: ${disallowRulesCount} / Allow: ${allowRulesCount}`,
                    `Размер: ${r.content_length || 0} байт`,
                ],
                actionButtons: robotsActionBtns,
            })}

            <div class="grid grid-cols-2 md:grid-cols-6 gap-3">
                ${buildMetricCard('User-Agents', userAgentCount)}
                ${buildMetricCard('Disallow', disallowRulesCount)}
                ${buildMetricCard('Allow', allowRulesCount)}
                ${buildMetricCard('Ошибок', issues.length)}
                ${buildMetricCard('Байт', r.content_length || 0)}
                ${buildMetricCard('Строк', r.lines_count || 0)}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Score</h4>
                    <div style="height:200px;"><canvas id="ds-chart-robots-score"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Issues by Severity</h4>
                    <div style="height:200px;"><canvas id="ds-chart-robots-severity"></canvas></div>
                </div>
            </div>

            <!-- Issues -->
            ${issuesHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-red-600 mb-3">
                        <i class="fas fa-exclamation-triangle mr-2"></i>Проблемы (${issues.length})
                    </h4>
                    ${issuesHTML}
                </div>
            ` : '<div class="bg-white rounded-xl shadow-md p-6"><div class="text-green-600"><i class="fas fa-check mr-2"></i>Критических проблем не обнаружено</div></div>'}
            
            <!-- Warnings -->
            ${warningsHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-yellow-600 mb-3">
                        <i class="fas fa-exclamation-circle mr-2"></i>Предупреждения (${warnings.length})
                    </h4>
                    ${warningsHTML}
                </div>
            ` : ''}

            ${infoIssues.length > 0 ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-sky-600 mb-3">
                        <i class="fas fa-info-circle mr-2"></i>Инфо (${infoIssues.length})
                    </h4>
                    ${infoIssues.map(item => `
                        <div class="bg-sky-50 border border-sky-200 rounded-lg p-3 mb-2 text-sky-700 text-sm">${item}</div>
                    `).join('')}
                </div>
            ` : ''}
             
            ${httpStatusAnalysisHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-slate-700 mb-3">
                        <i class="fas fa-server mr-2"></i>Анализ HTTP-статуса
                    </h4>
                    ${httpStatusAnalysisHTML}
                </div>
            ` : ''}

            ${unsupportedDirectivesHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-amber-700 mb-3">
                        <i class="fas fa-ban mr-2"></i>Неподдерживаемые директивы (${unsupportedDirectives.length})
                    </h4>
                    ${unsupportedDirectivesHTML}
                </div>
            ` : ''}

            ${syntaxErrorsHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-rose-700 mb-3">
                        <i class="fas fa-code-branch mr-2"></i>Синтаксические ошибки (${syntaxErrors.length})
                    </h4>
                    ${syntaxErrorsHTML}
                </div>
            ` : ''}

            ${hostValidationHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-yellow-700 mb-3">
                        <i class="fas fa-network-wired mr-2"></i>Проверка host
                    </h4>
                    ${hostValidationHTML}
                </div>
            ` : ''}

            ${conflictDetailsHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-orange-700 mb-3">
                        <i class="fas fa-random mr-2"></i>Конфликты директив (${conflictDetails.length})
                    </h4>
                    ${conflictDetailsHTML}
                </div>
            ` : ''}

            ${longestMatchHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-blue-700 mb-3">
                        <i class="fas fa-ruler-combined mr-2"></i>Примечания longest-match (${longestMatchNotes.length})
                    </h4>
                    ${longestMatchHTML}
                </div>
            ` : ''}

            ${paramRecommendationsHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-cyan-700 mb-3">
                        <i class="fas fa-filter mr-2"></i>Рекомендации Yandex Clean-param (${paramRecommendations.length})
                    </h4>
                    ${paramRecommendationsHTML}
                </div>
            ` : ''}

            <!-- Sitemaps -->
            ${processedSitemaps.length > 0 ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <div class="flex items-center justify-between mb-3 gap-3">
                        <h4 class="text-lg font-semibold text-green-600">
                            <i class="fas fa-sitemap mr-2"></i>Sitemap (${processedSitemaps.length})
                        </h4>
                        <a href="/" class="text-sm text-blue-600 hover:underline whitespace-nowrap">Открыть инструмент sitemap</a>
                    </div>
                    ${sitemapsHtml}
                </div>
            ` : ''}

            ${sitemapChecksHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-indigo-600 mb-3">
                        <i class="fas fa-shield-alt mr-2"></i>Проверки URL из sitemap (${sitemapChecks.length})
                    </h4>
                    ${sitemapChecksHTML}
                </div>
            ` : ''}
            
            <!-- Groups Detail -->
            ${groupsHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-gray-700 mb-3">
                        <i class="fas fa-list mr-2"></i>Детальный анализ 
                    </h4>
                    ${groupsHTML}
                </div>
            ` : ''}

            ${rawContent ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <div class="flex justify-between items-center mb-3">
                        <h4 class="text-lg font-semibold text-gray-700">
                            <i class="fas fa-file-code mr-2"></i>Robots.txt (raw)
                        </h4>
                        <div class="text-xs text-gray-500">Номера строк отображаются только визуально</div>
                    </div>
                    <div class="text-xs bg-gray-900 text-gray-100 p-4 rounded-lg overflow-auto max-h-96 font-mono">${rawWithLineNumbers}</div>
                </div>
            ` : ''}
            
            ${buildRecommendations(recommendations)}
            
            ${topFixesHTML ? `
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-indigo-700 mb-3">
                        <i class="fas fa-wrench mr-2"></i>Приоритетные исправления (${topFixes.length})
                    </h4>
                    ${topFixesHTML}
                </div>
            ` : ''}

            <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="text-lg font-semibold text-gray-700 mb-3">
                        <i class="fas fa-book mr-2"></i>Справка по robots.txt
                    </h4>
                    <div class="space-y-2 text-sm">
                        <a class="text-blue-600 hover:underline break-all" href="https://developers.google.com/search/docs/crawling-indexing/robots/robots_txt" target="_blank" rel="noopener noreferrer">
                            Google Search Central: спецификация robots.txt
                        </a>
                    <a class="text-blue-600 hover:underline break-all" href="https://yandex.com/support/webmaster/en/robot-workings/allow-disallow" target="_blank" rel="noopener noreferrer">
                        Yandex Webmaster: Allow/Disallow directives
                    </a>
                    <a class="text-blue-600 hover:underline break-all" href="https://yandex.com/support/webmaster/en/robot-workings/clean-param" target="_blank" rel="noopener noreferrer">
                        Yandex Webmaster: Clean-param directive
                    </a>
                </div>
            </div>
        </div>
    `;
}

function generateSitemapHTMLV2(result) {
    const r = result.results || result;
    const exportUrls = r.export_urls || [];
    const sitemapFiles = r.sitemap_files || [];
    const warnings = r.warnings || [];
    const errors = r.errors || [];
    const toolNotes = r.tool_notes || [];
    const recommendations = r.recommendations || [];
    const highlights = r.highlights || [];
    const qualityScore = Number.isFinite(r.quality_score) ? r.quality_score : null;
    const qualityGrade = r.quality_grade || '';
    const duplicateDetails = r.duplicate_details || [];
    const issues = r.issues || [];
    const severityCounts = r.severity_counts || {};
    const actionPlan = r.action_plan || [];
    const hreflang = r.hreflang || {};
    const freshness = r.freshness || {};
    const mediaExt = r.media_extensions || {};
    const liveChecks = r.live_indexability_checks || [];
    const inputUrl = r.input_url || '';
    const resolvedSitemapUrl = r.resolved_sitemap_url || result.url || '';
    const sitemapDiscoverySource = r.sitemap_discovery_source || '';
    const duplicateLines = duplicateDetails.map(d => `${d.url}	первый: ${d.first_sitemap || '-'}	дубликат: ${d.duplicate_sitemap || '-'}`);
    const exportChunkSize = r.export_chunk_size || 25000;
    const dateStamp = buildFilenameTimestamp();
    const safeDomain = sanitizeFilenamePart(extractDomain(result.url || resolvedSitemapUrl || '') || result.url || resolvedSitemapUrl || 'sitemap');
    sitemapExportUrls = exportUrls;
    sitemapDuplicateLines = duplicateLines;
    sitemapFilePreviewUrls = sitemapFiles.map(f => (f.urls || []).slice(0, 100000));

    const totalIssues = errors.length + warnings.length;
    const healthBadge = r.valid && totalIssues === 0 ? 'bg-emerald-100 text-emerald-700' : (r.valid ? 'bg-amber-100 text-amber-700' : 'bg-rose-100 text-rose-700');
    const healthText = r.valid && totalIssues === 0 ? 'Sitemap в порядке' : (r.valid ? 'Валиден, но есть предупреждения' : 'Требуются исправления');
    const totalUrls = Number(r.urls_count || 0);
    const uniqueUrls = Number(r.unique_urls_count || 0);
    const scanned = Number(r.sitemaps_scanned || 0);
    const validScanned = Number(r.sitemaps_valid || 0);
    const exported = Number(exportUrls.length || 0);
    const coverageUnique = totalUrls > 0 ? Math.round((uniqueUrls / totalUrls) * 1000) / 10 : 0;
    const exportCoverage = totalUrls > 0 ? Math.round((exported / totalUrls) * 1000) / 10 : 0;
    const riskLevel = errors.length > 0 ? 'Высокий' : (warnings.length > 0 ? 'Средний' : 'Низкий');
    const scanLimitFiles = Number(r.scan_limit_files || 0);
    const scanLimitReached = !!r.scan_limit_reached;
    const scanQueueRemaining = Number(r.scan_queue_remaining || 0);
    const scanProgressByLimit = scanLimitFiles > 0 ? Math.min(100, Math.round((scanned / scanLimitFiles) * 1000) / 10) : 100;

    const phaseFetchDone = Number.isFinite(Number(r.status_code)) && Number(r.status_code) > 0;
    const phaseTraverseDone = scanned > 0;
    const phaseParseDone = totalUrls > 0 || errors.length > 0;
    const phaseFinalizeDone = true;

    const scorePct = Math.max(0, Math.min(100, Number(qualityScore || 0)));
    const scoreRingStyle = `background: conic-gradient(#0ea5e9 ${scorePct}%, #e2e8f0 ${scorePct}% 100%);`;

    const levelClass = {
        critical: 'bg-rose-50 border-rose-200 text-rose-800',
        warning: 'bg-amber-50 border-amber-200 text-amber-800',
        info: 'bg-sky-50 border-sky-200 text-sky-800'
    };
    const levelLabel = {
        critical: 'Критично',
        warning: 'Предупреждение',
        info: 'Инфо'
    };

    const classifyNotice = (message) => {
        const text = String(message || '');
        if (/uniform lastmod|lastmod|однотипн|устаревш|будущ/i.test(text)) return 'lastmod';
        if (/preview truncated|export|превью|экспорт/i.test(text)) return 'preview_export';
        if (/hreflang/i.test(text)) return 'hreflang';
        if (/image|video|news|media|изображ|видео|новост/i.test(text)) return 'media';
        if (/sitemap index|self-references|repeated|индекс|самоссыл|повтор/i.test(text)) return 'index_structure';
        if (/http|parse|unsupported|парсинг|загрузк|неподдерж/i.test(text)) return 'fetch_parse';
        return 'other';
    };

    const groupLabel = {
        lastmod: 'Lastmod / Актуальность',
        preview_export: 'Превью / Экспорт',
        hreflang: 'Hreflang',
        media: 'Media-расширения',
        index_structure: 'Структура Sitemap Index',
        fetch_parse: 'Загрузка / Парсинг',
        other: 'Прочее'
    };

    const groupedWarnings = {};
    warnings.forEach((msg) => {
        const key = classifyNotice(msg);
        if (!groupedWarnings[key]) groupedWarnings[key] = [];
        groupedWarnings[key].push(msg);
    });

    const groupedErrors = {};
    errors.forEach((msg) => {
        const key = classifyNotice(msg);
        if (!groupedErrors[key]) groupedErrors[key] = [];
        groupedErrors[key].push(msg);
    });

    const groupedIssues = {
        critical: issues.filter(i => String(i.severity || '').toLowerCase() === 'critical'),
        warning: issues.filter(i => String(i.severity || '').toLowerCase() === 'warning'),
        info: issues.filter(i => String(i.severity || '').toLowerCase() === 'info'),
    };

    return `
        <div class="space-y-5">
            <div class="rounded-2xl overflow-hidden shadow-md border border-slate-200">
                <div class="bg-gradient-to-r from-cyan-700 via-sky-700 to-indigo-700 text-white p-6">
                    <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-5">
                        <div>
                            <div class="text-xs uppercase tracking-[0.18em] text-cyan-100 mb-2">Аналитика Sitemap</div>
                            <h3 class="text-2xl font-semibold mb-2">Валидация Sitemap</h3>
                            <div class="text-sm text-cyan-100 break-all">${escapeHtml(resolvedSitemapUrl || '')}</div>
                            ${inputUrl && inputUrl !== resolvedSitemapUrl ? `<div class="text-xs text-cyan-200 mt-1">Ввод: ${escapeHtml(inputUrl)} | Источник: ${escapeHtml(sitemapDiscoverySource === 'robots.txt' ? 'robots.txt' : sitemapDiscoverySource === 'direct_input' ? 'прямой URL' : sitemapDiscoverySource === 'common_path' ? 'стандартный путь' : 'автоопределение')}</div>` : ''}
                            <div class="mt-3 flex flex-wrap gap-2">
                                <span class="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${healthBadge}">${healthText}</span>
                                <span class="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-white/20 text-white">Ошибки: ${errors.length}</span>
                                <span class="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-white/20 text-white">Предупреждения: ${warnings.length}</span>
                                <span class="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-white/20 text-white">Риск: ${riskLevel}</span>
                            </div>
                        </div>
                        <div class="flex items-center gap-4">
                            <div class="relative w-24 h-24 rounded-full p-2" style="${scoreRingStyle}">
                                <div class="w-full h-full rounded-full bg-slate-900/80 backdrop-blur flex items-center justify-center">
                                    <div class="text-center">
                                        <div class="text-xl font-bold">${qualityScore !== null ? qualityScore : 'н/д'}</div>
                                        <div class="text-[10px] text-slate-300">оценка ${qualityGrade ? `(${qualityGrade})` : ''}</div>
                                    </div>
                                </div>
                            </div>
                            <div class="text-xs text-cyan-100 space-y-1">
                                <div>Просканировано файлов: <span class="text-white font-semibold">${scanned}</span></div>
                                <div>Всего URL: <span class="text-white font-semibold">${totalUrls}</span></div>
                                <div>Уникальных URL: <span class="text-white font-semibold">${uniqueUrls}</span></div>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="bg-white p-4 border-t border-slate-200">
                    <div class="flex flex-wrap gap-2">
                        <button onclick="copyToClipboard('${result.url}')" class="ds-export-btn">
                            <i class="fas fa-copy mr-1"></i>Копировать URL
                        </button>
                        ${exportUrls.length > 0 ? `
                            <button onclick='downloadCurrentSitemapUrls("sitemap-urls-${safeDomain}-${dateStamp}")' class="ds-export-btn">
                                <i class="fas fa-file-download mr-1"></i>Экспорт URL
                            </button>
                            <button onclick='downloadCurrentSitemapUrlsParts("sitemap-urls-${safeDomain}-${dateStamp}", ${exportChunkSize})' class="ds-export-btn">
                                <i class="fas fa-layer-group mr-1"></i>Экспорт частями
                            </button>
                        ` : ''}
                        <button onclick="downloadSitemapXlsxReport()" class="ds-export-btn">
                            <i class="fas fa-file-excel mr-1"></i>XLSX
                        </button>
                        <button onclick="downloadSitemapDocxReport()" class="ds-export-btn">
                            <i class="fas fa-file-word mr-1"></i>DOCX
                        </button>
                        <button onclick="copyToClipboard(JSON.stringify(lastTaskResult, null, 2))" class="ds-export-btn">
                            <i class="fas fa-code mr-1"></i>JSON
                        </button>
                    </div>
                </div>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
                <div class="bg-white rounded-xl border border-slate-200 p-3 shadow-sm transition hover:shadow-md"><div class="text-xs text-slate-500">HTTP</div><div class="text-xl font-semibold">${r.status_code || 'н/д'}</div></div>
                <div class="bg-white rounded-xl border border-slate-200 p-3 shadow-sm transition hover:shadow-md"><div class="text-xs text-slate-500">Sitemap-файлы</div><div class="text-xl font-semibold">${scanned}</div><div class="text-[11px] text-slate-500">валидных: ${validScanned}</div></div>
                <div class="bg-white rounded-xl border border-slate-200 p-3 shadow-sm transition hover:shadow-md"><div class="text-xs text-slate-500">URL</div><div class="text-xl font-semibold">${totalUrls}</div><div class="text-[11px] text-slate-500">уникальных: ${uniqueUrls}</div></div>
                <div class="bg-white rounded-xl border border-slate-200 p-3 shadow-sm transition hover:shadow-md"><div class="text-xs text-slate-500">Дубли</div><div class="text-xl font-semibold">${r.duplicate_urls_count || 0}</div></div>
                <div class="bg-white rounded-xl border border-slate-200 p-3 shadow-sm transition hover:shadow-md"><div class="text-xs text-slate-500">Неиндексируемые (live)</div><div class="text-xl font-semibold">${r.live_non_indexable_count || 0}</div></div>
                <div class="bg-white rounded-xl border border-slate-200 p-3 shadow-sm transition hover:shadow-md"><div class="text-xs text-slate-500">Глубина</div><div class="text-xl font-semibold">${r.max_depth_seen || 0}</div><div class="text-[11px] text-slate-500">самоссылки/повторы: ${r.self_child_refs || 0}/${r.repeated_child_refs || 0}</div></div>
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Оценка качества sitemap</h4>
                    <div style="height:200px;"><canvas id="ds-chart-sitemap-score"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Распределение URL</h4>
                    <div style="height:200px;"><canvas id="ds-chart-sitemap-dist"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Проблемы по severity</h4>
                    <div style="height:200px;"><canvas id="ds-chart-sitemap-severity"></canvas></div>
                </div>
            </div>

            <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5 xl:col-span-2">
                    <h4 class="font-semibold text-slate-800 mb-4">Этапы И Покрытие</h4>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs mb-4">
                        <div class="px-3 py-2 rounded border ${phaseFetchDone ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-gray-50 border-gray-200 text-gray-500'}">1. Загрузка</div>
                        <div class="px-3 py-2 rounded border ${phaseTraverseDone ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-gray-50 border-gray-200 text-gray-500'}">2. Обход</div>
                        <div class="px-3 py-2 rounded border ${phaseParseDone ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-gray-50 border-gray-200 text-gray-500'}">3. Парсинг</div>
                        <div class="px-3 py-2 rounded border ${phaseFinalizeDone ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-gray-50 border-gray-200 text-gray-500'}">4. Сборка отчета</div>
                    </div>
                    <div class="space-y-4">
                        <div>
                            <div class="flex justify-between text-xs text-slate-600 mb-1"><span>Прогресс обхода</span><span>${scanned} / ${scanLimitFiles || scanned} (${scanProgressByLimit}%)</span></div>
                            <div class="w-full bg-slate-200 rounded-full h-2.5 overflow-hidden"><div class="bg-sky-500 h-2.5 rounded-full transition-all duration-700" style="width:${scanProgressByLimit}%"></div></div>
                        </div>
                        <div>
                            <div class="flex justify-between text-xs text-slate-600 mb-1"><span>Покрытие уникальных URL</span><span>${coverageUnique}%</span></div>
                            <div class="w-full bg-slate-200 rounded-full h-2.5 overflow-hidden"><div class="bg-emerald-500 h-2.5 rounded-full transition-all duration-700" style="width:${coverageUnique}%"></div></div>
                        </div>
                        <div>
                            <div class="flex justify-between text-xs text-slate-600 mb-1"><span>Покрытие экспорта</span><span>${exportCoverage}%</span></div>
                            <div class="w-full bg-slate-200 rounded-full h-2.5 overflow-hidden"><div class="bg-indigo-500 h-2.5 rounded-full transition-all duration-700" style="width:${exportCoverage}%"></div></div>
                        </div>
                    </div>
                    ${scanLimitReached ? `<div class="mt-3 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">Достигнут лимит обхода: ${scanLimitFiles}. В очереди осталось sitemap: ${scanQueueRemaining}.</div>` : ''}
                    ${r.urls_export_truncated ? `<div class="mt-3 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">Превью экспорта ограничено до ${r.max_export_urls || 0} URL.</div>` : ''}
                    ${(r.export_parts_count || 0) > 1 ? `<div class="mt-2 text-xs text-slate-600">Рекомендуемое число частей экспорта: ${r.export_parts_count} (по ${exportChunkSize} URL)</div>` : ''}
                </div>
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                    <h4 class="font-semibold text-slate-800 mb-3">Расширенные Сигналы</h4>
                    <div class="space-y-3 text-sm">
                        <div class="rounded-lg border border-slate-200 p-3">
                            <div class="text-xs text-slate-500">Hreflang</div>
                            <div class="font-semibold">${hreflang.detected ? 'Обнаружен' : 'Не обнаружен'}</div>
                            <div class="text-xs text-slate-500">ссылок: ${hreflang.links_count || 0}, некорректных: ${(hreflang.invalid_code_count || 0) + (hreflang.invalid_href_count || 0)}</div>
                        </div>
                        <div class="rounded-lg border border-slate-200 p-3">
                            <div class="text-xs text-slate-500">Актуальность lastmod</div>
                            <div class="font-semibold">Устаревших URL: ${freshness.stale_lastmod_count || 0}</div>
                            <div class="text-xs text-slate-500">Без lastmod: ${freshness.lastmod_missing_count || 0}, с датой в будущем: ${freshness.lastmod_future_count || 0}</div>
                        </div>
                        <div class="rounded-lg border border-slate-200 p-3">
                            <div class="text-xs text-slate-500">Media-расширения</div>
                            <div class="font-semibold">${mediaExt.image_tags_count || 0}/${mediaExt.video_tags_count || 0}/${mediaExt.news_tags_count || 0}</div>
                            <div class="text-xs text-slate-500">пропущено обязательных: ${(mediaExt.image_missing_loc_count || 0) + (mediaExt.video_missing_required_count || 0) + (mediaExt.news_missing_required_count || 0)}</div>
                        </div>
                    </div>
                </div>
            </div>

            ${(issues.length > 0 || actionPlan.length > 0) ? `
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                    <div class="flex items-center justify-between mb-3">
                        <h4 class="font-semibold text-slate-800">Приоритизированные Находки</h4>
                        <div class="text-xs text-slate-600">критично: <span class="font-semibold">${severityCounts.critical || 0}</span>, предупреждений: <span class="font-semibold">${severityCounts.warning || 0}</span>, инфо: <span class="font-semibold">${severityCounts.info || 0}</span></div>
                    </div>
                    <div class="grid grid-cols-1 lg:grid-cols-3 gap-3">
                        ${['critical', 'warning', 'info'].map(level => `
                            <div class="rounded-lg border p-3 ${levelClass[level]}">
                                <div class="font-semibold text-sm mb-2 uppercase">${levelLabel[level] || level}</div>
                                ${groupedIssues[level].length > 0 ? groupedIssues[level].slice(0, 8).map(it => `
                                    <div class="mb-2 pb-2 border-b border-current/20 last:border-0 last:mb-0 last:pb-0">
                                        <div class="text-sm font-medium">${escapeHtml(it.title || it.code || 'Проблема')}</div>
                                        <div class="text-xs opacity-90">${escapeHtml(it.details || '')}</div>
                                    </div>
                                `).join('') : '<div class="text-xs opacity-75">Нет пунктов</div>'}
                            </div>
                        `).join('')}
                    </div>
                </div>
            ` : ''}

            ${(Object.keys(groupedErrors).length > 0 || Object.keys(groupedWarnings).length > 0) ? `
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                    <h4 class="font-semibold text-slate-800 mb-3">Структурированные Проблемы</h4>
                    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        <div>
                            <div class="text-sm font-semibold text-rose-700 mb-2">Ошибки (${errors.length})</div>
                            ${Object.keys(groupedErrors).length === 0 ? '<div class="text-xs text-slate-500">Ошибок нет</div>' : Object.entries(groupedErrors).map(([key, list]) => `
                                <details class="mb-2 border border-rose-200 rounded-lg bg-rose-50">
                                    <summary class="px-3 py-2 text-xs font-medium cursor-pointer">${escapeHtml(groupLabel[key] || key)} (${list.length})</summary>
                                    <div class="px-3 pb-3 space-y-1">${list.slice(0, 20).map(msg => `<div class="text-xs text-rose-700">${escapeHtml(msg)}</div>`).join('')}</div>
                                </details>
                            `).join('')}
                        </div>
                        <div>
                            <div class="text-sm font-semibold text-amber-700 mb-2">Предупреждения (${warnings.length})</div>
                            ${Object.keys(groupedWarnings).length === 0 ? '<div class="text-xs text-slate-500">Предупреждений нет</div>' : Object.entries(groupedWarnings).map(([key, list]) => `
                                <details class="mb-2 border border-amber-200 rounded-lg bg-amber-50">
                                    <summary class="px-3 py-2 text-xs font-medium cursor-pointer">${escapeHtml(groupLabel[key] || key)} (${list.length})</summary>
                                    <div class="px-3 pb-3 space-y-1">${list.slice(0, 20).map(msg => `<div class="text-xs text-amber-800">${escapeHtml(msg)}</div>`).join('')}</div>
                                </details>
                            `).join('')}
                        </div>
                    </div>
                </div>
            ` : ''}

            ${toolNotes.length > 0 ? `
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                    <h4 class="font-semibold text-slate-800 mb-3">Служебные Заметки (Не Ошибки Sitemap)</h4>
                    <div class="space-y-1">
                        ${toolNotes.map(note => `<div class="text-xs text-slate-600">${escapeHtml(note)}</div>`).join('')}
                    </div>
                </div>
            ` : ''}

            ${liveChecks.length > 0 ? `
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                    <h4 class="font-semibold text-slate-800 mb-3">Live-Выборка Индексируемости (${liveChecks.length})</h4>
                    <div class="space-y-2 max-h-80 overflow-auto pr-1">
                        ${liveChecks.slice(0, 20).map(item => `
                            <div class="border rounded-lg p-3 text-sm transition hover:shadow-sm ${item.indexable ? 'border-emerald-200 bg-emerald-50/40' : 'border-rose-200 bg-rose-50/40'}">
                                <div class="font-medium break-all">${escapeHtml(item.url || '')}</div>
                                <div class="text-xs text-gray-600 mt-1">HTTP: ${item.status_code ?? 'н/д'} | Индексируемость: <span class="${item.indexable ? 'text-emerald-700' : 'text-rose-700'} font-medium">${item.indexable ? 'Да' : 'Нет'}</span> | ${item.response_ms ?? 0} мс</div>
                                <div class="text-xs text-gray-600 mt-1">Canonical: <span class="font-medium">${escapeHtml(item.canonical_status || 'н/д')}</span>${item.canonical_url ? ` | <span class="break-all">${escapeHtml(item.canonical_url)}</span>` : ''}</div>
                                ${(item.reasons || []).length > 0 ? `<div class="text-xs text-rose-700 mt-1">${escapeHtml((item.reasons || []).join(' | '))}</div>` : ''}
                            </div>
                        `).join('')}
                    </div>
                </div>
            ` : ''}

            ${duplicateDetails.length > 0 ? `
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                    <div class="flex items-center justify-between mb-2">
                        <h4 class="font-semibold text-rose-700">Дубли URL (${duplicateDetails.length})</h4>
                        <button onclick='downloadCurrentSitemapDuplicates("sitemap-duplicates-${safeDomain}-${dateStamp}.txt")' class="px-2 py-1 rounded bg-rose-100 hover:bg-rose-200 text-xs text-rose-700 transition">
                            <i class="fas fa-file-download mr-1"></i>Экспорт дублей
                        </button>
                    </div>
                    ${r.duplicate_details_truncated ? `<div class="text-xs text-amber-700 mb-2">Список сокращен.</div>` : ''}
                    <div class="max-h-72 overflow-auto border rounded-lg">
                        ${duplicateDetails.slice(0, 500).map(d => `
                            <div class="text-xs border-b p-2 bg-white hover:bg-rose-50/40">
                                <div class="font-medium break-all text-gray-800">${escapeHtml(d.url)}</div>
                                <div class="text-gray-600">первый: <span class="break-all">${escapeHtml(d.first_sitemap || '-')}</span></div>
                                <div class="text-gray-600">дубликат: <span class="break-all">${escapeHtml(d.duplicate_sitemap || '-')}</span></div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            ` : ''}

            ${recommendations.length > 0 ? `
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                    <h4 class="font-semibold text-blue-700 mb-3">Рекомендации</h4>
                    <div class="space-y-2">${recommendations.map(rec => `<div class="text-sm text-blue-700 bg-blue-50 border border-blue-100 rounded p-2">${escapeHtml(rec)}</div>`).join('')}</div>
                </div>
            ` : ''}

            ${highlights.length > 0 ? `
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                    <h4 class="font-semibold text-emerald-700 mb-3">Ключевые выводы</h4>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-2">${highlights.map(item => `<div class="text-sm text-emerald-700 bg-emerald-50 border border-emerald-100 rounded p-2">${escapeHtml(item)}</div>`).join('')}</div>
                </div>
            ` : ''}

            ${actionPlan.length > 0 ? `
                <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                    <h4 class="font-semibold text-slate-800 mb-3">План Исправлений</h4>
                    <div class="space-y-2">
                        ${actionPlan.slice(0, 20).map(item => `
                            <div class="border rounded-lg p-3 bg-slate-50">
                                <div class="text-xs text-slate-600 mb-1">${escapeHtml(item.priority || 'P2')} | ${escapeHtml(item.owner || 'SEO')} | SLA: ${escapeHtml(item.sla || 'н/д')}</div>
                                <div class="text-sm font-medium text-slate-800">${escapeHtml(item.issue || '')}</div>
                                <div class="text-sm text-slate-700">${escapeHtml(item.action || '')}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            ` : ''}

            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                <h4 class="font-semibold text-slate-800 mb-3">Файлы Sitemap (${sitemapFiles.length})</h4>
                <div class="space-y-3">
                    ${sitemapFiles.map((f, idx) => `
                        <details class="border rounded-lg p-3 bg-white hover:bg-slate-50 transition">
                            <summary class="cursor-pointer list-none">
                                <div class="flex items-center justify-between gap-3">
                                    <div>
                                        <div class="text-sm font-medium break-all">${escapeHtml(f.sitemap_url)}</div>
                                        <div class="text-xs text-slate-600 mt-1">тип: ${escapeHtml(f.type || 'неизвестно')} | HTTP: ${f.status_code || 'н/д'} | URL: ${f.urls_count || 0} | дублей: ${f.duplicate_count || 0}</div>
                                    </div>
                                    <span class="text-xs px-2 py-1 rounded ${f.ok ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}">${f.ok ? 'OK' : 'ОШИБКА'}</span>
                                </div>
                            </summary>
                            <div class="mt-3 border-t pt-3">
                                ${(f.urls || []).length > 0 ? `
                                    <button onclick='downloadSitemapFilePreview(${idx}, "sitemap-${safeDomain}-${dateStamp}-${idx + 1}-urls-preview.txt")' class="px-2 py-1 rounded bg-emerald-100 hover:bg-emerald-200 text-xs text-emerald-800 transition mb-2">
                                        <i class="fas fa-file-download mr-1"></i>Экспорт URL-превью этого sitemap
                                    </button>
                                ` : ''}
                                ${(f.urls_omitted || 0) > 0 ? `<div class="text-xs text-amber-700 mb-1">Превью ограничено, скрыто: ${f.urls_omitted}</div>` : ''}
                                ${(f.duplicate_urls || []).length > 0 ? `<div class="text-xs text-rose-700 break-all mb-1">Примеры дублей: ${escapeHtml((f.duplicate_urls || []).slice(0, 10).join(' | '))}</div>` : ''}
                                ${(f.errors || []).length > 0 ? `<div class="text-xs text-rose-700">${escapeHtml((f.errors || []).join(' | '))}</div>` : ''}
                                ${(f.warnings || []).length > 0 ? `<div class="text-xs text-amber-700 mt-1">${escapeHtml((f.warnings || []).join(' | '))}</div>` : ''}
                            </div>
                        </details>
                    `).join('')}
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                <h4 class="font-semibold text-slate-800 mb-3">Справка</h4>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm">
                    <a class="text-blue-600 hover:underline break-all" href="https://www.sitemaps.org/protocol.html" target="_blank" rel="noopener noreferrer">Протокол Sitemap (sitemaps.org)</a>
                    <a class="text-blue-600 hover:underline break-all" href="https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview" target="_blank" rel="noopener noreferrer">Google Search Central: обзор Sitemap</a>
                </div>
            </div>
        </div>
    `;
}


function generateSitemapHTML(result) {
    const r = result.results || result;
    return `
        <div class="auditpro-results">
            <div class="bg-white rounded-xl shadow-md p-6">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-lg font-semibold">Валидация Sitemap</h3>
                <button onclick="copyToClipboard('${result.url}')" class="text-blue-600 hover:text-blue-800 text-sm">
                    <i class="fas fa-copy mr-1"></i>Копировать URL
                </button>
            </div>
            <div class="grid grid-cols-2 gap-4 text-sm">
                <div><span class="text-gray-600">URL:</span> <span class="font-medium">${result.url}</span></div>
                <div><span class="text-gray-600">Валиден:</span> <span class="font-medium">${r.valid ? 'Да' : 'Нет'}</span></div>
                <div><span class="text-gray-600">URLs найдено:</span> <span class="font-medium">${r.urls_count || 0}</span></div>
                <div><span class="text-gray-600">Стат:</span> <span class="font-medium">${r.status || 'н/д'}</span></div>
                ${r.error ? `<div class="col-span-2 text-red-600"><span class="font-medium">Ошибка: ${r.error}</span></div>` : ''}
            </div>
            </div>
        </div>
    `;
}


function filterBotRowsByCategory(category) {
    const select = document.getElementById('bot-category-filter');
    if (select) {
        select.value = category || 'all';
    }
    applyBotTableControls();
}

function applyBotTableControls() {
    const table = document.getElementById('bot-results-list');
    if (!table) return;
    const tbody = table.querySelector('tbody');
    if (!tbody) return;

    const category = (document.getElementById('bot-category-filter') || {}).value || 'all';
    const quick = (document.getElementById('bot-quick-filter') || {}).value || 'all';
    const sortKey = (document.getElementById('bot-sort-select') || {}).value || 'bot_az';

    const rows = Array.from(tbody.querySelectorAll('tr.bot-row'));

    const asNumber = (value, fallback = 0) => {
        const n = Number(value);
        return Number.isFinite(n) ? n : fallback;
    };

    const compare = (a, b) => {
        if (sortKey === 'indexable_first') {
            const aIdx = a.dataset.indexable === '1' ? 1 : 0;
            const bIdx = b.dataset.indexable === '1' ? 1 : 0;
            return aIdx - bIdx;
        }
        if (sortKey === 'response_asc') {
            return asNumber(a.dataset.responseMs, 10 ** 9) - asNumber(b.dataset.responseMs, 10 ** 9);
        }
        if (sortKey === 'response_desc') {
            return asNumber(b.dataset.responseMs, -1) - asNumber(a.dataset.responseMs, -1);
        }
        return (a.dataset.botName || '').localeCompare((b.dataset.botName || ''));
    };

    rows.sort(compare);
    rows.forEach((row) => tbody.appendChild(row));

    rows.forEach((row) => {
        const rowCategory = row.dataset.category || '';
        const byCategory = category === 'all' || rowCategory === category;
        let byQuick = true;
        if (quick === 'critical') byQuick = row.dataset.critical === '1';
        if (quick === 'ai') byQuick = row.dataset.ai === '1';
        if (quick === 'search') byQuick = row.dataset.search === '1';
        row.style.display = (byCategory && byQuick) ? '' : 'none';
    });
}

function generateBotHTML(result) {
    const r = result.results || result;
    const summary = r.summary || {};
    const categoryStats = r.category_stats || [];
    const issues = r.issues || [];
    const blockers = r.priority_blockers || [];
    const playbooks = r.playbooks || [];
    const alerts = r.alerts || [];
    const robotsLinter = (r.robots_linter || {}).findings || [];
    const allowlistScenarios = (r.allowlist_simulator || {}).scenarios || [];
    const actionCenter = (r.action_center || {}).by_owner || {};
    const evidenceRows = (r.evidence_pack || {}).rows || [];
    const batchRuns = r.batch_runs || [];
    const botRows = r.bot_rows || [];
    const serverTrend = r.trend || {};
    const serverTrendHistory = serverTrend.history || [];
    const trendSnapshots = serverTrendHistory.length ? serverTrendHistory : getBotSnapshotsForUrl(result.url || '');
    const latestTrend = serverTrend.latest || trendSnapshots[0] || null;
    const previousTrend = serverTrend.previous || (trendSnapshots.length > 1 ? trendSnapshots[1] : null);
    const criticalCategories = new Set(['Google', 'Yandex', 'Bing', 'Search']);

    const categorySet = new Set(botRows.map((x) => String(x.category || '').trim()).filter(Boolean));
    const categoryOptions = ['all', ...Array.from(categorySet).sort()];

    const botRowsHtml = botRows.map((row) => {
        const statusText = row.status || row.error || 'н/д';
        const responseMs = row.response_time_ms ? `${row.response_time_ms} ms` : 'н/д';
        const reachableLabel = row.accessible ? 'да' : 'нет';
        const indexable = (typeof row.indexable === 'boolean')
            ? row.indexable
            : (row.accessible && row.has_content && row.robots_allowed !== false && !row.x_robots_forbidden && !row.meta_forbidden);
        const indexableLabel = indexable ? 'да' : 'нет';
        const category = String(row.category || '');
        const isAi = category === 'AI';
        const isSearch = criticalCategories.has(category);
        return `
            <tr
                class="bot-row border-b"
                data-category="${category}"
                data-ai="${isAi ? '1' : '0'}"
                data-search="${isSearch ? '1' : '0'}"
                data-critical="${isSearch ? '1' : '0'}"
                data-indexable="${indexable ? '1' : '0'}"
                data-reachable="${row.accessible ? '1' : '0'}"
                data-response-ms="${row.response_time_ms || ''}"
                data-bot-name="${String(row.bot_name || '').toLowerCase()}"
            >
                <td class="py-2 pr-2 font-medium">${row.bot_name || ''}</td>
                <td class="py-2 pr-2 text-gray-700">${row.category || ''}</td>
                <td class="py-2 pr-2 ${row.accessible ? 'text-green-700' : 'text-red-700'}">${statusText}</td>
                <td class="py-2 pr-2">${responseMs}</td>
                <td class="py-2 pr-2 ${row.accessible ? 'text-green-700' : 'text-red-700'}">${reachableLabel}</td>
                <td class="py-2 pr-2 ${indexable ? 'text-green-700' : 'text-amber-700'}">${indexableLabel}</td>
                <td class="py-2 pr-2 text-xs text-gray-600">${escapeHtml(row.indexability_reason || '')}</td>
            </tr>
        `;
    }).join('');

    const matrixRows = categoryStats.map((c) => `
        <tr class="border-b text-sm">
            <td class="py-2 pr-2 font-medium">${c.category}</td>
            <td class="py-2 pr-2">${c.total}</td>
            <td class="py-2 pr-2">${c.accessible}</td>
            <td class="py-2 pr-2">${c.indexable ?? 0}</td>
            <td class="py-2 pr-2">${c.non_indexable ?? 0}</td>
            <td class="py-2 pr-2">${c.indexable_pct ?? 0}%</td>
            <td class="py-2 pr-2">${c.priority_risk_score ?? 0}</td>
        </tr>
    `).join('');

    const blockersHtml = blockers.length > 0
        ? blockers.slice(0, 8).map((b) => `
            <div class="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
                <div class="font-semibold text-amber-900">${b.title}</div>
                <div class="text-amber-800">Приоритет: ${b.priority_score} | Затронуто ботов: ${b.affected_bots}</div>
                <div class="text-amber-700">${b.details}</div>
                <div class="text-amber-700">Примеры ботов: ${(b.sample_bots || []).join(', ') || '-'}</div>
            </div>
        `).join('')
        : '<div class="text-sm text-gray-500">Приоритетные блокеры не найдены.</div>';

    const playbooksHtml = playbooks.length > 0
        ? playbooks.slice(0, 12).map((p) => `
            <div class="rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm">
                <div class="font-semibold text-sky-900">${p.title || 'План действий'}</div>
                <div class="text-sky-800">Владелец: ${p.owner || 'team'} | Тип: ${p.type || 'general'}</div>
                <ul class="list-disc pl-5 mt-2 text-sky-900">
                    ${(p.actions || []).slice(0, 5).map((a) => `<li>${a}</li>`).join('') || '<li>Список действий пуст.</li>'}
                </ul>
            </div>
        `).join('')
        : '<div class="text-sm text-gray-500">Плейбуки не сформированы.</div>';

    const issueRows = issues.slice(0, 20).map(i => `
        <div class="py-2 border-b text-sm">
            <div class="font-medium ${i.severity === 'critical' ? 'text-red-700' : (i.severity === 'warning' ? 'text-amber-700' : 'text-blue-700')}">
                ${(i.severity || 'info').toUpperCase()} - ${i.bot || 'bot'}
            </div>
            <div class="text-gray-700">${i.title || ''}</div>
            <div class="text-gray-500">${i.details || ''}</div>
        </div>
    `).join('');

    const sampleRow = botRows.find((x) => !x.indexable || !x.accessible || ((x.waf_cdn_signal || {}).detected)) || botRows[0] || null;
    const botSample = (sampleRow && sampleRow.response_sample) ? String(sampleRow.response_sample) : '';
    const bypassProbe = r.waf_bypass_probe || {};
    const bypassSample = bypassProbe.sample ? String(bypassProbe.sample) : '';

    const trendRows = trendSnapshots.slice(0, 8).map((s, idx) => `
        <tr class="border-b">
            <td class="py-2 pr-2">${idx + 1}</td>
            <td class="py-2 pr-2">${new Date(s.timestamp).toLocaleString()}</td>
            <td class="py-2 pr-2">${s.indexable || 0}/${s.total || 0}</td>
            <td class="py-2 pr-2">${s.crawlable || 0}/${s.total || 0}</td>
            <td class="py-2 pr-2">${s.renderable || 0}/${s.total || 0}</td>
            <td class="py-2 pr-2">${s.avg_response_time_ms || 0}</td>
            <td class="py-2 pr-2">${s.critical_issues || 0}</td>
            <td class="py-2 pr-2">${s.warning_issues || 0}</td>
        </tr>
    `).join('');
    const alertRows = alerts.map((a) => `
        <div class="py-2 border-b text-sm">
            <div class="font-medium ${(a.severity || 'info') === 'critical' ? 'text-red-700' : ((a.severity || 'info') === 'warning' ? 'text-amber-700' : 'text-blue-700')}">${(a.severity || 'info').toUpperCase()} - ${escapeHtml(a.code || 'alert')}</div>
            <div class="text-gray-700">${escapeHtml(a.message || '')}</div>
        </div>
    `).join('');
    const linterRows = robotsLinter.map((f) => `
        <tr class="border-b">
            <td class="py-2 pr-2 text-sm">${escapeHtml((f.severity || 'info').toUpperCase())}</td>
            <td class="py-2 pr-2 text-sm">${escapeHtml(f.code || '')}</td>
            <td class="py-2 pr-2 text-sm">${escapeHtml(f.message || '')}</td>
        </tr>
    `).join('');
    const allowlistRows = allowlistScenarios.map((s) => `
        <tr class="border-b">
            <td class="py-2 pr-2 text-sm">${escapeHtml(s.category || '')}</td>
            <td class="py-2 pr-2 text-sm">${s.affected_bots || 0}</td>
            <td class="py-2 pr-2 text-sm">${(s.delta_renderable > 0 ? '+' : '') + (s.delta_renderable || 0)}</td>
            <td class="py-2 pr-2 text-sm">${(s.delta_indexable > 0 ? '+' : '') + (s.delta_indexable || 0)}</td>
            <td class="py-2 pr-2 text-sm">${s.projected_indexable || 0}/${summary.total || 0} (${s.projected_indexable_pct || 0}%)</td>
        </tr>
    `).join('');
    const ownerBlocks = Object.keys(actionCenter).sort().map((owner) => {
        const rows = actionCenter[owner] || [];
        const top = rows.slice(0, 3).map((x) => `<li>${escapeHtml(x.title || 'Действие')} (p${x.priority_score || 0})</li>`).join('');
        return `
            <div class="rounded-lg border border-indigo-200 bg-indigo-50 p-3 text-sm">
                <div class="flex items-center justify-between mb-2">
                    <div class="font-semibold text-indigo-900">${escapeHtml(owner)}</div>
                    <button onclick='copyBotOwnerTasks(${JSON.stringify(owner)})' class="text-indigo-700 hover:text-indigo-900 text-xs"><i class="fas fa-copy mr-1"></i>Копировать</button>
                </div>
                <ul class="list-disc pl-5 text-indigo-900">${top || '<li>Нет действий</li>'}</ul>
            </div>
        `;
    }).join('');
    const evidenceTableRows = evidenceRows.slice(0, 20).map((e) => `
        <tr class="border-b">
            <td class="py-2 pr-2 text-sm">${escapeHtml(e.bot || '')}</td>
            <td class="py-2 pr-2 text-sm">${e.status ?? 'н/д'}</td>
            <td class="py-2 pr-2 text-sm">${escapeHtml(e.indexability_reason || '')}</td>
            <td class="py-2 pr-2 text-sm">${e.waf_detected ? 'да' : 'нет'} (${e.waf_confidence ?? 0})</td>
            <td class="py-2 pr-2 text-xs text-gray-600">${escapeHtml(e.response_sample || '')}</td>
        </tr>
    `).join('');
    const batchRows = batchRuns.map((b, idx) => `
        <tr class="border-b">
            <td class="py-2 pr-2 text-sm">${idx + 1}</td>
            <td class="py-2 pr-2 text-sm break-all">${escapeHtml(b.url || '')}</td>
            <td class="py-2 pr-2 text-sm">${b.indexable || 0}/${b.total || 0}</td>
            <td class="py-2 pr-2 text-sm">${b.crawlable || 0}/${b.total || 0}</td>
            <td class="py-2 pr-2 text-sm">${b.renderable || 0}/${b.total || 0}</td>
            <td class="py-2 pr-2 text-sm">${b.avg_response_time_ms || 0}</td>
            <td class="py-2 pr-2 text-sm">${b.critical_issues || 0}</td>
            <td class="py-2 pr-2 text-sm">${b.warning_issues || 0}</td>
        </tr>
    `).join('');

    const botScore = summary.total ? Math.round((summary.indexable || 0) / summary.total * 100) : null;
    const botActionBtns = `
        <button onclick="downloadBotDocxReport()" class="ds-export-btn"><i class="fas fa-file-word mr-1"></i>DOCX</button>
        <button onclick="downloadBotXlsxReport()" class="ds-export-btn"><i class="fas fa-file-excel mr-1"></i>XLSX</button>
        <button onclick="copyBotPlaybooksAsJira()" class="ds-export-btn"><i class="fas fa-list-check mr-1"></i>Jira задачи</button>
        <button onclick="downloadBotTrendHistoryJson()" class="ds-export-btn"><i class="fas fa-chart-line mr-1"></i>Тренды JSON</button>`;

    return `
        <div class="auditpro-results space-y-6">
            ${buildToolHeader({
                gradient: 'from-violet-700 via-purple-700 to-indigo-700',
                label: 'Bot Accessibility Checker v2',
                title: 'Проверка доступности ботов',
                subtitle: result.url,
                score: botScore,
                scoreLabel: 'индекс',
                badges: [
                    { cls: 'bg-white/10 border border-white/20 text-white/90', text: `${(r.bots_checked || []).length || botRows.length} ботов` },
                    ...(summary.total ? [{ cls: 'bg-emerald-500/20 border border-emerald-400/40 text-emerald-100', text: `${summary.indexable || 0}/${summary.total} индекс.` }] : []),
                    ...(issues.filter(i => i.severity === 'critical').length > 0 ? [{ cls: 'bg-rose-500/20 border border-rose-400/40 text-rose-100', text: `${issues.filter(i=>i.severity==='critical').length} критичных` }] : []),
                ],
                metaLines: [
                    `Сканируемо: ${summary.crawlable || 0}/${summary.total || 0}`,
                    `Ср. ответ: ${summary.avg_response_time_ms || 0} ms`,
                    `AI-блокировки: ${r.ai_block_expected ? 'да' : 'нет'}`,
                ],
                actionButtons: botActionBtns,
            })}

            <div class="grid grid-cols-2 md:grid-cols-5 gap-3">
                ${buildMetricCard('Ботов', (r.bots_checked || []).length || botRows.length)}
                ${buildMetricCard('Доступно', `${summary.accessible || 0}/${summary.total || 0}`)}
                ${buildMetricCard('Индексируемо', `${summary.indexable || 0}/${summary.total || 0}`)}
                ${buildMetricCard('Рендерится', `${summary.renderable || 0}/${summary.total || 0}`)}
                ${buildMetricCard('Ср. ответ', `${summary.avg_response_time_ms || 0} ms`)}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Доступность ботов</h4>
                    <div style="height:200px;"><canvas id="ds-chart-bot-radar"></canvas></div>
                </div>
            </div>

            ${buildFindingsGrid(
                issues.filter(i => i.severity === 'critical'),
                issues.filter(i => i.severity === 'warning'),
                issues.filter(i => i.severity !== 'critical' && i.severity !== 'warning')
            )}

            ${r.batch_mode ? `
            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Пакетные прогоны</h4>
                <div class="overflow-auto">
                    <table class="w-full min-w-[980px]">
                        <thead>
                            <tr class="text-left text-xs text-slate-500 border-b">
                                <th class="py-2 pr-2">#</th>
                                <th class="py-2 pr-2">URL</th>
                                <th class="py-2 pr-2">Индексируемо</th>
                                <th class="py-2 pr-2">Сканируемо</th>
                                <th class="py-2 pr-2">Рендерится</th>
                                <th class="py-2 pr-2">Среднее, мс</th>
                                <th class="py-2 pr-2">Критично</th>
                                <th class="py-2 pr-2">Предупреждения</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${batchRows || '<tr><td colspan="8" class="py-3 text-gray-500">Нет строк пакетных прогонов.</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>` : ''}

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Алерты</h4>
                ${alertRows || '<div class="text-sm text-gray-500">Активных алертов нет.</div>'}
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">История запусков (тренд домена)</h4>
                <div class="overflow-auto">
                    <table class="w-full min-w-[920px] text-sm">
                        <thead>
                            <tr class="text-left text-xs text-slate-500 border-b">
                                <th class="py-2 pr-2">#</th>
                                <th class="py-2 pr-2">Время запуска</th>
                                <th class="py-2 pr-2">Индексируемо</th>
                                <th class="py-2 pr-2">Сканируемо</th>
                                <th class="py-2 pr-2">Рендерится</th>
                                <th class="py-2 pr-2">Среднее, мс</th>
                                <th class="py-2 pr-2">Критично</th>
                                <th class="py-2 pr-2">Предупреждения</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${trendRows || '<tr><td colspan="8" class="py-3 text-gray-500">Для этого домена пока нет истории.</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="border-t pt-4">
                <div class="flex items-center justify-between mb-2">
                    <h4 class="font-semibold">Результаты по ботам</h4>
                    <div class="flex items-center gap-2 text-sm">
                        <label for="bot-quick-filter" class="text-gray-600">Быстрый фильтр:</label>
                        <select id="bot-quick-filter" class="border rounded px-2 py-1" onchange="applyBotTableControls()">
                            <option value="all">все</option>
                            <option value="critical">только критичные боты</option>
                            <option value="search">только поисковые</option>
                            <option value="ai">только AI</option>
                        </select>
                        <label for="bot-category-filter" class="text-gray-600">Фильтр:</label>
                        <select id="bot-category-filter" class="border rounded px-2 py-1" onchange="applyBotTableControls()">
                            ${categoryOptions.map((cat) => `<option value="${cat}">${cat}</option>`).join('')}
                        </select>
                        <label for="bot-sort-select" class="text-gray-600">Сортировка:</label>
                        <select id="bot-sort-select" class="border rounded px-2 py-1" onchange="applyBotTableControls()">
                            <option value="bot_az">бот A-Z</option>
                            <option value="indexable_first">сначала неиндексируемые</option>
                            <option value="response_asc">ответ по возрастанию</option>
                            <option value="response_desc">ответ по убыванию</option>
                        </select>
                    </div>
                </div>
                <div class="overflow-auto">
                    <table class="w-full min-w-[980px] text-sm" id="bot-results-list">
                        <thead>
                            <tr class="text-left text-xs text-slate-500 border-b">
                                <th class="py-2 pr-2">Бот</th>
                                <th class="py-2 pr-2">Категория</th>
                                <th class="py-2 pr-2">HTTP/Ошибка</th>
                                <th class="py-2 pr-2">Ответ</th>
                                <th class="py-2 pr-2">Доступен</th>
                                <th class="py-2 pr-2">Индексируемо</th>
                                <th class="py-2 pr-2">Причина</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${botRowsHtml || '<tr><td colspan="7" class="py-3 text-gray-500">Нет строк по ботам.</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Матрица категорий</h4>
                <div class="overflow-auto">
                    <table class="w-full min-w-[760px]">
                        <thead>
                            <tr class="text-left text-xs text-slate-500 border-b">
                                <th class="py-2 pr-2">Категория</th>
                                <th class="py-2 pr-2">Всего</th>
                                <th class="py-2 pr-2">Доступно</th>
                                <th class="py-2 pr-2">Индексируемо</th>
                                <th class="py-2 pr-2">Не индексируемо</th>
                                <th class="py-2 pr-2">Индексируемо, %</th>
                                <th class="py-2 pr-2">Оценка риска</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${matrixRows || '<tr><td colspan="7" class="py-3 text-gray-500">Нет статистики по категориям.</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Приоритетные блокеры (по критичности ботов)</h4>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    ${blockersHtml}
                </div>
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Плейбуки</h4>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    ${playbooksHtml}
                </div>
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Центр действий (по владельцу)</h4>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                    ${ownerBlocks || '<div class="text-sm text-gray-500">Нет действий по владельцам.</div>'}
                </div>
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Линтер Robots Policy</h4>
                <div class="overflow-auto">
                    <table class="w-full min-w-[760px]">
                        <thead><tr class="text-left text-xs text-slate-500 border-b"><th class="py-2 pr-2">Критичность</th><th class="py-2 pr-2">Код</th><th class="py-2 pr-2">Сообщение</th></tr></thead>
                        <tbody>${linterRows || '<tr><td colspan="3" class="py-3 text-gray-500">Линтер не нашел замечаний.</td></tr>'}</tbody>
                    </table>
                </div>
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Симулятор allowlist</h4>
                <div class="overflow-auto">
                    <table class="w-full min-w-[760px]">
                        <thead><tr class="text-left text-xs text-slate-500 border-b"><th class="py-2 pr-2">Категория</th><th class="py-2 pr-2">Затронуто ботов</th><th class="py-2 pr-2">Дельта рендеринга</th><th class="py-2 pr-2">Дельта индексации</th><th class="py-2 pr-2">Прогноз индексируемых</th></tr></thead>
                        <tbody>${allowlistRows || '<tr><td colspan="5" class="py-3 text-gray-500">Нет данных симуляции.</td></tr>'}</tbody>
                    </table>
                </div>
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Топ проблем</h4>
                ${issueRows || '<div class="text-sm text-gray-500">Проблем не обнаружено</div>'}
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Пример ответа: бот vs WAF bypass</h4>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
                    <div class="rounded-lg border border-rose-200 bg-rose-50 p-3">
                        <div class="font-semibold text-rose-900 mb-2">Пример ответа бота ${sampleRow ? `(${escapeHtml(sampleRow.bot_name || 'bot')})` : ''}</div>
                        <div class="text-rose-700 mb-2">HTTP: ${sampleRow?.status ?? 'н/д'} | WAF: ${(sampleRow?.waf_cdn_signal || {}).detected ? 'обнаружен' : 'нет'}</div>
                        <div class="text-rose-900 whitespace-pre-wrap break-words">${escapeHtml(botSample || 'Нет примера ответа бота.')}</div>
                    </div>
                    <div class="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
                        <div class="font-semibold text-emerald-900 mb-2">Пример bypass-запроса (как браузер)</div>
                        <div class="text-emerald-700 mb-2">HTTP: ${bypassProbe.status ?? 'н/д'} | WAF: ${(bypassProbe.waf_cdn_signal || {}).detected ? 'обнаружен' : 'нет'}</div>
                        <div class="text-emerald-900 whitespace-pre-wrap break-words">${escapeHtml(bypassSample || bypassProbe.error || 'Нет примера bypass-ответа.')}</div>
                    </div>
                </div>
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Консистентность host</h4>
                <div class="text-sm ${((r.host_consistency || {}).consistent === false) ? 'text-amber-700' : 'text-green-700'}">
                    ${((r.host_consistency || {}).consistent === false) ? 'Обнаружены расхождения между вариантами host.' : 'Варианты host выглядят консистентно.'}
                </div>
                ${((r.host_consistency || {}).notes || []).length ? `<ul class="list-disc pl-5 text-sm text-gray-700 mt-2">${(r.host_consistency.notes || []).map(n => `<li>${n}</li>`).join('')}</ul>` : ''}
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Пакет доказательств (проблемные боты)</h4>
                <div class="overflow-auto">
                    <table class="w-full min-w-[980px]">
                        <thead><tr class="text-left text-xs text-slate-500 border-b"><th class="py-2 pr-2">Бот</th><th class="py-2 pr-2">HTTP</th><th class="py-2 pr-2">Причина</th><th class="py-2 pr-2">WAF</th><th class="py-2 pr-2">Пример</th></tr></thead>
                        <tbody>${evidenceTableRows || '<tr><td colspan="5" class="py-3 text-gray-500">Нет строк доказательств.</td></tr>'}</tbody>
                    </table>
                </div>
            </div>

            <div class="border-t pt-4 mt-4">
                <h4 class="font-semibold mb-2">Сравнение с baseline</h4>
                ${
                    (r.baseline_diff || {}).has_baseline
                        ? `<div class="overflow-auto"><table class="w-full min-w-[520px] text-sm"><thead><tr class="text-left text-xs text-slate-500 border-b"><th class="py-2 pr-2">Метрика</th><th class="py-2 pr-2">Текущее</th><th class="py-2 pr-2">Базовое</th><th class="py-2 pr-2">Дельта</th></tr></thead><tbody>${((r.baseline_diff.metrics || []).map(m => `<tr class="border-b"><td class="py-2 pr-2">${m.metric || ''}</td><td class="py-2 pr-2">${m.current ?? ''}</td><td class="py-2 pr-2">${m.baseline ?? ''}</td><td class="py-2 pr-2">${m.delta ?? ''}</td></tr>`).join('') || '<tr><td colspan=\"4\" class=\"py-2 text-gray-500\">Нет метрик.</td></tr>')}</tbody></table></div>`
                        : `<div class="text-sm text-gray-500">${(r.baseline_diff || {}).message || 'Baseline не найден.'}</div>`
                }
            </div>
            </div>
        </div>
    `;
}

function generateSiteAnalysisHTML(result) {
    const r = result.results || result;
    if (r.error) {
        return `
            <div class="bg-white rounded-xl shadow-md p-6">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-lg font-semibold">Анализ сайта</h3>
                    <button onclick="copyCurrentTaskJson()" class="text-blue-600 hover:text-blue-800 text-sm">
                        <i class="fas fa-copy mr-1"></i>Копировать JSON
                    </button>
                </div>
                <div class="bg-red-50 border-l-4 border-red-500 p-4 rounded-r">
                    <p class="text-red-700">Ошибка: ${r.error}</p>
                </div>
            </div>
        `;
    }
    
    const summary = r.summary || {};
    const content = r.content_analysis || {};
    const tech = r.technology_stack || [];
    const issues = r.all_issues || [];
    const critical = r.critical_issues || [];
    const warnings = r.warning_issues || [];
    const info = r.info_issues || [];
    const recs = r.recommendations || [];
    
    const score = summary.seo_score || 0;
    let scoreBadge = 'bg-green-100 text-green-800';
    if (score < 30) scoreBadge = 'bg-red-100 text-red-800';
    else if (score < 50) scoreBadge = 'bg-orange-100 text-orange-800';
    else if (score < 70) scoreBadge = 'bg-yellow-100 text-yellow-800';
    
    let techHTML = '';
    if (tech.length > 0) {
        techHTML = tech.map(t => `
            <span class="inline-flex items-center bg-purple-100 text-purple-700 rounded-full px-3 py-1 text-sm font-medium mr-2 mb-2">
                ${t.tech}
                <span class="ml-1 bg-purple-200 text-purple-800 rounded-full px-1.5 py-0.5 text-xs">${t.count}</span>
            </span>
        `).join('');
    } else {
        techHTML = '<span class="text-gray-500">Технологии не определены</span>';
    }
    
    let issuesHTML = '';
    if (critical.length > 0) {
        issuesHTML += `
            <div class="mb-4">
                <h4 class="font-semibold text-red-600 mb-2"><i class="fas fa-exclamation-triangle mr-2"></i>Критические проблемы (${critical.length}):</h4>
                <ul class="list-disc pl-5 text-sm text-red-700 space-y-1">
                    ${critical.slice(0, 10).map(i => `<li>${i.issue} <span class="text-gray-500 ml-2">- ${i.url.substring(0, 60)}...</span></li>`).join('')}
                </ul>
            </div>
        `;
    }
    if (warnings.length > 0) {
        issuesHTML += `
            <div class="mb-4">
                <h4 class="font-semibold text-yellow-600 mb-2"><i class="fas fa-exclamation-circle mr-2"></i>Предупреждения (${warnings.length}):</h4>
                <ul class="list-disc pl-5 text-sm text-yellow-700 space-y-1">
                    ${warnings.slice(0, 10).map(i => `<li>${i.issue} <span class="text-gray-500 ml-2">- ${i.url.substring(0, 60)}...</span></li>`).join('')}
                </ul>
            </div>
        `;
    }
    if (info.length > 0) {
        issuesHTML += `
            <div class="mb-4">
                <h4 class="font-semibold text-blue-600 mb-2"><i class="fas fa-info-circle mr-2"></i>Рекомендации (${info.length}):</h4>
                <ul class="list-disc pl-5 text-sm text-blue-700 space-y-1">
                    ${info.slice(0, 5).map(i => `<li>${i.issue}</li>`).join('')}
                </ul>
            </div>
        `;
    }
    if (issues.length === 0) {
        issuesHTML = '<p class="text-green-600"><i class="fas fa-check mr-2"></i>Проблем не обнаружено!</p>';
    }
    
    let recsHTML = '';
    if (recs.length > 0) {
        recsHTML = recs.map(rec => `
            <div class="flex items-start mb-2 p-3 rounded ${rec.priority === 'critical' ? 'bg-red-100' : rec.priority === 'high' ? 'bg-orange-50' : rec.priority === 'medium' ? 'bg-yellow-50' : 'bg-gray-50'}">
                <span class="inline-block w-3 h-3 rounded-full mr-3 mt-1 flex-shrink-0 ${
                    rec.priority === 'critical' ? 'bg-red-500' : 
                    rec.priority === 'high' ? 'bg-orange-500' : 
                    rec.priority === 'medium' ? 'bg-yellow-500' : 'bg-gray-500'
                }"></span>
                <span class="text-sm text-gray-700">${rec.text}</span>
            </div>
        `).join('');
    }
    
    // Pages breakdown
    const goodPages = summary.good_pages || 0;
    const avgPages = summary.average_pages || 0;
    const badPages = summary.bad_pages || 0;
    const totalPages = goodPages + avgPages + badPages || 1;
    
    const goodPercent = Math.round((goodPages / totalPages) * 100);
    const avgPercent = Math.round((avgPages / totalPages) * 100);
    const badPercent = Math.round((badPages / totalPages) * 100);
    
    const scoreGradeLabel = score >= 80 ? 'Отлично' : score >= 70 ? 'Хорошо' : score >= 50 ? 'Средне' : score >= 30 ? 'Низко' : 'Критично';
    const siteAnalyzeActionBtns = `<button onclick="copyCurrentTaskJson()" class="px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm transition"><i class="fas fa-copy mr-1"></i>Копировать JSON</button>`;

    return `
        <div class="space-y-6 auditpro-results">
            ${buildToolHeader({
                gradient: 'from-blue-700 via-sky-700 to-cyan-700',
                label: 'Site Analyze',
                title: 'Анализ сайта',
                subtitle: result.url,
                score: score,
                scoreLabel: 'SEO',
                scoreGrade: scoreGradeLabel,
                badges: [
                    { cls: 'bg-white/10 border border-white/20 text-white/90', text: `${summary.pages_crawled || 0} страниц` },
                    ...(critical.length > 0 ? [{ cls: 'bg-rose-500/20 border border-rose-400/40 text-rose-100', text: `${critical.length} критичных` }] : []),
                    ...(warnings.length > 0 ? [{ cls: 'bg-amber-400/20 border border-amber-300/40 text-amber-100', text: `${warnings.length} предупреждений` }] : []),
                ],
                metaLines: [
                    `Внутренних ссылок: ${summary.internal_links_count || 0}`,
                    `Битых ссылок: ${summary.broken_links_count || 0}`,
                    `Редиректов: ${summary.redirects_count || 0}`,
                ],
                actionButtons: siteAnalyzeActionBtns,
            })}

            <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                ${buildMetricCard('Страниц', summary.pages_crawled || 0)}
                ${buildMetricCard('Внутренних ссылок', summary.internal_links_count || 0)}
                ${buildMetricCard('Битых ссылок', summary.broken_links_count || 0)}
                ${buildMetricCard('Всего проблем', (summary.critical_issues || 0) + (summary.warning_issues || 0))}
            </div>

            <!-- Pages Quality -->
            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                <h4 class="font-semibold text-slate-800 mb-3">Качество страниц</h4>
                <div class="space-y-2">
                    <div class="flex items-center">
                        <div class="w-24 text-sm text-slate-600">Хорошие</div>
                        <div class="flex-1 bg-slate-200 rounded-full h-3 mx-2">
                            <div class="bg-emerald-500 h-3 rounded-full" style="width: ${goodPercent}%"></div>
                        </div>
                        <div class="w-16 text-right text-sm font-medium">${goodPercent}%</div>
                    </div>
                    <div class="flex items-center">
                        <div class="w-24 text-sm text-slate-600">Средние</div>
                        <div class="flex-1 bg-slate-200 rounded-full h-3 mx-2">
                            <div class="bg-amber-500 h-3 rounded-full" style="width: ${avgPercent}%"></div>
                        </div>
                        <div class="w-16 text-right text-sm font-medium">${avgPercent}%</div>
                    </div>
                    <div class="flex items-center">
                        <div class="w-24 text-sm text-slate-600">Проблемные</div>
                        <div class="flex-1 bg-slate-200 rounded-full h-3 mx-2">
                            <div class="bg-rose-500 h-3 rounded-full" style="width: ${badPercent}%"></div>
                        </div>
                        <div class="w-16 text-right text-sm font-medium">${badPercent}%</div>
                    </div>
                </div>
            </div>

            ${buildFindingsGrid(critical, warnings, info)}

            <!-- Content Analysis -->
            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                <h4 class="font-semibold text-slate-800 mb-3"><i class="fas fa-chart-bar mr-2 text-blue-500"></i>Анализ контента</h4>
                <div class="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
                    ${buildMetricCard('Изображений', content.total_images || 0, `без alt: ${content.images_without_alt || 0}`)}
                    ${buildMetricCard('Ссылок', content.total_links || 0)}
                    ${buildMetricCard('Title страниц', `${content.pages_with_title || 0}/${summary.pages_crawled || 0}`)}
                    ${buildMetricCard('H1 страниц', `${content.pages_with_h1 || 0}/${summary.pages_crawled || 0}`)}
                    ${buildMetricCard('Schema.org', `${content.pages_with_schema_org || 0}/${summary.pages_crawled || 0}`)}
                    ${buildMetricCard('Ср. слов', Math.round(content.average_word_count || 0))}
                </div>
            </div>

            <!-- Technology Stack -->
            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
                <h4 class="font-semibold text-slate-800 mb-3"><i class="fas fa-microchip mr-2 text-purple-500"></i>Технологии</h4>
                <div class="flex flex-wrap gap-2">
                    ${techHTML}
                </div>
            </div>

            ${buildRecommendations(recs)}
        </div>
    `;
}

function generateRenderAuditHTML(result) {
    const r = result.results || result;
    const summary = r.summary || {};
    const variants = Array.isArray(r.variants) ? r.variants : [];
    const issues = Array.isArray(r.issues) ? r.issues : [];
    const mobileVariant = variants.find(v => (v?.profile_type === 'mobile') || v?.mobile === true || v?.variant_id === 'googlebot_mobile');
    const desktopVariant = variants.find(v => (v?.profile_type === 'desktop') || v?.mobile === false || v?.variant_id === 'googlebot_desktop');
    const score = Number(summary.score ?? 0);
    const scorePct = Math.max(0, Math.min(100, Math.round(score)));

    const severityRank = { critical: 0, warning: 1, info: 2 };
    const severityLabel = (sev) => {
        const s = String(sev || 'info').toLowerCase();
        if (s === 'critical') return 'КРИТИЧНО';
        if (s === 'warning') return 'ПРЕДУПРЕЖДЕНИЕ';
        return 'ИНФО';
    };
    const severityBadge = (sev) => {
        const s = String(sev || 'info').toLowerCase();
        if (s === 'critical') return 'bg-rose-100 text-rose-700';
        if (s === 'warning') return 'bg-amber-100 text-amber-700';
        return 'bg-sky-100 text-sky-700';
    };
    const scoreBadgeClass = (value) => value >= 80 ? 'bg-emerald-100 text-emerald-700' : value >= 60 ? 'bg-amber-100 text-amber-700' : 'bg-rose-100 text-rose-700';

    const screenshotItems = [];
    variants.forEach(v => {
        const shots = v.screenshots || {};
        Object.entries(shots).forEach(([key, shot]) => {
            if (shot && shot.url) screenshotItems.push({ src: shot.url, caption: `${v.variant_label || v.variant_id} | ${key}` });
        });
    });
    mobileLightboxItems = screenshotItems;

    const issueCodeMap = new Map();
    issues.forEach((item) => {
        const code = String(item.code || 'unknown');
        const sev = String(item.severity || 'info').toLowerCase();
        if (!issueCodeMap.has(code)) issueCodeMap.set(code, { code, total: 0, critical: 0, warning: 0, info: 0 });
        const row = issueCodeMap.get(code);
        row.total += 1;
        row[sev] = (row[sev] || 0) + 1;
    });
    const issueCodeRows = Array.from(issueCodeMap.values())
        .sort((a, b) => (b.total - a.total))
        .slice(0, 10);

    const variantRows = variants.map((v) => {
        const m = v.metrics || {};
        const seoReq = v.seo_required || {};
        const shots = Object.keys(v.screenshots || {});
        return {
            label: v.variant_label || v.variant_id || '-',
            profile: formatProfileLabel(v.profile_type || (v.mobile ? 'mobile' : 'desktop')),
            score: Number(m.score ?? 0),
            missingTotal: Number(m.total_missing ?? 0),
            missingPct: Number(m.missing_pct ?? 0),
            renderMs: Number(v.timings?.rendered_s ?? 0) * 1000,
            seoFail: Number(seoReq.fail ?? 0),
            seoWarn: Number(seoReq.warn ?? 0),
            shots: shots.length,
        };
    }).sort((a, b) => (a.score - b.score) || (b.missingPct - a.missingPct));

    const renderGapRowsHtml = variantRows.map((row) => `
        <tr class="border-b border-slate-100 text-sm">
            <td class="py-2 pr-2">${escapeHtml(row.label)}</td>
            <td class="py-2 pr-2">${escapeHtml(row.profile)}</td>
            <td class="py-2 pr-2"><span class="px-2 py-0.5 rounded-full text-xs ${scoreBadgeClass(row.score)}">${escapeHtml(row.score.toFixed(1))}</span></td>
            <td class="py-2 pr-2">${escapeHtml(Math.round(row.missingTotal))}</td>
            <td class="py-2 pr-2">${escapeHtml(row.missingPct.toFixed(1))}%</td>
            <td class="py-2 pr-2">${escapeHtml(Math.round(row.renderMs))} ms</td>
            <td class="py-2 pr-2 text-rose-700 font-medium">${escapeHtml(row.seoFail)}</td>
            <td class="py-2 pr-2 text-amber-700 font-medium">${escapeHtml(row.seoWarn)}</td>
            <td class="py-2 pr-2">${escapeHtml(row.shots)}</td>
        </tr>
    `).join('');

    const normalizeText = (value) => String(value ?? '').trim();
    const metricTextCell = (value) => {
        const text = normalizeText(value);
        if (!text) return '<span class="text-slate-400">-</span>';
        const preview = text.length > 70 ? `${text.slice(0, 70)}...` : text;
        return `<div class="font-medium text-slate-800">${escapeHtml(preview)}</div><div class="text-[11px] text-slate-500">длина: ${escapeHtml(text.length)}</div>`;
    };
    const toCount = (value) => {
        const n = Number(value ?? 0);
        return Number.isFinite(n) ? Math.max(0, Math.round(n)) : 0;
    };
    const deltaBadge = (delta) => {
        if (delta > 0) return `<span class="inline-flex text-[11px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">+${escapeHtml(delta)}</span>`;
        if (delta < 0) return `<span class="inline-flex text-[11px] px-1.5 py-0.5 rounded bg-rose-100 text-rose-700">${escapeHtml(delta)}</span>`;
        return `<span class="inline-flex text-[11px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">0</span>`;
    };
    const lenDeltaCell = (rawValue, renderedValue) => {
        const rawText = normalizeText(rawValue);
        const renderedText = normalizeText(renderedValue);
        const delta = renderedText.length - rawText.length;
        return deltaBadge(delta);
    };
    const countDeltaCell = (rawValue, renderedValue) => {
        const delta = toCount(renderedValue) - toCount(rawValue);
        return deltaBadge(delta);
    };
    const impactBadge = (impactScore) => {
        if (impactScore >= 4) return '<span class="px-2 py-0.5 rounded-full text-xs bg-rose-100 text-rose-700">высокое</span>';
        if (impactScore >= 2) return '<span class="px-2 py-0.5 rounded-full text-xs bg-amber-100 text-amber-700">среднее</span>';
        if (impactScore >= 1) return '<span class="px-2 py-0.5 rounded-full text-xs bg-sky-100 text-sky-700">низкое</span>';
        return '<span class="px-2 py-0.5 rounded-full text-xs bg-emerald-100 text-emerald-700">нет</span>';
    };
    const contentDiffRowsData = variants.map((v) => {
        const raw = v.raw || {};
        const rendered = v.rendered || {};
        const titleChanged = normalizeText(raw.title) !== normalizeText(rendered.title);
        const descChanged = normalizeText(raw.meta_description) !== normalizeText(rendered.meta_description);
        const h1Changed = toCount(raw.h1_count) !== toCount(rendered.h1_count);
        const imagesChanged = toCount(raw.images_count) !== toCount(rendered.images_count);
        const linksChanged = toCount(raw.links_count) !== toCount(rendered.links_count);
        const impactScore = [titleChanged, descChanged, h1Changed, imagesChanged, linksChanged].filter(Boolean).length;
        const rowClass = impactScore >= 4 ? 'bg-rose-50/40' : impactScore >= 2 ? 'bg-amber-50/40' : '';
        return {
            variantLabel: v.variant_label || v.variant_id || '-',
            profile: formatProfileLabel(v.profile_type || (v.mobile ? 'mobile' : 'desktop')),
            raw,
            rendered,
            impactScore,
            rowClass,
            titleChanged,
            descChanged,
            h1Changed,
            imagesChanged,
            linksChanged,
        };
    }).sort((a, b) => b.impactScore - a.impactScore || String(a.variantLabel).localeCompare(String(b.variantLabel)));
    const diffSummary = contentDiffRowsData.reduce((acc, row) => {
        acc.titleChanged += row.titleChanged ? 1 : 0;
        acc.descChanged += row.descChanged ? 1 : 0;
        acc.h1Changed += row.h1Changed ? 1 : 0;
        acc.imagesChanged += row.imagesChanged ? 1 : 0;
        acc.linksChanged += row.linksChanged ? 1 : 0;
        acc.highImpact += row.impactScore >= 4 ? 1 : 0;
        return acc;
    }, { titleChanged: 0, descChanged: 0, h1Changed: 0, imagesChanged: 0, linksChanged: 0, highImpact: 0 });
    const totalDiffRows = contentDiffRowsData.length || 1;
    const summaryRatio = (value) => `${Math.round((value / totalDiffRows) * 100)}%`;
    function formatRenderFieldName(name) {
        const n = String(name || '').toLowerCase();
        if (n === 'title') return 'title';
        if (n === 'description') return 'description';
        if (n === 'h1') return 'h1';
        if (n === 'images') return 'изображения';
        if (n === 'links') return 'ссылки';
        return n || '';
    }
    const topDivergencesHtml = contentDiffRowsData
        .filter((row) => row.impactScore > 0)
        .slice(0, 3)
        .map((row) => {
            const changedFields = [];
            if (row.titleChanged) changedFields.push('title');
            if (row.descChanged) changedFields.push('description');
            if (row.h1Changed) changedFields.push('h1');
            if (row.imagesChanged) changedFields.push('images');
            if (row.linksChanged) changedFields.push('links');
            return `
                <div class="rounded-lg border border-slate-200 bg-slate-50 p-2">
                    <div class="flex items-center justify-between gap-2">
                        <div class="text-sm font-medium text-slate-800 truncate">${escapeHtml(row.variantLabel)}</div>
                        ${impactBadge(row.impactScore)}
                    </div>
                    <div class="text-xs text-slate-500 mt-1">${escapeHtml(row.profile)}</div>
                    <div class="text-xs text-slate-700 mt-1">Изменения: ${escapeHtml(changedFields.map(formatRenderFieldName).join(', ') || '-')}</div>
                </div>
            `;
        }).join('');
    const contentDiffRowsHtml = contentDiffRowsData.map((row) => {
        return `
            <tr class="border-b border-slate-100 text-sm align-top ${row.rowClass}">
                <td class="py-2 pr-2 font-medium">${escapeHtml(row.variantLabel)}</td>
                <td class="py-2 pr-2 text-slate-600">${escapeHtml(row.profile)}</td>
                <td class="py-2 pr-2">${impactBadge(row.impactScore)}</td>
                <td class="py-2 pr-2">${metricTextCell(row.raw.title)}</td>
                <td class="py-2 pr-2">${metricTextCell(row.rendered.title)}</td>
                <td class="py-2 pr-2">${lenDeltaCell(row.raw.title, row.rendered.title)}</td>
                <td class="py-2 pr-2">${metricTextCell(row.raw.meta_description)}</td>
                <td class="py-2 pr-2">${metricTextCell(row.rendered.meta_description)}</td>
                <td class="py-2 pr-2">${lenDeltaCell(row.raw.meta_description, row.rendered.meta_description)}</td>
                <td class="py-2 pr-2 font-medium">${escapeHtml(toCount(row.raw.h1_count))}</td>
                <td class="py-2 pr-2 font-medium">${escapeHtml(toCount(row.rendered.h1_count))}</td>
                <td class="py-2 pr-2">${countDeltaCell(row.raw.h1_count, row.rendered.h1_count)}</td>
                <td class="py-2 pr-2 font-medium">${escapeHtml(toCount(row.raw.images_count))}</td>
                <td class="py-2 pr-2 font-medium">${escapeHtml(toCount(row.rendered.images_count))}</td>
                <td class="py-2 pr-2">${countDeltaCell(row.raw.images_count, row.rendered.images_count)}</td>
                <td class="py-2 pr-2 font-medium">${escapeHtml(toCount(row.raw.links_count))}</td>
                <td class="py-2 pr-2 font-medium">${escapeHtml(toCount(row.rendered.links_count))}</td>
                <td class="py-2 pr-2">${countDeltaCell(row.raw.links_count, row.rendered.links_count)}</td>
            </tr>
        `;
    }).join('');

    const recommendations = [];
    const recSeen = new Set();
    (r.recommendations || []).forEach((rec) => {
        const text = String(rec || '').trim();
        const key = text.toLowerCase();
        if (text && !recSeen.has(key)) {
            recSeen.add(key);
            recommendations.push({ text, source: 'сводка' });
        }
    });
    issues
        .slice()
        .sort((a, b) => (severityRank[String(a.severity || 'info').toLowerCase()] ?? 3) - (severityRank[String(b.severity || 'info').toLowerCase()] ?? 3))
        .slice(0, 8)
        .forEach((issue) => {
            const action = `${issue.title || issue.code || 'Проблема'}${issue.variant ? ` (${issue.variant})` : ''}`;
            const key = action.toLowerCase();
            if (!recSeen.has(key)) {
                recSeen.add(key);
                recommendations.push({ text: action, source: 'проблема' });
            }
        });
    const recommendationCardsHtml = recommendations.slice(0, 10).map((row) => `
        <div class="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div class="text-xs text-slate-500 uppercase tracking-wide">${escapeHtml(row.source)}</div>
            <div class="text-sm font-medium text-slate-900 mt-1">${escapeHtml(row.text)}</div>
        </div>
    `).join('');

    const topIssuesHtml = issues
        .slice()
        .sort((a, b) => {
            const sa = String(a.severity || 'info').toLowerCase();
            const sb = String(b.severity || 'info').toLowerCase();
            const bySev = (severityRank[sa] ?? 3) - (severityRank[sb] ?? 3);
            if (bySev !== 0) return bySev;
            return String(a.code || '').localeCompare(String(b.code || ''));
        })
        .slice(0, 20)
        .map((i) => `
            <div class="py-2 border-b border-slate-100 text-sm">
                <div class="flex flex-wrap items-center gap-2 mb-1">
                    <span class="px-2 py-0.5 rounded-full text-xs ${severityBadge(i.severity)}">${severityLabel(i.severity)}</span>
                    <span class="font-medium text-slate-800">${escapeHtml(i.code || 'issue')}</span>
                    <span class="text-xs text-slate-500">${escapeHtml(i.variant || 'профиль')}</span>
                </div>
                <div class="text-slate-800">${escapeHtml(i.title || '')}</div>
                <div class="text-slate-600">${escapeHtml(i.details || '')}</div>
                ${(i.examples || []).length ? `<ul class="list-disc pl-5 text-xs text-slate-600 mt-1">${(i.examples || []).slice(0, 3).map(ex => `<li class="break-all">${escapeHtml(ex)}</li>`).join('')}</ul>` : ''}
            </div>
        `).join('');

    const renderProfileCard = (v, accent = 'blue') => {
        if (!v) {
            return `
                <div class="bg-white rounded-2xl shadow-lg border border-rose-200 p-6">
                    <h4 class="font-semibold text-rose-700 mb-2">Профиль недоступен</h4>
                    <div class="text-sm text-rose-700">В результате render отсутствуют данные по вариантам.</div>
                </div>
            `;
        }
        const metrics = v.metrics || {};
        const seoReq = v.seo_required || {};
        const scoreValue = Number(metrics.score ?? 0);
        const miss = Math.round(Number(metrics.total_missing ?? 0));
        const missPctVal = Number(metrics.missing_pct ?? 0).toFixed(1);
        const renderedMs = Math.round(Number(v.timings?.rendered_s ?? 0) * 1000);
        const emulation = v.emulation || {};
        const shots = v.screenshots || {};
        const shotList = Object.entries(shots).filter(([, shot]) => shot && shot.url);
        const firstShot = shotList[0]?.[1]?.url || '';
        const shotIndex = firstShot ? screenshotItems.findIndex(i => i.src === firstShot) : -1;
        const accentBox = accent === 'blue' ? 'from-sky-50 to-blue-100 border-blue-200' : 'from-slate-50 to-gray-100 border-gray-200';
        const profilePill = String(v.profile_type || (v.mobile ? 'mobile' : 'desktop')).toLowerCase() === 'mobile'
            ? 'bg-blue-100 text-blue-700'
            : 'bg-slate-200 text-slate-700';
        const variantIssues = Array.isArray(v.issues) ? v.issues : [];
        const issuePreview = variantIssues.slice(0, 4).map((it) => `
            <li><span class="font-medium ${String(it.severity || '').toLowerCase() === 'critical' ? 'text-rose-700' : 'text-amber-700'}">${severityLabel(it.severity)}</span> - ${escapeHtml(it.title || it.code || 'Проблема')}</li>
        `).join('');
        return `
            <div class="bg-gradient-to-br ${accentBox} rounded-2xl shadow-lg border p-6">
                <div class="flex items-start justify-between mb-3">
                    <div>
                        <h4 class="text-lg font-semibold text-slate-800">${escapeHtml(v.variant_label || v.variant_id || 'Вариант')}</h4>
                        <div class="mt-1 inline-flex text-[11px] px-2 py-1 rounded-full ${profilePill}">${escapeHtml(formatProfileLabel(v.profile_type || (v.mobile ? 'mobile' : 'desktop')))}</div>
                    </div>
                    <span class="text-xs px-2 py-1 rounded-full ${scoreBadgeClass(scoreValue)}">Оценка ${escapeHtml(scoreValue.toFixed(1))}</span>
                </div>
                <div class="grid grid-cols-2 gap-2 text-sm mb-3">
                    <div class="bg-white/70 rounded-lg p-2"><span class="text-slate-600">Потери:</span> <span class="font-medium">${escapeHtml(miss)}</span></div>
                    <div class="bg-white/70 rounded-lg p-2"><span class="text-slate-600">Потери %:</span> <span class="font-medium">${escapeHtml(missPctVal)}%</span></div>
                    <div class="bg-white/70 rounded-lg p-2"><span class="text-slate-600">JS-рендер:</span> <span class="font-medium">${escapeHtml(renderedMs)} мс</span></div>
                    <div class="bg-white/70 rounded-lg p-2"><span class="text-slate-600">SEO fail/warn:</span> <span class="font-medium">${escapeHtml(seoReq.fail || 0)}/${escapeHtml(seoReq.warn || 0)}</span></div>
                    <div class="bg-white/70 rounded-lg p-2"><span class="text-slate-600">Скриншоты:</span> <span class="font-medium">${escapeHtml(shotList.length)}</span></div>
                    <div class="bg-white/70 rounded-lg p-2"><span class="text-slate-600">Viewport:</span> <span class="font-medium">${escapeHtml(emulation.viewport ? `${emulation.viewport.width}x${emulation.viewport.height}` : '-')}</span></div>
                </div>
                ${firstShot ? `
                    <div class="rounded-xl overflow-hidden border border-white/70 shadow cursor-zoom-in mb-3" onclick="openMobileLightbox(${Math.max(0, shotIndex)})">
                        <img src="${escapeHtml(firstShot)}" alt="${escapeHtml(v.variant_label || v.variant_id || 'Вариант')}" class="w-full h-44 object-cover">
                    </div>
                ` : '<div class="text-sm text-slate-500 mb-3">Скриншоты недоступны.</div>'}
                ${issuePreview ? `
                    <div class="text-sm">
                        <div class="font-medium mb-1">Топ проблем варианта</div>
                        <ul class="list-disc pl-5 text-slate-700">${issuePreview}</ul>
                    </div>
                ` : '<div class="text-sm text-emerald-700">В этом варианте проблем нет.</div>'}
            </div>
        `;
    };

    const issueCodeRowsHtml = issueCodeRows.map((row) => `
        <tr class="border-b border-slate-100 text-sm">
            <td class="py-2 pr-2">${escapeHtml(row.code)}</td>
            <td class="py-2 pr-2 font-medium">${escapeHtml(row.total)}</td>
            <td class="py-2 pr-2 text-rose-700">${escapeHtml(row.critical)}</td>
            <td class="py-2 pr-2 text-amber-700">${escapeHtml(row.warning)}</td>
            <td class="py-2 pr-2 text-sky-700">${escapeHtml(row.info)}</td>
        </tr>
`).join('');

    const diagnosticsHtml = variants.length
        ? `<ul class="list-disc pl-5 text-xs text-slate-700">${variants.map(v => {
            const dShots = Object.keys(v.screenshots || {});
            const pType = formatProfileLabel(v.profile_type || (v.mobile ? 'mobile' : 'desktop'));
            return `<li>${escapeHtml(v.variant_id || '-')} | ${escapeHtml(v.variant_label || '-')} | профиль=${escapeHtml(pType)} | проблем=${escapeHtml((v.issues || []).length)} | скриншотов=${escapeHtml(dShots.join(',') || '-')}</li>`;
        }).join('')}</ul>`
        : '<div class="text-xs text-slate-500">В данных нет диагностики по вариантам.</div>';

    const renderActionBtns = `
        <button onclick="downloadRenderDocxReport()" class="ds-export-btn"><i class="fas fa-file-word mr-1"></i>DOCX</button>
        <button onclick="downloadRenderXlsxReport()" class="ds-export-btn"><i class="fas fa-file-excel mr-1"></i>XLSX</button>
        <button onclick="copyCurrentTaskJson()" class="ds-export-btn"><i class="fas fa-copy mr-1"></i>JSON</button>`;

    return `
        <div class="space-y-6 renderpro-results">
            ${buildToolHeader({
                gradient: 'from-orange-700 via-amber-700 to-yellow-700',
                label: 'Render Audit v2',
                title: 'Render-аудит',
                subtitle: result.url || '',
                score: score || null,
                scoreLabel: 'рендер',
                badges: [
                    { cls: 'bg-white/10 border border-white/20 text-white/90', text: `${summary.variants_total || variants.length} вариантов` },
                    ...(summary.critical_issues > 0 ? [{ cls: 'bg-rose-500/20 border border-rose-400/40 text-rose-100', text: `${summary.critical_issues} критичных` }] : []),
                    ...(summary.warning_issues > 0 ? [{ cls: 'bg-amber-400/20 border border-amber-300/40 text-amber-100', text: `${summary.warning_issues} предупреждений` }] : []),
                ],
                metaLines: [
                    `Потери: ${summary.missing_total || 0} (${summary.avg_missing_pct || 0}%)`,
                    `JS-рендер: ${Math.round(Number(summary.avg_js_load_ms || 0))} мс`,
                    `Движок: ${formatEngineLabel(r.engine || 'legacy')}`,
                ],
                actionButtons: renderActionBtns,
            })}

            <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                ${buildMetricCard('Варианты', summary.variants_total || variants.length || 0)}
                ${buildMetricCard('Потери контента', summary.missing_total || 0, `${summary.avg_missing_pct || 0}% в среднем`)}
                ${buildMetricCard('JS-рендер', `${Math.round(Number(summary.avg_js_load_ms || 0))} мс`)}
                ${buildMetricCard('Критичных', summary.critical_issues || 0)}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Общий балл</h4>
                    <div style="height:200px;"><canvas id="ds-chart-render-raw"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">По вариантам</h4>
                    <div style="height:200px;"><canvas id="ds-chart-render-rendered"></canvas></div>
                </div>
            </div>

            ${buildFindingsGrid(
                issues.filter(i => String(i.severity||'').toLowerCase() === 'critical'),
                issues.filter(i => String(i.severity||'').toLowerCase() === 'warning'),
                issues.filter(i => String(i.severity||'').toLowerCase() === 'info')
            )}

            <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                <h4 class="font-semibold mb-3">Матрица расхождений рендера (JS vs no-JS)</h4>
                <table class="w-full min-w-[940px]">
                    <thead>
                        <tr class="text-left text-xs text-slate-500 border-b">
                            <th class="py-2 pr-2">Вариант</th>
                            <th class="py-2 pr-2">Профиль</th>
                                <th class="py-2 pr-2">Оценка</th>
                            <th class="py-2 pr-2">Потери</th>
                            <th class="py-2 pr-2">Потери %</th>
                            <th class="py-2 pr-2">JS-рендер</th>
                            <th class="py-2 pr-2">SEO fail</th>
                            <th class="py-2 pr-2">SEO warn</th>
                            <th class="py-2 pr-2">Скриншоты</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${renderGapRowsHtml || '<tr><td colspan="9" class="py-3 text-sm text-slate-500">В данных нет строк вариантов.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                <h4 class="font-semibold mb-3">Сравнение контента JS и no-JS</h4>
                <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2 mb-4">
                    <div class="rounded-lg border bg-slate-50 p-2">
                        <div class="text-[11px] uppercase tracking-wide text-slate-500">Title изменен</div>
                        <div class="text-base font-semibold text-slate-900">${escapeHtml(diffSummary.titleChanged)}</div>
                        <div class="text-[11px] text-slate-500">${escapeHtml(summaryRatio(diffSummary.titleChanged))} вариантов</div>
                    </div>
                    <div class="rounded-lg border bg-slate-50 p-2">
                        <div class="text-[11px] uppercase tracking-wide text-slate-500">Description изменен</div>
                        <div class="text-base font-semibold text-slate-900">${escapeHtml(diffSummary.descChanged)}</div>
                        <div class="text-[11px] text-slate-500">${escapeHtml(summaryRatio(diffSummary.descChanged))} вариантов</div>
                    </div>
                    <div class="rounded-lg border bg-slate-50 p-2">
                        <div class="text-[11px] uppercase tracking-wide text-slate-500">H1 изменен</div>
                        <div class="text-base font-semibold text-slate-900">${escapeHtml(diffSummary.h1Changed)}</div>
                        <div class="text-[11px] text-slate-500">${escapeHtml(summaryRatio(diffSummary.h1Changed))} вариантов</div>
                    </div>
                    <div class="rounded-lg border bg-slate-50 p-2">
                        <div class="text-[11px] uppercase tracking-wide text-slate-500">Изображения изменены</div>
                        <div class="text-base font-semibold text-slate-900">${escapeHtml(diffSummary.imagesChanged)}</div>
                        <div class="text-[11px] text-slate-500">${escapeHtml(summaryRatio(diffSummary.imagesChanged))} вариантов</div>
                    </div>
                    <div class="rounded-lg border bg-slate-50 p-2">
                        <div class="text-[11px] uppercase tracking-wide text-slate-500">Ссылки изменены</div>
                        <div class="text-base font-semibold text-slate-900">${escapeHtml(diffSummary.linksChanged)}</div>
                        <div class="text-[11px] text-slate-500">${escapeHtml(summaryRatio(diffSummary.linksChanged))} вариантов</div>
                    </div>
                    <div class="rounded-lg border bg-rose-50 border-rose-200 p-2">
                        <div class="text-[11px] uppercase tracking-wide text-rose-600">Высокий импакт</div>
                        <div class="text-base font-semibold text-rose-800">${escapeHtml(diffSummary.highImpact)}</div>
                        <div class="text-[11px] text-rose-600">>=4 измененных метрик</div>
                    </div>
                </div>
                <div class="mb-4">
                    <div class="flex items-center justify-between mb-2">
                        <div class="text-xs font-semibold uppercase tracking-wide text-slate-500">Топ вариантов с расхождениями</div>
                        <div class="text-[11px] text-slate-500">Изменение: положительное значение значит JS добавляет контент, отрицательное - теряет</div>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
                        ${topDivergencesHtml || '<div class="text-sm text-slate-500">Нет значимых расхождений JS и no-JS.</div>'}
                    </div>
                </div>
                <table class="w-full min-w-[1900px]">
                    <thead>
                        <tr class="text-left text-xs text-slate-500 border-b">
                            <th class="py-2 pr-2">Вариант</th>
                            <th class="py-2 pr-2">Профиль</th>
                            <th class="py-2 pr-2">Импакт</th>
                            <th class="py-2 pr-2">Title (no-JS)</th>
                            <th class="py-2 pr-2">Title (JS)</th>
                            <th class="py-2 pr-2">Дельта</th>
                            <th class="py-2 pr-2">Description (no-JS)</th>
                            <th class="py-2 pr-2">Description (JS)</th>
                            <th class="py-2 pr-2">Дельта</th>
                            <th class="py-2 pr-2">H1 (no-JS)</th>
                            <th class="py-2 pr-2">H1 (JS)</th>
                            <th class="py-2 pr-2">Дельта</th>
                            <th class="py-2 pr-2">Изобр. (no-JS)</th>
                            <th class="py-2 pr-2">Изобр. (JS)</th>
                            <th class="py-2 pr-2">Дельта</th>
                            <th class="py-2 pr-2">Ссылки (no-JS)</th>
                            <th class="py-2 pr-2">Ссылки (JS)</th>
                            <th class="py-2 pr-2">Дельта</th>
                        </tr>
                    </thead>
                    <tbody>
                            ${contentDiffRowsHtml || '<tr><td colspan="18" class="py-3 text-sm text-slate-500">В данных нет статистики по raw/rendered контенту.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
                ${renderProfileCard(desktopVariant, 'gray')}
                ${renderProfileCard(mobileVariant, 'blue')}
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                    <h4 class="font-semibold mb-3">Обзор типов проблем</h4>
                    <table class="w-full min-w-[520px]">
                        <thead>
                            <tr class="text-left text-xs text-slate-500 border-b">
                                <th class="py-2 pr-2">Код</th>
                                <th class="py-2 pr-2">Всего</th>
                                <th class="py-2 pr-2">Критично</th>
                                <th class="py-2 pr-2">Предупреждение</th>
                                <th class="py-2 pr-2">Инфо</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${issueCodeRowsHtml || '<tr><td colspan="5" class="py-3 text-sm text-slate-500">Нет статистики по кодам проблем.</td></tr>'}
                        </tbody>
                    </table>
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Практические рекомендации</h4>
                    <div class="grid grid-cols-1 gap-3">
                        ${recommendationCardsHtml || '<div class="text-sm text-slate-500">Рекомендации недоступны.</div>'}
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <h4 class="font-semibold mb-2">Приоритетные проблемы</h4>
                ${topIssuesHtml || '<div class="text-sm text-slate-500">Проблемы не найдены.</div>'}
            </div>

            <details class="bg-white rounded-xl shadow-md p-4 border border-gray-100">
                            <summary class="cursor-pointer text-sm font-medium">Диагностика вариантов (отладка)</summary>
                <div class="mt-3">${diagnosticsHtml}</div>
            </details>
        </div>
    `;
}
function generateMobileCheckHTML(result) {
    const r = result.results || result;
    const summary = r.summary || {};
    const devices = r.device_results || [];
    const issues = r.issues || [];

    mobileLightboxItems = devices
        .filter(d => d.screenshot_url)
        .map(d => ({ src: d.screenshot_url, caption: `${d.device_name} (${d.category || 'устройство'})` }));

    const deviceCards = devices.map((d, idx) => `
        <div class="bg-white rounded-xl shadow-md p-4 border border-gray-100">
            <div class="flex justify-between items-center mb-2">
                <h4 class="font-semibold text-gray-800">${d.device_name}</h4>
                <span class="text-xs px-2 py-1 rounded-full ${d.mobile_friendly ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'}">${d.mobile_friendly ? '' : 'Проблемы'}</span>
            </div>
            <div class="text-sm text-gray-600 mb-2">${d.viewport?.width || '-'}x${d.viewport?.height || '-'} | ${d.load_time_ms || 0} мс | проблем: ${d.issues_count || 0}</div>
            ${d.screenshot_url ? `
                <div class="rounded-lg overflow-hidden border cursor-zoom-in" onclick="openMobileLightbox(${idx})">
                    <img src="${d.screenshot_url}" alt="${d.device_name}" class="w-full h-44 object-cover">
                </div>
            ` : `<div class="text-sm text-gray-500">Скриншот отсутствует</div>`}
            ${(d.issues || []).length ? `
                <div class="mt-3 text-sm">
                    <div class="font-medium mb-1">Ключевые проблемы:</div>
                    <ul class="list-disc pl-5 text-red-600">
                        ${(d.issues || []).slice(0, 3).map(i => `<li>${i.title}</li>`).join('')}
                    </ul>
                </div>
            ` : ''}
        </div>
    `).join('');

    const topIssuesHtml = (issues || []).slice(0, 20).map(i => `
            <div class="py-2 border-b text-sm">
            <div class="font-medium ${i.severity === 'critical' ? 'text-red-700' : (i.severity === 'warning' ? 'text-amber-700' : 'text-blue-700')}">
                ${(i.severity || 'info').toUpperCase()} | ${i.device || 'Устройство'}
            </div>
            <div class="text-gray-700">${i.title || ''}</div>
            <div class="text-gray-500">${(i.details || '').replace(/\n/g, '<br>')}</div>
        </div>
    `).join('');

    const recommendationsHtml = (r.recommendations || []).map(rec => `<li>${rec}</li>`).join('');

    const critIssues = (issues || []).filter(i => i.severity === 'critical');
    const warnIssues = (issues || []).filter(i => i.severity === 'warning');
    const infoIssues = (issues || []).filter(i => i.severity !== 'critical' && i.severity !== 'warning');
    const mobileBadgeCls = r.mobile_friendly
        ? 'bg-emerald-500/20 border border-emerald-400/40 text-emerald-100'
        : 'bg-rose-500/20 border border-rose-400/40 text-rose-100';
    const mobileActionBtns = `
        <button onclick="downloadMobileDocxReport()" class="ds-export-btn"><i class="fas fa-file-word mr-1"></i>DOCX</button>
        <button onclick="downloadMobileXlsxReport()" class="ds-export-btn"><i class="fas fa-file-excel mr-1"></i>XLSX</button>
        <button onclick="copyCurrentTaskJson()" class="ds-export-btn"><i class="fas fa-copy mr-1"></i>JSON</button>`;

    return `
        <div class="space-y-6 auditpro-results">
            ${buildToolHeader({
                gradient: 'from-emerald-700 via-teal-700 to-cyan-700',
                label: 'Mobile Audit v2',
                title: 'Проверка мобильной версии',
                subtitle: result.url,
                score: r.score ?? null,
                scoreLabel: 'score',
                badges: [
                    { cls: mobileBadgeCls, text: r.mobile_friendly ? 'Mobile-Friendly' : 'Проблемы' },
                    { cls: 'bg-white/10 border border-white/20 text-white/90', text: `${summary.total_devices || devices.length} устройств` },
                    ...(summary.critical_issues > 0 ? [{ cls: 'bg-rose-500/20 border border-rose-400/40 text-rose-100', text: `${summary.critical_issues} критичных` }] : []),
                ],
                metaLines: [
                    `Движок: ${formatEngineLabel(r.engine || 'legacy')}`,
                    `Ср. загрузка: ${summary.avg_load_time_ms || 0} мс`,
                    `Предупреждений: ${summary.warning_issues || 0}`,
                ],
                actionButtons: mobileActionBtns,
            })}

            <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                ${buildMetricCard('Устройств', summary.total_devices || devices.length)}
                ${buildMetricCard('Критичных', summary.critical_issues || 0)}
                ${buildMetricCard('Предупреждений', summary.warning_issues || 0)}
                ${buildMetricCard('Ср. загрузка', `${summary.avg_load_time_ms || 0} мс`)}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Время загрузки</h4>
                    <div style="height:200px;"><canvas id="ds-chart-mobile-radar"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Mobile-Friendly</h4>
                    <div style="height:200px;"><canvas id="ds-chart-mobile-compat"></canvas></div>
                </div>
            </div>

            ${buildFindingsGrid(critIssues, warnIssues, infoIssues)}

            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                ${deviceCards || '<div class="text-sm text-gray-500">Нет результатов по устройствам.</div>'}
            </div>

            ${buildRecommendations(r.recommendations || [])}
        </div>
    `;
}

function generateOnPageAuditHTML(result) {
    const r = result.results || {};
    const summary = r.summary || {};
    const content = r.content || {};
    const keywords = r.keywords || [];
    const issues = r.issues || [];
    const topTerms = r.top_terms || [];
    const technical = r.technical || {};
    const schema = r.schema || {};
    const opengraph = r.opengraph || {};
    const links = r.links || {};
    const media = r.media || {};
    const readability = r.readability || {};
    const ai = r.ai_insights || {};
    const ngrams = r.ngrams || {};
    const contentProfile = r.content_profile || {};
    const params = r.parameter_values || [];
    const linkAnchorTerms = r.link_anchor_terms || [];
    const heatmap = r.heatmap || {};
    const priorityQueue = r.priority_queue || [];
    const targets = r.targets || [];
    const recs = r.recommendations || [];
    const titleMeta = r.title || {};
    const descMeta = r.description || {};
    const h1Meta = r.h1 || {};
    const keywordCoverage = r.keyword_coverage || {};
    const scores = r.scores || {};

    const keywordsRows = keywords.slice(0, 30).map(k => `
        <tr class="border-b border-gray-100 text-sm">
            <td class="py-2 pr-2 font-medium">${k.keyword || ''}</td>
            <td class="py-2 pr-2">${k.occurrences ?? 0}</td>
            <td class="py-2 pr-2">${k.density_pct ?? 0}%</td>
            <td class="py-2 pr-2">${k.in_title ? 'Да' : 'Нет'}</td>
            <td class="py-2 pr-2">${k.in_description ? 'Да' : 'Нет'}</td>
            <td class="py-2 pr-2">${k.in_h1 ? 'Да' : 'Нет'}</td>
            <td class="py-2 pr-2">
                <span class="px-2 py-1 rounded-full text-xs ${k.status === 'critical' ? 'bg-red-100 text-red-700' : (k.status === 'warning' ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700')}">
                    ${(k.status || 'ok').toUpperCase()}
                </span>
            </td>
        </tr>
    `).join('');

    const issuesHtml = issues.slice(0, 25).map(i => `
        <div data-issue-severity="${(i.severity || 'info').toLowerCase()}" class="text-sm border-l-4 ${i.severity === 'critical' ? 'border-red-500 bg-red-50' : i.severity === 'warning' ? 'border-yellow-500 bg-yellow-50' : 'border-blue-500 bg-blue-50'} p-3 rounded-r mb-2">
            <div class="font-semibold">${(i.severity || 'info').toUpperCase()} | ${i.code || 'issue'}</div>
            <div>${i.title || ''}</div>
            <div class="text-gray-600">${i.details || ''}</div>
        </div>
    `).join('');

    const termsHtml = topTerms.slice(0, 15).map(t => `
        <div class="flex items-center justify-between text-sm py-1 border-b border-gray-100">
            <span>${t.term || ''}</span>
            <span class="font-medium">${t.count ?? 0} (${t.pct ?? 0}%)</span>
        </div>
    `).join('');
    const bigramsHtml = (ngrams.bigrams || []).slice(0, 10).map(t => `
        <div class="flex items-center justify-between text-sm py-1 border-b border-gray-100">
            <span>${t.term || ''}</span>
            <span class="font-medium">${t.count ?? 0} (${t.pct ?? 0}%)</span>
        </div>
    `).join('');
    const trigramsHtml = (ngrams.trigrams || []).slice(0, 10).map(t => `
        <div class="flex items-center justify-between text-sm py-1 border-b border-gray-100">
            <span>${t.term || ''}</span>
            <span class="font-medium">${t.count ?? 0} (${t.pct ?? 0}%)</span>
        </div>
    `).join('');
    const spam = r.spam_metrics || {};

    const recsHtml = recs.map(x => `<li>${x}</li>`).join('');
    const linkTermsHtml = linkAnchorTerms.slice(0, 10).map(t => `
        <div class="flex items-center justify-between text-sm py-1 border-b border-gray-100">
            <span>${t.term || ''}</span>
            <span class="font-medium">${t.count ?? 0}</span>
        </div>
    `).join('');
    const paramRows = params.map(p => `
        <tr class="border-b border-gray-100 text-sm">
            <td class="py-2 pr-2 font-medium">${p.parameter || ''}</td>
            <td class="py-2 pr-2">${p.value ?? ''}</td>
            <td class="py-2 pr-2">
                <span class="px-2 py-1 rounded-full text-xs ${p.status === 'good' ? 'bg-emerald-100 text-emerald-700' : (p.status === 'acceptable' ? 'bg-amber-100 text-amber-700' : (p.status === 'bad' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-700'))}">
                    ${(p.status || 'info').toUpperCase()}
                </span>
            </td>
        </tr>
    `).join('');
    const heatmapCards = Object.entries(heatmap).map(([k, v]) => `
        <div class="rounded-xl border p-3 ${v.score >= 80 ? 'bg-emerald-50 border-emerald-200' : (v.score >= 60 ? 'bg-amber-50 border-amber-200' : 'bg-rose-50 border-rose-200')}">
            <div class="text-xs uppercase tracking-wide text-gray-500">${k}</div>
            <div class="text-2xl font-semibold">${v.score ?? 0}</div>
            <div class="text-xs text-gray-600">Проблемы: ${v.issues ?? 0}, К:${v.critical ?? 0}, П:${v.warning ?? 0}</div>
        </div>
    `).join('');
    const queueRows = priorityQueue.slice(0, 12).map(q => `
        <tr class="border-b border-gray-100 text-sm">
            <td class="py-2 pr-2">${q.bucket || '-'}</td>
            <td class="py-2 pr-2">${(q.severity || '').toUpperCase()}</td>
            <td class="py-2 pr-2">${q.code || ''}</td>
            <td class="py-2 pr-2">${q.title || ''}</td>
            <td class="py-2 pr-2">${q.priority_score ?? 0}</td>
            <td class="py-2 pr-2">${q.effort ?? 0}</td>
        </tr>
    `).join('');
    const targetRows = targets.map(t => `
        <tr class="border-b border-gray-100 text-sm">
            <td class="py-2 pr-2 font-medium">${t.metric || ''}</td>
            <td class="py-2 pr-2">${t.current ?? 0}</td>
            <td class="py-2 pr-2">${t.target ?? 0}</td>
            <td class="py-2 pr-2 ${Number(t.delta || 0) > 0 ? 'text-red-700' : 'text-emerald-700'}">${t.delta ?? 0}</td>
        </tr>
    `).join('');
    const clampPct = (v) => Math.max(0, Math.min(100, Number(v || 0)));
    const scoreBar = (label, value, tone = 'blue') => {
        const pct = clampPct(value);
        const toneMap = {
            blue: 'bg-blue-500',
            emerald: 'bg-emerald-500',
            amber: 'bg-amber-500',
            rose: 'bg-rose-500',
            indigo: 'bg-indigo-500',
        };
        return `
            <div class="rounded-xl border border-slate-200 p-3 bg-slate-50">
                <div class="flex items-center justify-between text-xs text-slate-600 mb-1">
                    <span>${label}</span>
                    <span class="font-semibold">${pct.toFixed(1)}</span>
                </div>
                <div class="h-2 rounded-full bg-slate-200 overflow-hidden">
                    <div class="h-2 ${toneMap[tone] || toneMap.blue}" style="width:${pct}%"></div>
                </div>
            </div>
        `;
    };
    const scoreBars = [
        scoreBar('Общая оценка', r.score ?? summary.score ?? 0, 'blue'),
        scoreBar('Spam-оценка', scores.spam_score ?? summary.spam_score ?? 0, 'emerald'),
        scoreBar('Покрытие ключей', keywordCoverage.coverage_pct ?? summary.keyword_coverage_pct ?? 0, 'indigo'),
        scoreBar('AI-риск', ai.ai_risk_composite ?? summary.ai_risk_composite ?? 0, 'rose'),
    ].join('');
    const quickActions = priorityQueue.slice(0, 6).map((q, idx) => `
        <div class="rounded-xl border border-slate-200 p-3 ${q.bucket === 'Now' ? 'bg-rose-50' : (q.bucket === 'Next' ? 'bg-amber-50' : (q.bucket === 'Сейчас' ? 'bg-rose-50' : (q.bucket === 'Далее' ? 'bg-amber-50' : 'bg-emerald-50')))}">
            <div class="flex items-center justify-between mb-1">
                <span class="text-xs font-semibold uppercase tracking-wide text-slate-600">#${idx + 1} ${(q.bucket === 'Now' ? 'Сейчас' : (q.bucket === 'Next' ? 'Далее' : (q.bucket || 'Позже')))}</span>
                <span class="text-xs text-slate-500">P ${q.priority_score ?? 0}</span>
            </div>
            <div class="text-sm font-medium text-slate-800">${q.title || q.code || 'Проблема'}</div>
            <div class="text-xs text-slate-600 mt-1">${(q.severity || '').toUpperCase()} | Трудозатраты ${q.effort ?? 0}</div>
        </div>
    `).join('');

    const onpageActionBtns = `
        <button onclick="downloadOnpageDocxReport()" class="ds-export-btn"><i class="fas fa-file-word mr-1"></i>DOCX</button>
        <button onclick="downloadOnpageXlsxReport()" class="ds-export-btn"><i class="fas fa-file-excel mr-1"></i>XLSX</button>
        <button onclick="copyOnpageTopFixes()" class="ds-export-btn"><i class="fas fa-bolt mr-1"></i>Топ исправлений</button>
        <button onclick="copyCurrentTaskJson()" class="ds-export-btn"><i class="fas fa-copy mr-1"></i>JSON</button>`;
    const onpageScore = r.score ?? summary.score ?? 0;

    return `
        <div class="space-y-6 auditpro-results">
            ${buildToolHeader({
                gradient: 'from-rose-700 via-pink-700 to-purple-700',
                label: 'OnPage Audit v1',
                title: 'OnPage-аудит',
                subtitle: result.url || '',
                score: onpageScore,
                scoreLabel: 'SEO',
                badges: [
                    ...(summary.critical_issues > 0 ? [{ cls: 'bg-rose-500/20 border border-rose-400/40 text-rose-100', text: `${summary.critical_issues} критичных` }] : []),
                    ...(summary.warning_issues > 0 ? [{ cls: 'bg-amber-400/20 border border-amber-300/40 text-amber-100', text: `${summary.warning_issues} предупреждений` }] : []),
                ],
                metaLines: [
                    `Слов: ${content.word_count ?? 0}`,
                    `Покрытие КС: ${keywordCoverage.coverage_pct ?? 0}%`,
                    `AI-риск: ${ai.ai_risk_composite ?? 0}`,
                ],
                actionButtons: onpageActionBtns,
            })}

            <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
                ${buildMetricCard('Оценка', onpageScore)}
                ${buildMetricCard('Слова', content.word_count ?? 0)}
                ${buildMetricCard('Spam-оценка', scores.spam_score ?? 0)}
                ${buildMetricCard('Покрытие КС', `${keywordCoverage.coverage_pct ?? 0}%`)}
                ${buildMetricCard('AI-риск', ai.ai_risk_composite ?? 0)}
                ${buildMetricCard('Критично', summary.critical_issues ?? 0)}
                ${buildMetricCard('Предупреждений', summary.warning_issues ?? 0)}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">OnPage профиль</h4>
                    <div style="height:200px;"><canvas id="ds-chart-onpage-radar"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">OnPage Score</h4>
                    <div style="height:200px;"><canvas id="ds-chart-onpage-score"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Плотность ключей</h4>
                    <div style="height:200px;"><canvas id="ds-chart-onpage-density"></canvas></div>
                </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Schema</h4>
                    <div class="space-y-1 text-sm">
                        <div><span class="text-gray-600">JSON-LD блоков:</span> <span class="font-medium">${schema.json_ld_blocks ?? 0}</span></div>
                        <div><span class="text-gray-600">Валидных JSON-LD:</span> <span class="font-medium">${schema.json_ld_valid_blocks ?? 0}</span></div>
                        <div><span class="text-gray-600">Элементов Microdata:</span> <span class="font-medium">${schema.microdata_items ?? 0}</span></div>
                        <div><span class="text-gray-600">Элементов RDFa:</span> <span class="font-medium">${schema.rdfa_items ?? 0}</span></div>
                        <div><span class="text-gray-600">Типы:</span> <span class="font-medium">${(schema.types || []).map(t => t.type).slice(0, 8).join(', ') || '-'}</span></div>
                    </div>
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">OpenGraph</h4>
                    <div class="space-y-1 text-sm">
                        <div><span class="text-gray-600">Количество тегов:</span> <span class="font-medium">${opengraph.tags_count ?? 0}</span></div>
                        <div><span class="text-gray-600">Обязательных найдено:</span> <span class="font-medium">${opengraph.required_present_count ?? 0}/5</span></div>
                        <div><span class="text-gray-600">Отсутствует:</span> <span class="font-medium">${(opengraph.required_missing || []).join(', ') || '-'}</span></div>
                        <div><span class="text-gray-600">og:title:</span> <span class="font-medium">${(opengraph.tags || {})['og:title'] || '-'}</span></div>
                        <div><span class="text-gray-600">og:description:</span> <span class="font-medium">${(opengraph.tags || {})['og:description'] || '-'}</span></div>
                        <div><span class="text-gray-600">og:image:</span> <span class="font-medium break-all">${(opengraph.tags || {})['og:image'] || '-'}</span></div>
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <h4 class="font-semibold mb-3">AI-сигналы</h4>
                <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-3 text-sm">
                    <div><span class="text-gray-600">Плотность AI-маркеров/1k:</span> <span class="font-medium">${ai.ai_marker_density_1k ?? 0}</span></div>
                    <div><span class="text-gray-600">Доля хеджирования:</span> <span class="font-medium">${ai.hedging_ratio ?? 0}</span></div>
                    <div><span class="text-gray-600">Повторяемость шаблонов:</span> <span class="font-medium">${ai.template_repetition ?? 0}</span></div>
                    <div><span class="text-gray-600">Вариативность (CV):</span> <span class="font-medium">${ai.burstiness_cv ?? 0}</span></div>
                    <div><span class="text-gray-600">Прокси perplexity:</span> <span class="font-medium">${ai.perplexity_proxy ?? 0}</span></div>
                    <div><span class="text-gray-600">Глубина сущностей/1k:</span> <span class="font-medium">${ai.entity_depth_1k ?? 0}</span></div>
                    <div><span class="text-gray-600">Специфичность утверждений:</span> <span class="font-medium">${ai.claim_specificity_score ?? 0}</span></div>
                    <div><span class="text-gray-600">Сигнал автора:</span> <span class="font-medium">${ai.author_signal_score ?? 0}</span></div>
                    <div><span class="text-gray-600">Атрибуция источников:</span> <span class="font-medium">${ai.source_attribution_score ?? 0}</span></div>
                    <div><span class="text-gray-600">Композитный AI-риск:</span> <span class="font-medium">${ai.ai_risk_composite ?? 0}</span></div>
                </div>
            </div>

            <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Обзор оценок</h4>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        ${scoreBars}
                    </div>
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Топ исправлений (быстрые действия)</h4>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                        ${quickActions || '<div class="text-sm text-gray-500">Нет данных.</div>'}
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <h4 class="font-semibold mb-3">Тепловая карта критичности</h4>
                <div class="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-3">
                    ${heatmapCards || '<div class="text-sm text-gray-500">Нет данных.</div>'}
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                <h4 class="font-semibold mb-3">Очередь приоритетов (Сейчас / Далее / Позже)</h4>
                <table class="w-full min-w-[860px]">
                    <thead class="sticky top-0 z-10 bg-white">
                        <tr class="text-left text-xs text-gray-500 border-b">
                            <th class="py-2 pr-2 bg-white">Этап</th>
                            <th class="py-2 pr-2 bg-white">Критичность</th>
                            <th class="py-2 pr-2 bg-white">Код</th>
                            <th class="py-2 pr-2 bg-white">Проблема</th>
                            <th class="py-2 pr-2 bg-white">Приоритет</th>
                            <th class="py-2 pr-2 bg-white">Трудозатраты</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${queueRows || '<tr><td class="py-2 text-sm text-gray-500" colspan="6">Нет данных.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                <h4 class="font-semibold mb-3">Цели: до / после</h4>
                <table class="w-full min-w-[640px]">
                    <thead class="sticky top-0 z-10 bg-white">
                        <tr class="text-left text-xs text-gray-500 border-b">
                            <th class="py-2 pr-2 bg-white">Метрика</th>
                            <th class="py-2 pr-2 bg-white">Текущее</th>
                            <th class="py-2 pr-2 bg-white">Цель</th>
                            <th class="py-2 pr-2 bg-white">Изменение</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${targetRows || '<tr><td class="py-2 text-sm text-gray-500" colspan="4">Нет данных.</td></tr>'}
                    </tbody>
                </table>
            </div>

            ${buildRecommendations(recs)}

            <div class="bg-white rounded-xl shadow-md p-6">
                <div class="flex flex-wrap items-center justify-between gap-3 mb-3">
                    <h4 class="font-semibold">Проблемы</h4>
                    <div class="inline-flex rounded-lg border border-slate-200 p-1 bg-slate-50 text-xs">
                        <button type="button" data-onpage-issues-filter="all" onclick="filterOnpageIssues('all')" class="px-2 py-1 rounded-md bg-slate-900 text-white">Все</button>
                        <button type="button" data-onpage-issues-filter="critical" onclick="filterOnpageIssues('critical')" class="px-2 py-1 rounded-md text-slate-700">Критично</button>
                        <button type="button" data-onpage-issues-filter="warning" onclick="filterOnpageIssues('warning')" class="px-2 py-1 rounded-md text-slate-700">Предупреждение</button>
                        <button type="button" data-onpage-issues-filter="info" onclick="filterOnpageIssues('info')" class="px-2 py-1 rounded-md text-slate-700">Инфо</button>
                    </div>
                </div>
                <div class="text-xs text-slate-500 mb-2">Показано: <span id="onpage-issues-count">${Math.min(25, issues.length)} / ${Math.min(25, issues.length)}</span></div>
                <div id="onpage-issues-list">
                    ${issuesHtml || '<div class="text-sm text-gray-500">Проблемы не найдены.</div>'}
                </div>
            </div>
        </div>
    `;
}

function filterClusterTable(query) {
    const q = String(query || '').toLowerCase().trim();
    const rows = document.querySelectorAll('#clusterTableBody tr[data-cluster-row]');
    rows.forEach((row) => {
        if (!q) {
            row.style.display = '';
            return;
        }
        const text = String(row.dataset.searchText || '').toLowerCase();
        row.style.display = text.includes(q) ? '' : 'none';
    });
}

function generateClusterizerHTML(result) {
    const r = result.results || {};
    const summary = r.summary || {};
    const settings = r.settings || {};
    const clusters = Array.isArray(r.clusters) ? r.clusters : [];
    const primaryClusters = Array.isArray(r.primary_clusters)
        ? r.primary_clusters
        : clusters.filter((row) => Number(row.size || 0) >= Number(settings.min_cluster_size || 2));
    const intentDistribution = r.intent_distribution || {};
    const unclusteredKeywords = Array.isArray(r.unclustered_keywords) ? r.unclustered_keywords : [];
    const shownClusters = clusters.slice(0, Math.min(clusters.length, 500));

    const clusterRowsHtml = shownClusters.map((cluster) => {
        const clusterId = Number(cluster.cluster_id || 0);
        const size = Number(cluster.size || 0);
        const clusterLabel = escapeHtml(String(cluster.cluster_label || ''));
        const representative = escapeHtml(String(cluster.representative || '-'));
        const topTokens = Array.isArray(cluster.top_tokens)
            ? cluster.top_tokens.slice(0, 8).map((token) => escapeHtml(String(token))).join(', ')
            : '-';
        const keywords = Array.isArray(cluster.keywords) ? cluster.keywords : [];
        const keywordsPreview = keywords
            .slice(0, 5)
            .map((keyword) => escapeHtml(String(keyword)))
            .join(', ');
        const hiddenKeywords = Math.max(0, keywords.length - 5);
        const keywordsDetailHtml = keywords.map((kw) => `<div class="py-0.5">${escapeHtml(String(kw))}</div>`).join('');
        const densityPct = Math.round(Number(cluster.density || 0) * 1000) / 10;
        const avgSimPct = Math.round(Number(cluster.avg_similarity || 0) * 1000) / 10;
        const demandTotal = Math.round(Number(cluster.demand_total || 0) * 100) / 100;
        const demandShare = Math.round(Number(cluster.demand_share_pct || 0) * 10) / 10;
        const intent = escapeHtml(String(cluster.intent || 'mixed'));
        const cohesion = String(cluster.cohesion || 'low');
        const cohesionLabel = cohesion === 'high' ? 'Высокая' : (cohesion === 'medium' ? 'Средняя' : 'Низкая');
        const searchText = `${clusterLabel} ${representative} ${keywords.join(' ')}`;

        return `
            <tr class="border-b border-slate-100 text-sm" data-cluster-row="1" data-search-text="${escapeHtml(searchText.toLowerCase())}">
                <td class="py-2 pr-2 font-medium">${clusterId}</td>
                <td class="py-2 pr-2">${size}</td>
                <td class="py-2 pr-2 break-words">
                    <div class="font-medium text-slate-800">${clusterLabel || representative}</div>
                    ${clusterLabel ? `<div class="text-xs text-slate-500 mt-0.5">${representative}</div>` : ''}
                </td>
                <td class="py-2 pr-2 break-words">${topTokens || '-'}</td>
                <td class="py-2 pr-2">${densityPct}%</td>
                <td class="py-2 pr-2">${avgSimPct}%</td>
                <td class="py-2 pr-2">${demandTotal}</td>
                <td class="py-2 pr-2">${demandShare}%</td>
                <td class="py-2 pr-2">${intent}</td>
                <td class="py-2 pr-2">${cohesionLabel}</td>
                <td class="py-2 pr-2 break-words max-w-xs">
                    <details class="text-xs">
                        <summary class="cursor-pointer text-slate-600 list-none hover:text-slate-900">${keywordsPreview}${hiddenKeywords > 0 ? ` <span class="text-violet-600 font-medium">+${hiddenKeywords}</span>` : ''}</summary>
                        <div class="mt-1 text-slate-700 border-t border-slate-100 pt-1">${keywordsDetailHtml}</div>
                    </details>
                </td>
            </tr>
        `;
    }).join('');

    const unclusteredPreview = unclusteredKeywords
        .slice(0, 200)
        .map((keyword) => `<span class="px-2 py-1 rounded-md bg-slate-100 text-slate-700 text-xs">${escapeHtml(String(keyword))}</span>`)
        .join('');
    const intentChips = Object.entries(intentDistribution)
        .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))
        .map(([intent, count]) => `<span class="px-2 py-1 rounded-md bg-cyan-50 text-cyan-800 text-xs border border-cyan-200">${escapeHtml(String(intent))}: ${Number(count || 0)}</span>`)
        .join('');

    const clusterActionBtns = `
        <button onclick="downloadClusterizerXlsxReport()" class="ds-export-btn"><i class="fas fa-file-excel mr-1"></i>XLSX</button>
        <button onclick="copyCurrentTaskJson()" class="ds-export-btn"><i class="fas fa-copy mr-1"></i>JSON</button>`;

    return `
        <div class="space-y-6 sitepro-results">
            ${buildToolHeader({
                gradient: 'from-fuchsia-700 via-violet-700 to-indigo-700',
                label: 'Keyword Clusterizer',
                title: 'Кластеризатор ключей',
                score: null,
                badges: [
                    { cls: 'bg-white/10 border border-white/20 text-white/90', text: `${Number(summary.clusters_total || 0)} кластеров` },
                    { cls: 'bg-white/10 border border-white/20 text-white/90', text: `${Number(summary.keywords_unique_total || 0)} ключей` },
                ],
                metaLines: [
                    `Метод: ${escapeHtml(String(settings.method || 'jaccard'))}`,
                    `Режим: ${escapeHtml(String(settings.clustering_mode || 'balanced'))}`,
                    `Порог: ${Number(settings.similarity_threshold_pct || 0)}%`,
                ],
                actionButtons: clusterActionBtns,
            })}

            <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-5 gap-3">
                ${buildMetricCard('Входных ключей', Number(summary.keywords_input_total || 0))}
                ${buildMetricCard('Уникальных', Number(summary.keywords_unique_total || 0))}
                ${buildMetricCard('Кластеров', Number(summary.clusters_total || 0))}
                ${buildMetricCard('Основных', Number(summary.primary_clusters_total || 0))}
                ${buildMetricCard('Когезия', `${Math.round(Number(summary.avg_cluster_cohesion || 0) * 1000) / 10}%`)}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Распределение кластеров</h4>
                    <div style="height:200px;"><canvas id="ds-chart-cluster-bar"></canvas></div>
                </div>
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Intent-распределение</h4>
                    <div style="height:200px;"><canvas id="ds-chart-cluster-intent"></canvas></div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
                <div class="text-xs text-slate-500 mb-2">Intent-распределение</div>
                <div class="flex flex-wrap gap-2">${intentChips || '<span class="text-xs text-slate-500">Intent-распределение недоступно.</span>'}</div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                <div class="flex items-center justify-between mb-3">
                    <h4 class="font-semibold">Кластеры</h4>
                    <span class="text-xs text-slate-500">Показано ${shownClusters.length} из ${clusters.length}</span>
                </div>
                <div class="mb-3">
                    <input
                        type="text"
                        placeholder="Поиск по кластерам..."
                        oninput="filterClusterTable(this.value)"
                        class="w-full max-w-sm px-3 py-1.5 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-violet-400"
                    >
                </div>
                <table class="w-full min-w-[1240px]">
                    <thead class="sticky top-0 z-10 bg-white">
                        <tr class="text-left text-xs text-gray-500 border-b">
                            <th class="py-2 pr-2 bg-white">#</th>
                            <th class="py-2 pr-2 bg-white">Размер</th>
                            <th class="py-2 pr-2 bg-white">Репрезентативный ключ</th>
                            <th class="py-2 pr-2 bg-white">Топ токены</th>
                            <th class="py-2 pr-2 bg-white">Плотность</th>
                            <th class="py-2 pr-2 bg-white">Сред. схожесть</th>
                            <th class="py-2 pr-2 bg-white">Спрос</th>
                            <th class="py-2 pr-2 bg-white">Доля спроса</th>
                            <th class="py-2 pr-2 bg-white">Intent</th>
                            <th class="py-2 pr-2 bg-white">Качество</th>
                            <th class="py-2 pr-2 bg-white">Ключи</th>
                        </tr>
                    </thead>
                    <tbody id="clusterTableBody">
                        ${clusterRowsHtml || '<tr><td class="py-2 text-sm text-gray-500" colspan="11">Нет данных для отображения.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Параметры запуска</h4>
                    <div class="space-y-1 text-sm text-slate-700">
                        <div><span class="text-slate-500">Метод:</span> <span class="font-medium">${escapeHtml(String(settings.method || 'jaccard'))}</span></div>
                        <div><span class="text-slate-500">Режим:</span> <span class="font-medium">${escapeHtml(String(settings.clustering_mode || 'balanced'))}</span></div>
                        <div><span class="text-slate-500">Запрошенный порог:</span> <span class="font-medium">${Math.round(Number(settings.similarity_threshold_requested || settings.similarity_threshold || 0) * 1000) / 10}%</span></div>
                        <div><span class="text-slate-500">Порог схожести:</span> <span class="font-medium">${Number(settings.similarity_threshold_pct || 0)}%</span></div>
                        <div><span class="text-slate-500">Мин. размер кластера:</span> <span class="font-medium">${Number(settings.min_cluster_size || 0)}</span></div>
                        <div><span class="text-slate-500">Кластеры >= min size:</span> <span class="font-medium">${primaryClusters.length}</span></div>
                        <div><span class="text-slate-500">Доля спроса в основных кластерах:</span> <span class="font-medium">${Math.round(Number(summary.primary_demand_share_pct || 0) * 10) / 10}%</span></div>
                        <div><span class="text-slate-500">Доля спроса одиночных ключей:</span> <span class="font-medium">${Math.round(Number(summary.singleton_demand_share_pct || 0) * 10) / 10}%</span></div>
                        <div><span class="text-slate-500">Топ-кластер по спросу:</span> <span class="font-medium">${Math.round(Number(summary.top_cluster_demand_share_pct || 0) * 10) / 10}%</span></div>
                        <div><span class="text-slate-500">Средний размер кластера:</span> <span class="font-medium">${Number(summary.avg_cluster_size || 0)}</span></div>
                        <div><span class="text-slate-500">Ключей с низкой уверенностью:</span> <span class="font-medium">${Number(summary.low_confidence_keywords || 0)}</span></div>
                        <div><span class="text-slate-500">Сравнений:</span> <span class="font-medium">${Number(summary.comparisons_total || 0)} из ${Number(summary.comparisons_total_potential || 0)}</span></div>
                    </div>
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Одиночные ключи</h4>
                    <div class="flex flex-wrap gap-2">
                        ${unclusteredPreview || '<span class="text-sm text-slate-500">Одиночных ключей нет.</span>'}
                    </div>
                    ${unclusteredKeywords.length > 200 ? `<div class="mt-2 text-xs text-slate-500">И еще ${unclusteredKeywords.length - 200} ключей.</div>` : ''}
                </div>
            </div>
        </div>
    `;
}

function generateSiteAuditProHTML(result) {
    const r = result.results || {};
    const summary = r.summary || {};
    const pages = r.pages || [];
    const issues = r.issues || [];
    const pipeline = r.pipeline || {};
    const metrics = pipeline.metrics || {};
    const chunkManifest = (r.artifacts || {}).chunk_manifest || {};
    const artifactsMeta = r.artifacts || {};
    const crawlBudgetSummary = artifactsMeta.crawl_budget_summary || {};
    const homepageSecurity = artifactsMeta.homepage_security || {};
    const mode = r.mode || result.mode || 'quick';
    const isBatchMode = Boolean(result.batch_mode || artifactsMeta.batch_mode);
    const batchUrlsCount = Number(result.batch_urls_count || artifactsMeta.batch_urls_requested || 0);
    const duplicates = pipeline.duplicates || {};
    const duplicateTitleGroups = Array.isArray(duplicates.title_groups) ? duplicates.title_groups : [];
    const duplicateDescriptionGroups = Array.isArray(duplicates.description_groups) ? duplicates.description_groups : [];
    const pagesCount = Number(summary.total_pages ?? pages.length ?? 0);
    const totalPagesBase = Math.max(1, pagesCount);
    const totalIssuesBase = Math.max(1, issues.length);
    const normalizeRecommendationText = (text) => String(text || '')
        .toLowerCase()
        .replace(/[^\w\s]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
    const issueFamilyByCode = (code) => {
        const codeL = String(code || '').toLowerCase();
        if (/(title|meta|h1|canonical|robots|viewport|charset|schema|structured)/.test(codeL)) return 'OnPage+Structured';
        if (/(http_status|https|cache|compression|security|mixed_content|crawl_budget|redirect)/.test(codeL)) return 'Technical';
        if (/(thin_content|duplicate_content|ai_|hidden_content|cloaking|cta|list|table|content)/.test(codeL)) return 'Content+AI';
        if (/(anchor|link|orphan|pagerank)/.test(codeL)) return 'LinkGraph';
        if (/(image|alt|webp|avif|external)/.test(codeL)) return 'Images+External';
        if (/(hierarchy|heading|h1_hierarchy)/.test(codeL)) return 'Hierarchy';
        if (/(keyword|tf_idf|intent|cannibal)/.test(codeL)) return 'Keywords';
        return 'Other';
    };
    const ownerHintByCode = (code) => {
        const codeL = String(code || '').toLowerCase();
        if (/(title|meta|h1|keyword|content|ai_|duplicate_)/.test(codeL)) return 'Content+SEO';
        if (/(schema|structured|hreflang|canonical|index|http_status)/.test(codeL)) return 'SEO+Dev';
        if (/(security|cache|compression|https|crawl_budget|redirect)/.test(codeL)) return 'Dev+Infra';
        return 'SEO';
    };
    const rootCauseCluster = (code) => {
        const codeL = String(code || '').toLowerCase();
        if (/(title|meta|h1|keyword|content|duplicate|ai_)/.test(codeL)) return 'Контентная модель';
        if (/(canonical|index|robots|http_status|redirect|crawl_budget)/.test(codeL)) return 'Индексирование';
        if (/(schema|structured|hreflang)/.test(codeL)) return 'Структурированные данные';
        if (/(security|https|cache|compression|mixed_content)/.test(codeL)) return 'Платформа/Инфра';
        if (/(image|alt|webp|avif)/.test(codeL)) return 'Медиа-пайплайн';
        return 'Общее SEO';
    };
    const issueActionByCode = (code) => {
        const codeL = String(code || '').toLowerCase();
        if (codeL.includes('title')) return 'Исправить title: уникальность и оптимальная длина.';
        if (codeL.includes('meta')) return 'Повысить качество и уникальность meta description.';
        if (codeL.includes('h1') || codeL.includes('hierarchy')) return 'Нормализовать иерархию заголовков (один H1, корректная структура H2/H3).';
        if (codeL.includes('canonical')) return 'Устранить конфликты canonical и выровнять цели canonical.';
        if (codeL.includes('crawl_budget') || codeL.includes('redirect')) return 'Снизить потери crawl budget: редиректы, параметрические ловушки, неиндексируемые ветки.';
        if (codeL.includes('schema') || codeL.includes('structured')) return 'Исправить покрытие и ошибки структурированных данных.';
        if (codeL.includes('image') || codeL.includes('alt')) return 'Исправить оптимизацию изображений: ALT, lazy-load, размеры.';
        if (codeL.includes('anchor') || codeL.includes('link') || codeL.includes('orphan')) return 'Улучшить внутреннюю перелинковку и качество анкоров.';
        if (codeL.includes('security') || codeL.includes('https')) return 'Закрыть технические проблемы безопасности/заголовков.';
        return 'Приоритизировать данный тип проблемы и применить шаблонные исправления.';
    };
    const recommendationThemeFromText = (text) => {
        const t = normalizeRecommendationText(text);
        if (!t) return { key: 'general', label: 'Общее SEO', action: 'Приоритизировать исправления по влиянию и критичности.' };
        if (/(title|meta)/.test(t)) return { key: 'metadata', label: 'Качество метаданных', action: 'Повысить уникальность и качество title/meta в шаблонах.' };
        if (/(h1|heading|hierarchy)/.test(t)) return { key: 'heading_structure', label: 'Структура заголовков', action: 'Нормализовать иерархию заголовков и оставить один явный H1.' };
        if (/(canonical|index|robots|crawl|redirect|http status)/.test(t)) return { key: 'indexing_flow', label: 'Индексирование', action: 'Исправить сигналы индексирования и снизить потери crawl budget.' };
        if (/(schema|structured)/.test(t)) return { key: 'structured_data', label: 'Структурированные данные', action: 'Исправить покрытие/валидацию schema на ключевых шаблонах.' };
        if (/(image|alt|lazy|webp|avif)/.test(t)) return { key: 'image_pipeline', label: 'Медиа-пайплайн', action: 'Исправить покрытие ALT и оптимизацию изображений.' };
        if (/(anchor|link|orphan)/.test(t)) return { key: 'internal_links', label: 'Внутренние ссылки', action: 'Улучшить внутреннюю перелинковку и релевантность анкоров.' };
        if (/(security|https|header|cache|compression)/.test(t)) return { key: 'platform_infra', label: 'Платформа/Инфра', action: 'Закрыть инфраструктурные и security-SEO проблемы.' };
        if (/(duplicate|thin|content|keyword|ai)/.test(t)) return { key: 'content_model', label: 'Контентная модель', action: 'Повысить уникальность, глубину и покрытие интента контента.' };
        return { key: `custom:${t.slice(0, 80)}`, label: 'Общее SEO', action: String(text || '').trim() || 'Общие SEO-улучшения.' };
    };
    const issuesByUrl = {};
    issues.forEach((issue) => {
        const url = String(issue.url || '');
        if (!url) return;
        if (!issuesByUrl[url]) issuesByUrl[url] = [];
        issuesByUrl[url].push(issue);
    });

    const issueCodeStatsMap = new Map();
    const clusterStatsMap = new Map();
    const ownerStatsMap = new Map();
    const issueFamilyStatsMap = new Map();
    const groupedIssuesByCode = new Map();
    issues.forEach((issue) => {
        const code = String(issue.code || 'unknown');
        const severity = String(issue.severity || 'info').toLowerCase();
        const cluster = rootCauseCluster(code);
        const owner = ownerHintByCode(code);
        const family = issueFamilyByCode(code);
        const url = String(issue.url || '');
        if (!issueCodeStatsMap.has(code)) issueCodeStatsMap.set(code, { code, count: 0, critical: 0, warning: 0, info: 0 });
        const codeRow = issueCodeStatsMap.get(code);
        codeRow.count += 1;
        codeRow[severity] = (codeRow[severity] || 0) + 1;
        if (!clusterStatsMap.has(cluster)) clusterStatsMap.set(cluster, { cluster, count: 0 });
        clusterStatsMap.get(cluster).count += 1;
        if (!ownerStatsMap.has(owner)) ownerStatsMap.set(owner, { owner, count: 0 });
        ownerStatsMap.get(owner).count += 1;
        if (!issueFamilyStatsMap.has(family)) issueFamilyStatsMap.set(family, { family, count: 0 });
        issueFamilyStatsMap.get(family).count += 1;
        if (!groupedIssuesByCode.has(code)) {
            groupedIssuesByCode.set(code, { code, critical: 0, warning: 0, info: 0, urls: new Set() });
        }
        const grouped = groupedIssuesByCode.get(code);
        grouped[severity] = (grouped[severity] || 0) + 1;
        if (url) grouped.urls.add(url);
    });

    const issueActionPlan = Array.from(groupedIssuesByCode.values()).map((row) => {
        const critical = Number(row.critical || 0);
        const warning = Number(row.warning || 0);
        const info = Number(row.info || 0);
        const topSeverity = critical > 0 ? 'critical' : warning > 0 ? 'warning' : 'info';
        const affectedPages = row.urls.size;
        const sharePct = Number(((affectedPages / totalPagesBase) * 100).toFixed(1));
        const impactScore = Number(((critical * 3.0) + (warning * 2.0) + info + (sharePct / 10.0)).toFixed(1));
        const effort = topSeverity === 'critical' ? 'M' : 'S';
        const roiScore = Number((impactScore / (effort === 'M' ? 1.3 : 1.0)).toFixed(1));
        const sprintBucket = roiScore >= 25 ? 'Сейчас' : roiScore >= 10 ? 'Далее' : 'Позже';
        const action = issueActionByCode(row.code);
        const theme = recommendationThemeFromText(action);
        return {
            code: row.code,
            topSeverity,
            affectedPages,
            sharePct,
            critical,
            warning,
            info,
            impactScore,
            effort,
            roiScore,
            sprintBucket,
            owner: ownerHintByCode(row.code),
            rootCause: rootCauseCluster(row.code),
            recommendation: action,
            representativeUrls: Array.from(row.urls).slice(0, 5),
            themeKey: theme.key,
            themeLabel: theme.label,
            themeAction: theme.action,
        };
    }).sort((a, b) =>
        (b.impactScore - a.impactScore) ||
        (b.critical - a.critical) ||
        (b.warning - a.warning)
    );

    const recommendationMap = new Map();
    issueActionPlan.forEach((row) => {
        if (!recommendationMap.has(row.themeKey)) {
            recommendationMap.set(row.themeKey, {
                action: row.themeAction,
                theme: row.themeLabel,
                affectedUrls: new Set(),
                critical: 0,
                warning: 0,
                info: 0,
                sourceCodes: new Set(),
            });
        }
        const bucket = recommendationMap.get(row.themeKey);
        row.representativeUrls.forEach((u) => bucket.affectedUrls.add(u));
        bucket.critical += row.critical;
        bucket.warning += row.warning;
        bucket.info += row.info;
        bucket.sourceCodes.add(row.code);
    });

    pages.forEach((page) => {
        const rec = String(page.recommendation || '').trim();
        if (!rec) return;
        const theme = recommendationThemeFromText(rec);
        const url = String(page.url || '');
        const pageIssues = issuesByUrl[url] || [];
        if (!recommendationMap.has(theme.key)) {
            recommendationMap.set(theme.key, {
                action: theme.action,
                theme: theme.label,
                affectedUrls: new Set(),
                critical: 0,
                warning: 0,
                info: 0,
                sourceCodes: new Set(),
            });
        }
        const bucket = recommendationMap.get(theme.key);
        if (url) bucket.affectedUrls.add(url);
        pageIssues.forEach((i) => {
            const code = String(i.code || '').trim();
            if (code) bucket.sourceCodes.add(code);
        });
    });

    const actionableRecommendations = Array.from(recommendationMap.values())
        .map((row) => ({
            ...row,
            affectedCount: row.affectedUrls.size,
            urlsPreview: Array.from(row.affectedUrls).slice(0, 3),
            codesPreview: Array.from(row.sourceCodes).slice(0, 4),
        }))
        .sort((a, b) =>
            (b.critical - a.critical) ||
            (b.warning - a.warning) ||
            (b.affectedCount - a.affectedCount)
        )
        .slice(0, 10);

    const issueCodeStats = Array.from(issueCodeStatsMap.values())
        .map((row) => ({ ...row, sharePct: Number(((row.count / totalIssuesBase) * 100).toFixed(1)) }))
        .sort((a, b) => (b.count - a.count))
        .slice(0, 8);
    const clusterStats = Array.from(clusterStatsMap.values())
        .sort((a, b) => (b.count - a.count))
        .slice(0, 6);
    const ownerStats = Array.from(ownerStatsMap.values())
        .sort((a, b) => (b.count - a.count))
        .slice(0, 6);
    const issueFamilyStats = Array.from(issueFamilyStatsMap.values())
        .map((row) => ({ ...row, sharePct: Number(((row.count / totalIssuesBase) * 100).toFixed(1)) }))
        .sort((a, b) => (b.count - a.count))
        .slice(0, 7);
    const criticalPages = pages
        .map((page) => {
            const url = String(page.url || '');
            const pageIssues = issuesByUrl[url] || [];
            const critical = pageIssues.filter(i => String(i.severity || '').toLowerCase() === 'critical').length;
            const warning = pageIssues.filter(i => String(i.severity || '').toLowerCase() === 'warning').length;
            return { url, critical, warning };
        })
        .filter((row) => row.critical > 0 || row.warning > 0)
        .sort((a, b) => (b.critical - a.critical) || (b.warning - a.warning))
        .slice(0, 8);
    const criticalPagesAllCount = pages.filter((page) => {
        const url = String(page.url || '');
        const pageIssues = issuesByUrl[url] || [];
        return pageIssues.some((i) => String(i.severity || '').toLowerCase() === 'critical');
    }).length;
    const criticalPagesPct = Number(((criticalPagesAllCount / totalPagesBase) * 100).toFixed(1));
    const topFixPages = pages
        .map((p) => ({
            url: p.url || '',
            health: Number(p.health_score ?? 0),
            issues: Array.isArray(p.all_issues) ? p.all_issues.length : 0,
            recommendation: p.recommendation || '',
        }))
        .sort((a, b) => (b.issues - a.issues) || (a.health - b.health))
        .slice(0, 10);
    const totalIssues = Number(summary.issues_total ?? issues.length ?? 0);
    const score = Number(summary.score ?? 0);
    const scorePct = Math.max(0, Math.min(100, Math.round(score)));
    const issueLoadPct = pagesCount > 0 ? Math.min(100, Math.round((totalIssues / pagesCount) * 10)) : 0;
    const crawlRiskHigh = Number(metrics.crawl_budget_high_risk ?? 0);
    const crawlRiskMedium = Number(metrics.crawl_budget_medium_risk ?? 0);
    const boolLabel = (value) => value === true ? 'Да' : value === false ? 'Нет' : 'н/д';
    const avgPerfLightFromPages = pages.length
        ? Number((pages.reduce((sum, p) => sum + Number(p.perf_light_score || 0), 0) / pages.length).toFixed(1))
        : 0;
    const aiPages = pages.map((p) => ({
        url: p.url || '',
        aiRiskScore: Number(p.ai_risk_score || 0),
        aiRiskLevel: String(p.ai_risk_level || 'low').toLowerCase(),
        aiMarkersCount: Number(p.ai_markers_count || 0),
        aiDensity: Number(p.ai_markers_density_1k || 0),
        aiSample: String(p.ai_marker_sample || ''),
        aiGuard: p.ai_false_positive_guard === true,
    }));
    const aiTotals = {
        markerHits: aiPages.reduce((sum, row) => sum + row.aiMarkersCount, 0),
        avgRisk: Number((aiPages.reduce((sum, row) => sum + row.aiRiskScore, 0) / Math.max(1, aiPages.length)).toFixed(1)),
        avgDensity: Number((aiPages.reduce((sum, row) => sum + row.aiDensity, 0) / Math.max(1, aiPages.length)).toFixed(2)),
        highRiskPages: aiPages.filter((row) => row.aiRiskLevel === 'high').length,
        mediumRiskPages: aiPages.filter((row) => row.aiRiskLevel === 'medium').length,
        guardedPages: aiPages.filter((row) => row.aiGuard).length,
    };
    const aiRiskRowsHtml = aiPages
        .filter((row) => row.aiMarkersCount > 0 || row.aiRiskScore > 0)
        .sort((a, b) =>
            (b.aiRiskScore - a.aiRiskScore) ||
            (b.aiMarkersCount - a.aiMarkersCount) ||
            (b.aiDensity - a.aiDensity)
        )
        .slice(0, 10)
        .map((row) => `
            <tr class="border-b border-slate-100 align-top">
                <td class="py-2 pr-2 text-xs break-all">${escapeHtml(row.url)}</td>
                <td class="py-2 pr-2 text-xs font-medium">${escapeHtml(row.aiRiskScore.toFixed(1))}</td>
                <td class="py-2 pr-2 text-xs">${escapeHtml(row.aiMarkersCount)}</td>
                <td class="py-2 pr-2 text-xs">${escapeHtml(row.aiDensity.toFixed(2))}</td>
                <td class="py-2 pr-2 text-xs">
                    <span class="px-2 py-0.5 rounded-full ${row.aiRiskLevel === 'high' ? 'bg-rose-100 text-rose-700' : row.aiRiskLevel === 'medium' ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700'}">${escapeHtml(row.aiRiskLevel)}</span>
                </td>
                <td class="py-2 pr-2 text-xs text-slate-700">${escapeHtml(row.aiSample || '-')}</td>
            </tr>
        `).join('');
    const contentSignals = {
        lowUnique: pages.filter((p) => Number(p.unique_percent || 0) > 0 && Number(p.unique_percent || 0) < 55).length,
        highBoilerplate: pages.filter((p) => Number(p.boilerplate_percent || 0) >= 45).length,
        keywordStuffing: pages.filter((p) => Number(p.keyword_stuffing_score || 0) >= 3).length,
        hiddenContent: pages.filter((p) => p.hidden_content === true).length,
        highFiller: pages.filter((p) => Number(p.filler_ratio || 0) >= 0.08).length,
        toxicContent: pages.filter((p) => Number(p.toxicity_score || 0) >= 1.5).length,
    };
    const contentHygieneScore = Math.max(0, 100 - Math.min(100, Math.round(((contentSignals.highBoilerplate + contentSignals.keywordStuffing + contentSignals.hiddenContent) / totalPagesBase) * 100)));
    const aiTrustScore = Math.max(0, 100 - Math.min(100, Math.round((aiTotals.highRiskPages / totalPagesBase) * 100)));
    const indexingStabilityScore = Math.max(0, 100 - Math.min(100, Math.round(((crawlRiskHigh + Number(metrics.non_https_pages || 0)) / totalPagesBase) * 100)));
    const accessibilityScore = Math.max(0, 100 - Math.min(100, Math.round((Number(metrics.pages_without_alt || 0) / totalPagesBase) * 100)));
    const insightPillars = [
        { label: 'Content hygiene', value: contentHygieneScore, tone: contentHygieneScore >= 75 ? 'text-emerald-700' : contentHygieneScore >= 55 ? 'text-amber-700' : 'text-rose-700' },
        { label: 'AI trust', value: aiTrustScore, tone: aiTrustScore >= 75 ? 'text-emerald-700' : aiTrustScore >= 55 ? 'text-amber-700' : 'text-rose-700' },
        { label: 'Indexing stability', value: indexingStabilityScore, tone: indexingStabilityScore >= 75 ? 'text-emerald-700' : indexingStabilityScore >= 55 ? 'text-amber-700' : 'text-rose-700' },
        { label: 'Accessibility baseline', value: accessibilityScore, tone: accessibilityScore >= 75 ? 'text-emerald-700' : accessibilityScore >= 55 ? 'text-amber-700' : 'text-rose-700' },
    ];
    const insightPillarsHtml = insightPillars.map((item) => `
        <div class="rounded-xl border border-slate-200 bg-white p-3">
            <div class="text-xs text-slate-500">${escapeHtml(item.label)}</div>
            <div class="mt-1 text-xl font-semibold ${item.tone}">${escapeHtml(item.value)}</div>
            <div class="mt-1 h-1.5 bg-slate-200 rounded-full overflow-hidden">
                <div class="h-full ${item.value >= 75 ? 'bg-emerald-500' : item.value >= 55 ? 'bg-amber-500' : 'bg-rose-500'}" style="width:${escapeHtml(item.value)}%"></div>
            </div>
        </div>
    `).join('');
    const metricTone = (state) => state === 'good'
        ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
        : state === 'warn'
            ? 'bg-amber-50 text-amber-700 border-amber-200'
            : 'bg-rose-50 text-rose-700 border-rose-200';
    const pipelineRows = [
        {
            metric: 'Avg response time',
            value: `${Number(metrics.avg_response_time_ms || 0)} ms`,
            target: '< 1200 ms',
            state: Number(metrics.avg_response_time_ms || 0) <= 1200 ? 'good' : Number(metrics.avg_response_time_ms || 0) <= 2000 ? 'warn' : 'bad',
        },
        {
            metric: 'Avg readability',
            value: Number(metrics.avg_readability_score || 0).toFixed(1),
            target: '>= 55',
            state: Number(metrics.avg_readability_score || 0) >= 55 ? 'good' : Number(metrics.avg_readability_score || 0) >= 40 ? 'warn' : 'bad',
        },
        {
            metric: 'Avg link quality',
            value: Number(metrics.avg_link_quality_score || 0).toFixed(1),
            target: '>= 70',
            state: Number(metrics.avg_link_quality_score || 0) >= 70 ? 'good' : Number(metrics.avg_link_quality_score || 0) >= 50 ? 'warn' : 'bad',
        },
        {
            metric: 'Avg perf light score',
            value: Number((metrics.avg_perf_light_score ?? avgPerfLightFromPages) || 0).toFixed(1),
            target: '>= 70',
            state: Number((metrics.avg_perf_light_score ?? avgPerfLightFromPages) || 0) >= 70 ? 'good' : Number((metrics.avg_perf_light_score ?? avgPerfLightFromPages) || 0) >= 50 ? 'warn' : 'bad',
        },
        {
            metric: 'Orphan pages',
            value: escapeHtml(metrics.orphan_pages ?? 0),
            target: '0',
            state: Number(metrics.orphan_pages || 0) === 0 ? 'good' : Number(metrics.orphan_pages || 0) <= Math.max(2, Math.round(totalPagesBase * 0.05)) ? 'warn' : 'bad',
        },
        {
            metric: 'Pages without ALT',
            value: escapeHtml(metrics.pages_without_alt ?? 0),
            target: '<= 10%',
            state: Number(metrics.pages_without_alt || 0) <= Math.round(totalPagesBase * 0.1) ? 'good' : Number(metrics.pages_without_alt || 0) <= Math.round(totalPagesBase * 0.2) ? 'warn' : 'bad',
        },
        {
            metric: 'Non-HTTPS pages',
            value: escapeHtml(metrics.non_https_pages ?? 0),
            target: '0',
            state: Number(metrics.non_https_pages || 0) === 0 ? 'good' : 'bad',
        },
        {
            metric: 'Crawl risk (H/M)',
            value: `${Number(metrics.crawl_budget_high_risk || 0)} / ${Number(metrics.crawl_budget_medium_risk || 0)}`,
            target: 'Downtrend',
            state: Number(metrics.crawl_budget_high_risk || 0) === 0 ? 'good' : Number(metrics.crawl_budget_high_risk || 0) <= Math.max(1, Math.round(totalPagesBase * 0.05)) ? 'warn' : 'bad',
        },
    ];
    const pipelineRowsHtml = pipelineRows.map((row) => `
        <tr class="border-b border-slate-100 text-sm">
            <td class="py-2 pr-2 font-medium text-slate-800">${escapeHtml(row.metric)}</td>
            <td class="py-2 pr-2 text-slate-900">${escapeHtml(row.value)}</td>
            <td class="py-2 pr-2 text-slate-500">${escapeHtml(row.target)}</td>
            <td class="py-2 pr-2">
                <span class="px-2 py-0.5 rounded-full border text-xs ${metricTone(row.state)}">${escapeHtml(row.state.toUpperCase())}</span>
            </td>
        </tr>
    `).join('');
    const spamTermMap = new Map();
    const pushSpamTerm = (term, type, url, weight = 1.0) => {
        const clean = String(term || '').trim().toLowerCase();
        const cleanUrl = String(url || '').trim();
        if (!clean || clean.length < 3) return;
        const key = `${type}::${clean}`;
        if (!spamTermMap.has(key)) {
            spamTermMap.set(key, { term: clean, type, pages: new Set(), weight: 0 });
        }
        const node = spamTermMap.get(key);
        if (cleanUrl) node.pages.add(cleanUrl);
        node.weight += Number(weight || 0);
    };
    pages.forEach((page) => {
        const url = String(page.url || '');
        const keywordProfile = page.keyword_density_profile || {};
        Object.entries(keywordProfile).forEach(([term, density]) => {
            const d = Number(density || 0);
            if (d >= 2.5) pushSpamTerm(term, 'keyword_density', url, d / 2);
        });
        (page.filler_phrases || []).slice(0, 12).forEach((term) => pushSpamTerm(term, 'filler_phrase', url, 1.7));
        (page.ai_markers_list || []).slice(0, 12).forEach((term) => pushSpamTerm(term, 'ai_marker', url, 2.0));
        if (Number(page.keyword_stuffing_score || 0) >= 3) {
            (page.top_keywords || []).slice(0, 5).forEach((term) => pushSpamTerm(term, 'keyword_stuffing', url, Number(page.keyword_stuffing_score || 0) / 2));
        }
    });
    const spamRows = Array.from(spamTermMap.values())
        .map((row) => {
            const pagesHit = row.pages.size;
            const sharePct = Number(((pagesHit / totalPagesBase) * 100).toFixed(1));
            const riskScore = Number((row.weight + pagesHit + (sharePct / 10)).toFixed(1));
            return { ...row, pagesHit, sharePct, riskScore };
        })
        .sort((a, b) => (b.riskScore - a.riskScore) || (b.pagesHit - a.pagesHit))
        .slice(0, 20);
    const spamRowsHtml = spamRows.map((row) => `
        <tr class="border-b border-slate-100 text-sm">
            <td class="py-2 pr-2 font-medium text-slate-800">${escapeHtml(row.term)}</td>
            <td class="py-2 pr-2 text-xs">
                <span class="px-2 py-0.5 rounded-full ${row.type === 'ai_marker' ? 'bg-rose-100 text-rose-700' : row.type === 'filler_phrase' ? 'bg-amber-100 text-amber-700' : 'bg-indigo-100 text-indigo-700'}">${escapeHtml(row.type)}</span>
            </td>
            <td class="py-2 pr-2">${escapeHtml(row.pagesHit)}</td>
            <td class="py-2 pr-2">${escapeHtml(row.sharePct)}%</td>
            <td class="py-2 pr-2 font-medium">${escapeHtml(row.riskScore)}</td>
        </tr>
    `).join('');
    const downloadsHtml = (chunkManifest.chunks || []).flatMap(chunk =>
        (chunk.files || []).map(f => {
            const safeUrl = sanitizeHttpUrl(f.download_url || '');
            if (!safeUrl) {
                return `<span class="text-sm text-gray-500">${escapeHtml(f.filename || 'artifact.jsonl')} (invalid download URL)</span>`;
            }
            return `
                <a class="text-sm text-blue-700 hover:text-blue-900 underline"
                   href="${escapeHtml(safeUrl)}"
                   target="_blank"
                   rel="noopener">
                    ${escapeHtml(f.filename || 'artifact.jsonl')} (${escapeHtml(f.records || 0)} rows)
                </a>
            `;
        })
    ).join('<br>');
    const topFixRowsHtml = topFixPages.map((row) => `
        <tr class="border-b border-slate-100 align-top">
            <td class="py-2 pr-2 text-sm break-all">${escapeHtml(row.url)}</td>
            <td class="py-2 pr-2 text-sm font-medium">${escapeHtml(row.health.toFixed(1))}</td>
            <td class="py-2 pr-2 text-sm font-medium">${escapeHtml(row.issues)}</td>
            <td class="py-2 pr-2 text-xs text-slate-700">${escapeHtml(row.recommendation || '-')}</td>
        </tr>
    `).join('');
    const duplicateTitleHtml = duplicateTitleGroups.slice(0, 5).map((g) => `
        <div class="text-sm py-2 border-b border-slate-100">
            <div class="font-medium truncate">${escapeHtml(g.value || '')}</div>
            <div class="text-xs text-slate-500">URL: ${escapeHtml((g.urls || []).length)}</div>
        </div>
    `).join('');
    const duplicateDescriptionHtml = duplicateDescriptionGroups.slice(0, 5).map((g) => `
        <div class="text-sm py-2 border-b border-slate-100">
            <div class="font-medium truncate">${escapeHtml(g.value || '')}</div>
            <div class="text-xs text-slate-500">URL: ${escapeHtml((g.urls || []).length)}</div>
        </div>
    `).join('');
    const issueTypeRowsHtml = issueCodeStats.map((row) => `
        <tr class="border-b border-slate-100 align-top">
            <td class="py-2 pr-2 text-xs">
                <button type="button" onclick="applySiteProIssuePreset('${escapeHtml(String(row.code || '').replace(/'/g, '\\\''))}')" class="text-indigo-700 hover:text-indigo-900 underline">${escapeHtml(row.code)}</button>
            </td>
            <td class="py-2 pr-2 text-xs font-medium">${escapeHtml(row.count)}</td>
            <td class="py-2 pr-2 text-xs text-slate-600">${escapeHtml(row.sharePct)}%</td>
            <td class="py-2 pr-2 text-xs text-red-700">${escapeHtml(row.critical || 0)}</td>
            <td class="py-2 pr-2 text-xs text-amber-700">${escapeHtml(row.warning || 0)}</td>
            <td class="py-2 pr-2 text-xs text-blue-700">${escapeHtml(row.info || 0)}</td>
        </tr>
    `).join('');
    const clusterRowsHtml = clusterStats.map((row) => `
        <div class="flex items-center justify-between py-1.5 border-b border-slate-100 text-sm">
            <button type="button" onclick="applySiteProIssuePreset('${escapeHtml(String(row.cluster || '').replace(/'/g, '\\\''))}')" class="text-indigo-700 hover:text-indigo-900 underline text-left">${escapeHtml(row.cluster)}</button>
            <span class="font-semibold">${escapeHtml(row.count)}</span>
        </div>
    `).join('');
    const ownerRowsHtml = ownerStats.map((row) => `
        <div class="flex items-center justify-between py-1.5 border-b border-slate-100 text-sm">
            <button type="button" onclick="applySiteProOwnerPreset('${escapeHtml(String(row.owner || '').replace(/'/g, '\\\''))}')" class="text-indigo-700 hover:text-indigo-900 underline text-left">${escapeHtml(row.owner)}</button>
            <span class="font-semibold">${escapeHtml(row.count)}</span>
        </div>
    `).join('');
    const issueFamilyCardsHtml = issueFamilyStats.map((row) => `
        <div class="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div class="text-xs uppercase tracking-wide text-slate-500">${escapeHtml(row.family)}</div>
            <div class="mt-1 text-lg font-semibold text-slate-900">${escapeHtml(row.count)}</div>
            <div class="text-xs text-slate-600">${escapeHtml(row.sharePct)}% от всех проблем</div>
        </div>
    `).join('');
    const criticalPagesRowsHtml = criticalPages.map((row) => `
        <tr class="border-b border-slate-100 align-top">
            <td class="py-2 pr-2 text-xs break-all">${escapeHtml(row.url)}</td>
            <td class="py-2 pr-2 text-xs text-red-700 font-medium">${escapeHtml(row.critical)}</td>
            <td class="py-2 pr-2 text-xs text-amber-700 font-medium">${escapeHtml(row.warning)}</td>
        </tr>
    `).join('');
    const recommendationCardsHtml = actionableRecommendations.map((row) => `
        <div class="rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div class="inline-flex items-center px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 text-[11px] mb-1">${escapeHtml(row.theme || 'Общее SEO')}</div>
            <div class="text-sm font-medium text-slate-900">${escapeHtml(row.action)}</div>
            <div class="mt-1 text-xs text-slate-600">Затронутые страницы: ${escapeHtml(row.affectedCount)} | К:${escapeHtml(row.critical)} П:${escapeHtml(row.warning)} И:${escapeHtml(row.info)}</div>
            <div class="mt-1 text-xs text-slate-500">Типы проблем: ${escapeHtml(row.codesPreview.join(', ') || '-')}</div>
            <div class="mt-2 text-xs text-slate-500">${row.urlsPreview.map((u) => `<div class="break-all">${escapeHtml(u)}</div>`).join('') || '<div>-</div>'}</div>
        </div>
    `).join('');
    const actionPlanRowsHtml = issueActionPlan.slice(0, 12).map((row, idx) => `
        <tr class="border-b border-slate-100 align-top">
            <td class="py-2 pr-2 text-xs font-semibold">${escapeHtml(idx + 1)}</td>
            <td class="py-2 pr-2 text-xs">
                <button type="button" onclick="applySiteProIssuePreset('${escapeHtml(String(row.code || '').replace(/'/g, '\\\''))}')" class="text-indigo-700 hover:text-indigo-900 underline">${escapeHtml(row.code)}</button>
            </td>
            <td class="py-2 pr-2 text-xs">
                <span class="px-2 py-0.5 rounded-full ${row.topSeverity === 'critical' ? 'bg-rose-100 text-rose-700' : row.topSeverity === 'warning' ? 'bg-amber-100 text-amber-700' : 'bg-sky-100 text-sky-700'}">${escapeHtml(row.topSeverity)}</span>
            </td>
            <td class="py-2 pr-2 text-xs">${escapeHtml(row.affectedPages)} (${escapeHtml(row.sharePct)}%)</td>
            <td class="py-2 pr-2 text-xs font-medium">${escapeHtml(row.impactScore)}</td>
            <td class="py-2 pr-2 text-xs">${escapeHtml(row.owner)}</td>
            <td class="py-2 pr-2 text-xs">${escapeHtml(row.sprintBucket)}</td>
            <td class="py-2 pr-2 text-xs text-slate-700">${escapeHtml(row.recommendation)}</td>
        </tr>
    `).join('');
    const nowRoadmap = actionableRecommendations
        .filter((row) => row.critical > 0)
        .slice(0, 5);
    const nextRoadmap = actionableRecommendations
        .filter((row) => row.critical === 0 && row.warning > 0)
        .slice(0, 5);
    const laterRoadmap = actionableRecommendations
        .filter((row) => row.critical === 0 && row.warning === 0)
        .slice(0, 5);
    const roadmapList = (rows, emptyText) => rows.length
        ? rows.map((row) => `<li class="mb-2"><div class="font-medium">${escapeHtml(row.action)}</div><div class="text-xs text-slate-500">Охват: ${escapeHtml(row.affectedCount)} страниц</div></li>`).join('')
        : `<li class="text-slate-500">${escapeHtml(emptyText)}</li>`;

    const siteProActionBtns = `
        <button onclick="downloadSiteAuditProDocxReport()" class="ds-export-btn"><i class="fas fa-file-word mr-1"></i>DOCX</button>
        <button onclick="downloadSiteAuditProXlsxReport()" class="ds-export-btn"><i class="fas fa-file-excel mr-1"></i>XLSX</button>
        <button onclick="copyCurrentTaskJson()" class="ds-export-btn"><i class="fas fa-copy mr-1"></i>JSON</button>`;

    return `
        <div class="space-y-6 sitepro-results">
            ${buildToolHeader({
                gradient: 'from-indigo-700 via-violet-700 to-purple-700',
                label: 'Site Audit Pro',
                title: 'Site Audit Pro',
                subtitle: result.url || '',
                score: scorePct,
                scoreLabel: 'SEO',
                badges: [
                    { cls: 'bg-white/10 border border-white/20 text-white/90', text: `${escapeHtml(String(pagesCount))} страниц` },
                    { cls: 'bg-white/10 border border-white/20 text-white/90', text: `режим: ${escapeHtml(String(mode))}` },
                    ...(summary.critical_issues > 0 ? [{ cls: 'bg-rose-500/20 border border-rose-400/40 text-rose-100', text: `${escapeHtml(String(summary.critical_issues))} критичных` }] : []),
                    ...(summary.warning_issues > 0 ? [{ cls: 'bg-amber-400/20 border border-amber-300/40 text-amber-100', text: `${escapeHtml(String(summary.warning_issues))} предупреждений` }] : []),
                ],
                metaLines: [
                    `Всего проблем: ${escapeHtml(String(totalIssues))}`,
                    `Критично: ${escapeHtml(String(summary.critical_issues || 0))}`,
                    `Инфо: ${escapeHtml(String(summary.info_issues || 0))}`,
                ],
                actionButtons: siteProActionBtns,
            })}

            <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                ${buildMetricCard('Страницы', escapeHtml(String(pagesCount)))}
                ${buildMetricCard('Проблем', escapeHtml(String(totalIssues)))}
                ${buildMetricCard('Критично', escapeHtml(String(summary.critical_issues || 0)))}
                ${buildMetricCard('Предупреждений', escapeHtml(String(summary.warning_issues || 0)))}
            </div>

            <!-- Charts -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="ds-card" style="padding:1rem;">
                    <h4 class="text-sm font-semibold mb-2" style="color:var(--ds-text);">Профиль аудита сайта</h4>
                    <div style="height:200px;"><canvas id="ds-chart-sitepro-radar"></canvas></div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <h4 class="font-semibold mb-3">Ключевые направления качества</h4>
                <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
                    ${insightPillarsHtml}
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <div class="flex flex-wrap items-center justify-between gap-3 mb-3">
                    <h4 class="font-semibold">Обзор проблем</h4>
                    <div class="inline-flex rounded-lg border border-slate-200 p-1 bg-slate-50 text-xs">
                        <button type="button" data-sitepro-issues-filter="all" onclick="setSiteAuditProIssuesFilter('all')" class="px-2 py-1 rounded-md bg-slate-900 text-white">Все</button>
                        <button type="button" data-sitepro-issues-filter="critical" onclick="setSiteAuditProIssuesFilter('critical')" class="px-2 py-1 rounded-md text-slate-700">Критично</button>
                        <button type="button" data-sitepro-issues-filter="warning" onclick="setSiteAuditProIssuesFilter('warning')" class="px-2 py-1 rounded-md text-slate-700">Предупреждение</button>
                        <button type="button" data-sitepro-issues-filter="info" onclick="setSiteAuditProIssuesFilter('info')" class="px-2 py-1 rounded-md text-slate-700">Инфо</button>
                    </div>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-6 gap-2 mb-3">
                    <input id="sitepro-issues-search" type="text" oninput="updateSiteAuditProIssuesQuery(this.value)" placeholder="Поиск по коду, заголовку, URL..." class="md:col-span-3 px-3 py-2 border rounded-lg text-sm">
                    <select id="sitepro-issues-limit" onchange="updateSiteAuditProIssuesLimit(this.value)" class="px-3 py-2 border rounded-lg text-sm">
                        <option value="20" selected>20 строк</option>
                        <option value="50">50 строк</option>
                        <option value="100">100 строк</option>
                    </select>
                    <select id="sitepro-issues-sort" onchange="updateSiteAuditProIssuesSort(this.value)" class="px-3 py-2 border rounded-lg text-sm">
                        <option value="severity" selected>Сортировка: критичность</option>
                        <option value="url">Сортировка: URL</option>
                    </select>
                    <select id="sitepro-issues-owner" onchange="updateSiteAuditProIssuesOwner(this.value)" class="px-3 py-2 border rounded-lg text-sm">
                        <option value="all" selected>Владелец: все</option>
                        <option value="SEO">Владелец: SEO</option>
                        <option value="Content+SEO">Владелец: Content+SEO</option>
                        <option value="SEO+Dev">Владелец: SEO+Dev</option>
                        <option value="Dev+Infra">Владелец: Dev+Infra</option>
                    </select>
                </div>
                <div class="text-xs text-slate-500 mb-2">Показано: <span id="sitepro-issues-count">0 / 0</span></div>
                <div id="sitepro-issues-list" class="max-h-[420px] overflow-auto pr-1"></div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Обзор рисков AI-маркеров</h4>
                    <div class="grid grid-cols-2 gap-3 text-sm">
                        <div class="rounded-lg border border-rose-200 bg-rose-50 p-3">
                            <div class="text-rose-700">Страницы высокого риска</div>
                            <div class="text-xl font-semibold text-rose-800">${escapeHtml(aiTotals.highRiskPages)}</div>
                        </div>
                        <div class="rounded-lg border border-amber-200 bg-amber-50 p-3">
                            <div class="text-amber-700">Страницы среднего риска</div>
                            <div class="text-xl font-semibold text-amber-800">${escapeHtml(aiTotals.mediumRiskPages)}</div>
                        </div>
                        <div class="rounded-lg border border-indigo-200 bg-indigo-50 p-3">
                            <div class="text-indigo-700">Срабатывания маркеров</div>
                            <div class="text-xl font-semibold text-indigo-800">${escapeHtml(aiTotals.markerHits)}</div>
                        </div>
                        <div class="rounded-lg border border-slate-200 bg-slate-50 p-3">
                            <div class="text-slate-700">Средний риск / плотность</div>
                            <div class="text-xl font-semibold text-slate-900">${escapeHtml(aiTotals.avgRisk)} / ${escapeHtml(aiTotals.avgDensity)}</div>
                        </div>
                    </div>
                    <div class="mt-2 text-xs text-slate-500">Защита от false-positive активна на ${escapeHtml(aiTotals.guardedPages)} страницах.</div>
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Давление на качество контента</h4>
                    <div class="grid grid-cols-2 gap-3 text-sm">
                        <div class="rounded-lg border border-amber-200 bg-amber-50 p-3">
                            <div class="text-amber-700">Низкая уникальность текста</div>
                            <div class="text-xl font-semibold text-amber-800">${escapeHtml(contentSignals.lowUnique)}</div>
                        </div>
                        <div class="rounded-lg border border-rose-200 bg-rose-50 p-3">
                            <div class="text-rose-700">Много boilerplate-контента</div>
                            <div class="text-xl font-semibold text-rose-800">${escapeHtml(contentSignals.highBoilerplate)}</div>
                        </div>
                        <div class="rounded-lg border border-rose-200 bg-rose-50 p-3">
                            <div class="text-rose-700">Риск keyword stuffing</div>
                            <div class="text-xl font-semibold text-rose-800">${escapeHtml(contentSignals.keywordStuffing)}</div>
                        </div>
                        <div class="rounded-lg border border-sky-200 bg-sky-50 p-3">
                            <div class="text-sky-700">Флаги скрытого контента</div>
                            <div class="text-xl font-semibold text-sky-800">${escapeHtml(contentSignals.hiddenContent)}</div>
                        </div>
                        <div class="rounded-lg border border-amber-200 bg-amber-50 p-3">
                            <div class="text-amber-700">Высокая доля filler-текста</div>
                            <div class="text-xl font-semibold text-amber-800">${escapeHtml(contentSignals.highFiller)}</div>
                        </div>
                        <div class="rounded-lg border border-rose-200 bg-rose-50 p-3">
                            <div class="text-rose-700">Флаги токсичных формулировок</div>
                            <div class="text-xl font-semibold text-rose-800">${escapeHtml(contentSignals.toxicContent)}</div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <h4 class="font-semibold mb-3">Метрики пайплайна</h4>
                <table class="w-full min-w-[760px]">
                    <thead>
                        <tr class="text-left text-xs text-slate-500 border-b">
                            <th class="py-2 pr-2">Метрика</th>
                            <th class="py-2 pr-2">Текущее</th>
                            <th class="py-2 pr-2">Цель</th>
                            <th class="py-2 pr-2">Статус</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${pipelineRowsHtml}
                    </tbody>
                </table>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Краткий риск-снимок</h4>
                    <div class="grid grid-cols-2 gap-3 text-sm">
                        <div class="rounded-lg border border-rose-200 bg-rose-50 p-3">
                            <div class="text-rose-700">Критичные страницы</div>
                            <div class="text-xl font-semibold text-rose-800">${escapeHtml(criticalPagesAllCount)}</div>
                            <div class="text-xs text-rose-700">${escapeHtml(criticalPagesPct)}% от просканированных страниц</div>
                        </div>
                        <div class="rounded-lg border border-amber-200 bg-amber-50 p-3">
                            <div class="text-amber-700">Параметризованные URL</div>
                            <div class="text-xl font-semibold text-amber-800">${escapeHtml(crawlBudgetSummary.parameterized_urls ?? 0)}</div>
                            <div class="text-xs text-amber-700">Потенциальная трата crawl budget</div>
                        </div>
                        <div class="rounded-lg border border-sky-200 bg-sky-50 p-3">
                            <div class="text-sky-700">Глубокие URL</div>
                            <div class="text-xl font-semibold text-sky-800">${escapeHtml(crawlBudgetSummary.deep_path_urls ?? 0)}</div>
                            <div class="text-xs text-sky-700">Depth >= 4</div>
                        </div>
                        <div class="rounded-lg border border-slate-200 bg-slate-50 p-3">
                            <div class="text-slate-700">Высокий / средний риск сканирования</div>
                            <div class="text-xl font-semibold text-slate-900">${escapeHtml(crawlBudgetSummary.high_risk_urls ?? 0)} / ${escapeHtml(crawlBudgetSummary.medium_risk_urls ?? 0)}</div>
                            <div class="text-xs text-slate-600">По модели crawl budget</div>
                        </div>
                    </div>
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Снимок безопасности главной</h4>
                    <div class="grid grid-cols-2 gap-3 text-sm">
                        <div><span class="text-gray-600">Оценка заголовков:</span> <span class="font-medium">${escapeHtml(homepageSecurity.security_headers_score ?? 'н/д')}</span></div>
                        <div><span class="text-gray-600">Смешанный контент:</span> <span class="font-medium">${escapeHtml(homepageSecurity.mixed_content_count ?? 0)}</span></div>
                        <div><span class="text-gray-600">CSP:</span> <span class="font-medium">${boolLabel(homepageSecurity.csp_present)}</span></div>
                        <div><span class="text-gray-600">HSTS:</span> <span class="font-medium">${boolLabel(homepageSecurity.hsts_present)}</span></div>
                        <div><span class="text-gray-600">X-Frame-Options:</span> <span class="font-medium">${boolLabel(homepageSecurity.x_frame_options_present)}</span></div>
                        <div><span class="text-gray-600">Referrer-Policy:</span> <span class="font-medium">${boolLabel(homepageSecurity.referrer_policy_present)}</span></div>
                        <div><span class="text-gray-600">Permissions-Policy:</span> <span class="font-medium">${boolLabel(homepageSecurity.permissions_policy_present)}</span></div>
                        <div><span class="text-gray-600">URL:</span> <span class="font-medium break-all">${escapeHtml(homepageSecurity.url || result.url || '-')}</span></div>
                    </div>
                </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                    <h4 class="font-semibold mb-3">Обзор типов проблем</h4>
                    <table class="w-full min-w-[620px]">
                        <thead>
                            <tr class="text-left text-xs text-slate-500 border-b">
                                <th class="py-2 pr-2">Код</th>
                                <th class="py-2 pr-2">Всего</th>
                                <th class="py-2 pr-2">Доля</th>
                                <th class="py-2 pr-2">Критично</th>
                                <th class="py-2 pr-2">Предупреждение</th>
                                <th class="py-2 pr-2">Инфо</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${issueTypeRowsHtml || '<tr><td colspan="6" class="py-3 text-sm text-slate-500">В payload нет кодов проблем.</td></tr>'}
                        </tbody>
                    </table>
                </div>
                <div class="space-y-6">
                    <div class="bg-white rounded-xl shadow-md p-6">
                        <h4 class="font-semibold mb-3">Кластеры первопричин</h4>
                        ${clusterRowsHtml || '<div class="text-sm text-slate-500">Кластеры недоступны.</div>'}
                    </div>
                    <div class="bg-white rounded-xl shadow-md p-6">
                        <h4 class="font-semibold mb-3">Распределение по владельцам</h4>
                        ${ownerRowsHtml || '<div class="text-sm text-slate-500">Нет разбивки по владельцам.</div>'}
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <h4 class="font-semibold mb-3">Распределение семейств проблем</h4>
                <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
                    ${issueFamilyCardsHtml || '<div class="text-sm text-slate-500">Нет статистики по семействам проблем.</div>'}
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                <h4 class="font-semibold mb-3">План действий по типам проблем</h4>
                <table class="w-full min-w-[980px]">
                    <thead>
                        <tr class="text-left text-xs text-slate-500 border-b">
                            <th class="py-2 pr-2">#</th>
                            <th class="py-2 pr-2">Код проблемы</th>
                            <th class="py-2 pr-2">Критичность</th>
                            <th class="py-2 pr-2">Затронутые страницы</th>
                            <th class="py-2 pr-2">Влияние</th>
                            <th class="py-2 pr-2">Владелец</th>
                            <th class="py-2 pr-2">Спринт</th>
                            <th class="py-2 pr-2">Рекомендация</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${actionPlanRowsHtml || '<tr><td colspan="8" class="py-3 text-sm text-slate-500">Нет строк плана действий.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                <h4 class="font-semibold mb-3">Топ критичных страниц</h4>
                <table class="w-full min-w-[680px]">
                    <thead>
                        <tr class="text-left text-xs text-slate-500 border-b">
                            <th class="py-2 pr-2">URL</th>
                            <th class="py-2 pr-2">Критично</th>
                            <th class="py-2 pr-2">Предупреждение</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${criticalPagesRowsHtml || '<tr><td colspan="3" class="py-3 text-sm text-slate-500">Критичные/предупредительные страницы не найдены.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                <h4 class="font-semibold mb-3">Топ страниц для исправления</h4>
                <table class="w-full min-w-[720px]">
                    <thead>
                        <tr class="text-left text-xs text-slate-500 border-b">
                            <th class="py-2 pr-2">URL</th>
                            <th class="py-2 pr-2">Состояние</th>
                            <th class="py-2 pr-2">Проблемы</th>
                            <th class="py-2 pr-2">Рекомендация</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${topFixRowsHtml || '<tr><td colspan="4" class="py-3 text-sm text-slate-500">Нет постраничных данных.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Риски crawl budget</h4>
                    <div class="grid grid-cols-2 gap-3 text-sm">
                        <div class="rounded-lg border border-rose-200 bg-rose-50 p-3">
                            <div class="text-rose-700">Страницы высокого риска</div>
                            <div class="text-xl font-semibold text-rose-800">${escapeHtml(crawlRiskHigh)}</div>
                        </div>
                        <div class="rounded-lg border border-amber-200 bg-amber-50 p-3">
                            <div class="text-amber-700">Страницы среднего риска</div>
                            <div class="text-xl font-semibold text-amber-800">${escapeHtml(crawlRiskMedium)}</div>
                        </div>
                    </div>
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Группы дублей</h4>
                    <div class="grid grid-cols-2 gap-3 text-sm">
                        <div class="rounded-lg border bg-slate-50 p-3 text-center">
                            <div class="text-slate-500 text-xs">Группы title</div>
                            <div class="text-lg font-semibold">${escapeHtml(duplicateTitleGroups.length)}</div>
                        </div>
                        <div class="rounded-lg border bg-slate-50 p-3 text-center">
                            <div class="text-slate-500 text-xs">Группы description</div>
                            <div class="text-lg font-semibold">${escapeHtml(duplicateDescriptionGroups.length)}</div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Топ дублей title</h4>
                    ${duplicateTitleHtml || '<div class="text-sm text-slate-500">Нет групп дублей title.</div>'}
                </div>
                <div class="bg-white rounded-xl shadow-md p-6">
                    <h4 class="font-semibold mb-3">Топ дублей description</h4>
                    ${duplicateDescriptionHtml || '<div class="text-sm text-slate-500">Нет групп дублей description.</div>'}
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6 overflow-auto">
                <h4 class="font-semibold mb-3">Spam Detector: термины и фразы</h4>
                <table class="w-full min-w-[760px]">
                    <thead>
                        <tr class="text-left text-xs text-slate-500 border-b">
                            <th class="py-2 pr-2">Термин / фраза</th>
                            <th class="py-2 pr-2">Тип сигнала</th>
                            <th class="py-2 pr-2">Страницы</th>
                            <th class="py-2 pr-2">Доля</th>
                            <th class="py-2 pr-2">Оценка риска</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${spamRowsHtml || '<tr><td colspan="5" class="py-3 text-sm text-slate-500">В текущем payload спам-сигналы не обнаружены.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <h4 class="font-semibold mb-3">Роадмап: Сейчас / Далее / Позже</h4>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
                    <div class="rounded-lg border border-rose-200 bg-rose-50 p-3">
                        <div class="font-semibold text-rose-800 mb-2">Сейчас</div>
                        <ul class="list-disc pl-4">${roadmapList(nowRoadmap, 'Критичные действия не запланированы.')}</ul>
                    </div>
                    <div class="rounded-lg border border-amber-200 bg-amber-50 p-3">
                        <div class="font-semibold text-amber-800 mb-2">Далее</div>
                        <ul class="list-disc pl-4">${roadmapList(nextRoadmap, 'Действия по предупреждениям не запланированы.')}</ul>
                    </div>
                    <div class="rounded-lg border border-sky-200 bg-sky-50 p-3">
                        <div class="font-semibold text-sky-800 mb-2">Позже</div>
                        <ul class="list-disc pl-4">${roadmapList(laterRoadmap, 'Действия в backlog не запланированы.')}</ul>
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <h4 class="font-semibold mb-2">Практические рекомендации</h4>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    ${recommendationCardsHtml || '<div class="text-sm text-slate-500">Рекомендации недоступны.</div>'}
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md p-6">
                <h4 class="font-semibold mb-2">Скачивания</h4>
                ${artifactsMeta.payload_compacted ? `
                    <div class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 mb-3">
                        Включен компактный режим данных. Полные данные доступны через скачивание частей ниже.
                    </div>
                    <div class="text-xs text-gray-600 mb-3">
                        Пропущено в inline данных:
                        проблемы=${escapeHtml((artifactsMeta.omitted_counts || {}).issues ?? 0)},
                        семантика=${escapeHtml((artifactsMeta.omitted_counts || {}).semantic_linking_map ?? 0)},
                        страницы=${escapeHtml((artifactsMeta.omitted_counts || {}).pages ?? 0)}
                    </div>
                ` : ''}
                ${downloadsHtml || '<div class="text-sm text-gray-500">Chunk-артефакты для этого запуска недоступны.</div>'}
            </div>
        </div>
    `;
}

// ---------------------------------------------------------------------------
// Unified Full SEO Audit — results renderer
// ---------------------------------------------------------------------------
function _unifiedScoreColor(score) {
    const s = Number(score || 0);
    if (s >= 90) return '#10b981';
    if (s >= 70) return '#3b82f6';
    if (s >= 50) return '#f59e0b';
    return '#ef4444';
}

function _unifiedGradeColor(grade) {
    const g = String(grade || '').toUpperCase();
    if (g === 'A+' || g === 'A') return '#10b981';
    if (g === 'B') return '#3b82f6';
    if (g === 'C') return '#f59e0b';
    return '#ef4444';
}

function _unifiedPriorityBadge(priority) {
    const p = String(priority || '').toUpperCase();
    if (p === 'P0') return 'ds-badge ds-badge-danger';
    if (p === 'P1') return 'ds-badge ds-badge-warning';
    if (p === 'P2') return 'ds-badge ds-badge-info';
    return 'ds-badge';
}

function generateUnifiedAuditHTML(result) {
    // result may be the direct run_unified_audit() output (has overall_score at top level)
    // or wrapped in .results / .result by the task store
    const r = (result.overall_score != null) ? result
            : (result.results && result.results.overall_score != null) ? result.results
            : (result.result && result.result.overall_score != null) ? result.result
            : result;
    const overallScore = Number(r.overall_score ?? 0);
    const overallGrade = r.overall_grade || '';
    const durationMs = Number(r.duration_ms ?? 0);
    const toolsRun = Number(r.tools_run ?? 0);
    const toolsFailed = Number(r.tools_failed ?? 0);
    const toolResults = r.results || r.tool_results || r.per_tool || {};
    const scores = { ...(r.scores || {}) };
    const devTasks = Array.isArray(r.dev_tasks) ? r.dev_tasks : [];
    const errors = r.errors || {};
    const tid = result.task_id || taskId || '';
    const url = result.url || r.url || '';

    const durationSec = (durationMs / 1000).toFixed(1);

    const robotsPayload = toolResults.robots?.results || toolResults.robots || {};
    if (toolResults.robots) {
        const robotsQuality = Number(robotsPayload.quality_score ?? NaN);
        scores.robots_ok = Number.isFinite(robotsQuality) ? robotsQuality : (robotsPayload.robots_txt_found ? 100 : 0);
    }
    const mobilePayload = toolResults.mobile?.results || toolResults.mobile || {};
    if (toolResults.mobile) {
        const mobileScore = Number(mobilePayload.score ?? mobilePayload.summary?.score ?? NaN);
        if (Number.isFinite(mobileScore)) scores.mobile_friendly = mobileScore;
    }
    const redirectPayload = toolResults.redirect?.results || toolResults.redirect || {};
    if (toolResults.redirect) {
        const redirectScore = Number(redirectPayload.summary?.quality_score ?? NaN);
        if (Number.isFinite(redirectScore)) scores.redirect = redirectScore;
    }
    const cwvPayload = toolResults.cwv?.results || toolResults.cwv || {};
    if (toolResults.cwv) {
        if (cwvPayload.combined) {
            const mobilePerf = Number(cwvPayload.mobile?.summary?.performance_score ?? cwvPayload.mobile?.categories?.performance ?? NaN);
            const desktopPerf = Number(cwvPayload.desktop?.summary?.performance_score ?? cwvPayload.desktop?.categories?.performance ?? NaN);
            if (Number.isFinite(mobilePerf)) scores.cwv_mobile = mobilePerf;
            if (Number.isFinite(desktopPerf)) scores.cwv_desktop = desktopPerf;
            const avgParts = [scores.cwv_mobile, scores.cwv_desktop].filter(v => Number.isFinite(Number(v)));
            if (avgParts.length > 0) scores.cwv_avg = Math.round((avgParts.reduce((a, b) => Number(a) + Number(b), 0) / avgParts.length) * 10) / 10;
        } else {
            const perf = Number(cwvPayload.summary?.performance_score ?? cwvPayload.categories?.performance ?? NaN);
            if (Number.isFinite(perf)) scores.cwv_avg = perf;
        }
    }

    // --- Header with overall score ring ---
    const headerHtml = buildToolHeader({
        gradient: 'from-indigo-600 to-blue-700',
        label: 'Unified Full SEO Audit',
        title: 'Комплексный SEO-аудит',
        subtitle: url ? escapeHtml(url) : '',
        score: overallScore,
        scoreLabel: 'оценка',
        scoreGrade: overallGrade,
        badges: [
            { text: `Инструментов: ${toolsRun}`, cls: 'bg-white/20 text-white' },
            toolsFailed > 0
                ? { text: `Ошибок: ${toolsFailed}`, cls: 'bg-red-500/30 text-white' }
                : { text: 'Без ошибок', cls: 'bg-emerald-500/30 text-white' },
            { text: `${durationSec}s`, cls: 'bg-white/20 text-white' }
        ],
        metaLines: [],
        actionButtons: `
            <button onclick="downloadUnifiedAuditExport('xlsx')" class="ds-export-btn" aria-label="Скачать XLSX отчет">
                <i class="fas fa-file-excel mr-1"></i>XLSX
            </button>
            <button onclick="downloadUnifiedAuditExport('docx')" class="ds-export-btn" aria-label="Скачать DOCX отчет">
                <i class="fas fa-file-word mr-1"></i>DOCX
            </button>`
    });

    // --- Overall grade display ---
    const gradeColor = _unifiedGradeColor(overallGrade);
    const gradeHtml = overallGrade ? `
    <div class="ds-card text-center" style="padding:1.5rem;">
        <div style="font-size:4rem;font-weight:800;color:${gradeColor};line-height:1;">${escapeHtml(overallGrade)}</div>
        <div class="text-sm" style="color:var(--ds-text-secondary);margin-top:0.5rem;">Общая оценка</div>
        <div class="text-2xl font-bold" style="color:${_unifiedScoreColor(overallScore)};margin-top:0.25rem;">${overallScore.toFixed(1)}</div>
    </div>` : '';

    // --- Chart canvases ---
    const chartsHtml = `
    <div class="ds-card" style="padding:1.25rem;">
        <h4 class="font-semibold mb-3" style="color:var(--ds-text);">Результаты по инструментам</h4>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div><canvas id="ds-chart-unified-overall" width="200" height="200"></canvas></div>
            <div><canvas id="ds-chart-unified-scores" width="400" height="200"></canvas></div>
        </div>
    </div>`;

    // --- Scores grid ---
    const scoreNames = {
        onpage: 'OnPage Audit',
        render: 'Render Audit',
        mobile_friendly: 'Mobile Friendly',
        bot_accessibility: 'Bot Accessibility',
        redirect: 'Redirect Checker',
        cwv_mobile: 'CWV Mobile',
        cwv_desktop: 'CWV Desktop',
        cwv_avg: 'CWV Average',
        robots_ok: 'Robots.txt'
    };
    const scoreKeys = Object.keys(scores);
    const scoresGridHtml = scoreKeys.length > 0 ? `
    <div class="ds-card" style="padding:1.25rem;">
        <h4 class="font-semibold mb-3" style="color:var(--ds-text);">Оценки по модулям</h4>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
            ${scoreKeys.map(k => {
                const s = Number(scores[k] || 0);
                const label = scoreNames[k] || k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                const isCwvError = (k === 'cwv_mobile' || k === 'cwv_desktop' || k === 'cwv_avg') && s === 0 && (errors['cwv'] || errors['core_web_vitals']);
                const displayVal = isCwvError ? 'N/A' : s;
                const displayColor = isCwvError ? 'var(--ds-text-muted)' : _unifiedScoreColor(s);
                return `<div class="ds-card text-center" style="padding:1rem;animation:none;">
                    <div class="text-3xl font-bold" style="color:${displayColor}">${displayVal}</div>
                    <div class="text-sm" style="color:var(--ds-text-secondary);">${escapeHtml(label)}</div>
                </div>`;
            }).join('')}
        </div>
    </div>` : '';

    // --- Developer Tasks table (ТЗ) ---
    let devTasksHtml = '';
    if (devTasks.length > 0) {
        const rows = devTasks.map((t, i) => `
            <tr>
                <td style="padding:0.5rem;white-space:nowrap;">${i + 1}</td>
                <td style="padding:0.5rem;"><span class="${_unifiedPriorityBadge(t.priority)}">${escapeHtml(String(t.priority || ''))}</span></td>
                <td style="padding:0.5rem;">${escapeHtml(String(t.category || ''))}</td>
                <td style="padding:0.5rem;">${escapeHtml(String(t.source_tool || ''))}</td>
                <td style="padding:0.5rem;font-weight:500;">${escapeHtml(String(t.title || ''))}</td>
                <td style="padding:0.5rem;font-size:0.85em;color:var(--ds-text-secondary);">${escapeHtml(String(t.description || ''))}</td>
                <td style="padding:0.5rem;">${escapeHtml(String(t.owner || ''))}</td>
            </tr>`).join('');

        const p0Count = devTasks.filter(t => String(t.priority).toUpperCase() === 'P0').length;
        const p1Count = devTasks.filter(t => String(t.priority).toUpperCase() === 'P1').length;
        const p2Count = devTasks.filter(t => String(t.priority).toUpperCase() === 'P2').length;
        const p3Count = devTasks.filter(t => String(t.priority).toUpperCase() === 'P3').length;

        devTasksHtml = `
        <div class="ds-card" style="padding:1.25rem;">
            <div class="flex items-center justify-between mb-3 flex-wrap gap-2">
                <h4 class="font-semibold" style="color:var(--ds-text);">Техническое задание (${devTasks.length} задач)</h4>
                <div class="flex gap-2 text-xs">
                    ${p0Count ? `<span class="ds-badge ds-badge-danger">P0: ${p0Count}</span>` : ''}
                    ${p1Count ? `<span class="ds-badge ds-badge-warning">P1: ${p1Count}</span>` : ''}
                    ${p2Count ? `<span class="ds-badge ds-badge-info">P2: ${p2Count}</span>` : ''}
                    ${p3Count ? `<span class="ds-badge">P3: ${p3Count}</span>` : ''}
                </div>
            </div>
            <div class="ds-table-wrap">
                <table class="ds-table">
                    <thead>
                        <tr>
                            <th>#</th><th>Priority</th><th>Category</th><th>Tool</th>
                            <th>Title</th><th>Description</th><th>Owner</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        </div>`;
    }

    // --- Errors section ---
    const errorKeys = Object.keys(errors);
    let errorsHtml = '';
    if (errorKeys.length > 0) {
        const errRows = errorKeys.map(k => `
            <tr>
                <td style="padding:0.5rem;font-weight:500;">${escapeHtml(k)}</td>
                <td style="padding:0.5rem;color:var(--ds-danger);">${escapeHtml(String(errors[k] || ''))}</td>
            </tr>`).join('');
        errorsHtml = `
        <div class="ds-card" style="padding:1.25rem;">
            <h4 class="font-semibold mb-3" style="color:var(--ds-danger);">Ошибки выполнения</h4>
            <div class="ds-table-wrap">
                <table class="ds-table">
                    <thead><tr><th>Инструмент</th><th>Ошибка</th></tr></thead>
                    <tbody>${errRows}</tbody>
                </table>
            </div>
        </div>`;
    }

    // --- Cross-tool insights (synergy) ---
    let insightsHtml = '';
    const insights = [];

    // Check for common patterns across tools
    if (scores.render === 0 || scores.render < 50) {
        if (scores.onpage > 70) insights.push({icon: 'exclamation-triangle', color: 'var(--ds-warning)', text: 'Контент хорошего качества, но рендеринг проблемный — боты могут не увидеть контент.'});
    }
    if (scores.mobile_friendly < 100 && scores.cwv_avg && scores.cwv_avg < 50) {
        insights.push({icon: 'mobile-alt', color: 'var(--ds-danger)', text: 'Мобильная версия и Core Web Vitals ниже нормы — Google может понизить позиции (Mobile-First Indexing).'});
    }
    if (scores.bot_accessibility < 80) {
        insights.push({icon: 'robot', color: 'var(--ds-danger)', text: 'Часть ботов не может получить доступ к сайту — проверьте robots.txt и серверную конфигурацию.'});
    }
    if (scores.redirect < 60) {
        insights.push({icon: 'exchange-alt', color: 'var(--ds-warning)', text: 'Много проблем с редиректами — это тратит краулинговый бюджет и может вызвать проблемы с индексацией.'});
    }
    if (scores.robots_ok === 0) {
        insights.push({icon: 'file-alt', color: 'var(--ds-danger)', text: 'robots.txt не найден — поисковые системы не получают инструкций по краулингу.'});
    }
    if (scores.onpage > 80 && scores.cwv_avg > 80 && scores.mobile_friendly >= 100) {
        insights.push({icon: 'check-circle', color: 'var(--ds-success)', text: 'Контент, скорость и мобильность в хорошем состоянии — сильная техническая база.'});
    }

    if (insights.length > 0) {
        insightsHtml = `
        <div class="ds-card" style="padding:1.25rem;border-left:4px solid var(--ds-brand);">
            <h4 class="font-semibold mb-3" style="color:var(--ds-text);"><i class="fas fa-lightbulb mr-2" style="color:var(--ds-brand);"></i>Кросс-инструментная аналитика</h4>
            <div class="space-y-2">
                ${insights.map(ins => `
                    <div class="flex items-start gap-3 text-sm">
                        <i class="fas fa-${ins.icon} mt-0.5" style="color:${ins.color};"></i>
                        <span style="color:var(--ds-text);">${ins.text}</span>
                    </div>`).join('')}
            </div>
        </div>`;
    }

    // --- Per-tool detailed results ---

    function _resolveUnifiedToolScore(toolKey, data, rd) {
        if (toolKey === 'robots') return Number(rd.quality_score ?? data.quality_score ?? scores[toolKey] ?? 0);
        if (toolKey === 'sitemap') return Number(rd.quality_score ?? data.quality_score ?? scores[toolKey] ?? 0);
        if (toolKey === 'mobile') return Number(rd.score ?? rd.summary?.score ?? scores.mobile_friendly ?? 0);
        if (toolKey === 'redirect') return Number(rd.summary?.quality_score ?? scores[toolKey] ?? 0);
        if (toolKey === 'cwv') {
            if (rd.combined) {
                const mobilePerf = Number(rd.mobile?.summary?.performance_score ?? rd.mobile?.categories?.performance ?? 0);
                const desktopPerf = Number(rd.desktop?.summary?.performance_score ?? rd.desktop?.categories?.performance ?? 0);
                const parts = [mobilePerf, desktopPerf].filter(v => v > 0);
                return parts.length ? Math.round(parts.reduce((a, b) => a + b, 0) / parts.length) : Number(scores.cwv_avg ?? 0);
            }
            return Number(rd.summary?.performance_score ?? rd.categories?.performance ?? scores.cwv_avg ?? 0);
        }
        return scores[toolKey];
    }

    function _renderToolSummary(toolKey, toolName, data) {
        if (!data || typeof data !== 'object') return '';
        const rd = data.results || data;
        const score = _resolveUnifiedToolScore(toolKey, data, rd);
        const scoreColor = typeof score === 'number' ? _unifiedScoreColor(score) : 'var(--ds-text-muted)';
        const scoreDisplay = score !== undefined && score !== null ? score : '—';

        let metricsHtml = '';
        let issuesHtml = '';
        let detailsHtml = '';

        // Extract results — may be nested under .results
        if (toolKey === 'robots') {
            const found = rd.robots_txt_found ?? data.robots_txt_found;
            const rulesCount = rd.total_rules ?? (Number(rd.disallow_rules || 0) + Number(rd.allow_rules || 0)) ?? rd.findings?.total_rules ?? 0;
            const sitemapsCount = Array.isArray(rd.sitemaps) ? rd.sitemaps.length : (rd.sitemaps_found ?? rd.findings?.total_sitemaps ?? 0);
            metricsHtml = `
                <div class="grid grid-cols-3 gap-3 mb-3">
                    <div class="text-center"><div class="text-xl font-bold" style="color:${found ? 'var(--ds-success)' : 'var(--ds-danger)'}">${found ? 'Найден' : 'Не найден'}</div><div class="text-xs" style="color:var(--ds-text-muted)">robots.txt</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${rulesCount}</div><div class="text-xs" style="color:var(--ds-text-muted)">Правила</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${sitemapsCount}</div><div class="text-xs" style="color:var(--ds-text-muted)">Sitemaps</div></div>
                </div>`;
            const robotItems = [
                ...(Array.isArray(rd.issues) ? rd.issues : []),
                ...(Array.isArray(rd.warnings) ? rd.warnings : []),
                ...(Array.isArray(rd.recommendations) ? rd.recommendations : []),
            ].slice(0, 6);
            if (robotItems.length > 0) {
                issuesHtml = `<ul class="text-sm space-y-1" style="color:var(--ds-text-secondary);">${robotItems.map(rec => `<li>• ${escapeHtml(typeof rec === 'string' ? rec : rec.text || rec.title || rec.message || JSON.stringify(rec))}</li>`).join('')}</ul>`;
            }
        }

        else if (toolKey === 'sitemap') {
            const totalUrls = rd.urls_count ?? rd.total_urls ?? rd.summary?.total_urls ?? 0;
            const filesCount = rd.sitemaps_scanned ?? rd.files_checked ?? rd.summary?.files_checked ?? 0;
            const sitemapValid = rd.valid ?? rd.summary?.valid;
            const sitemapGrade = rd.quality_grade ?? rd.summary?.quality_grade ?? '';
            const status = sitemapValid === true ? 'valid' : sitemapValid === false ? 'invalid' : (sitemapGrade || 'unknown');
            metricsHtml = `
                <div class="grid grid-cols-3 gap-3 mb-3">
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${Number(totalUrls).toLocaleString()}</div><div class="text-xs" style="color:var(--ds-text-muted)">URL в sitemap</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${filesCount}</div><div class="text-xs" style="color:var(--ds-text-muted)">Файлов</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:${status === 'valid' ? 'var(--ds-success)' : status === 'invalid' ? 'var(--ds-danger)' : 'var(--ds-warning)'}">${status}</div><div class="text-xs" style="color:var(--ds-text-muted)">Статус</div></div>
                </div>`;
            const sitemapMessages = [
                ...(Array.isArray(rd.errors) ? rd.errors : []),
                ...(Array.isArray(rd.warnings) ? rd.warnings : []),
                ...(sitemapValid ? ((Array.isArray(rd.highlights) ? rd.highlights : []).slice(0, 3)) : []),
            ].slice(0, 6);
            if (sitemapMessages.length > 0) {
                issuesHtml = `<ul class="text-sm space-y-1" style="color:var(--ds-text-secondary);">${sitemapMessages.map(item => `<li>• ${escapeHtml(typeof item === 'string' ? item : item.text || item.title || item.message || JSON.stringify(item))}</li>`).join('')}</ul>`;
            }
        }

        else if (toolKey === 'onpage') {
            const opScore = rd.score ?? rd.scores?.onpage_score ?? rd.summary?.score ?? 0;
            const wordCount = rd.content?.word_count ?? 0;
            const kwCoverage = rd.keyword_coverage?.coverage_pct ?? rd.summary?.keyword_coverage_pct ?? 0;
            const titleLen = rd.title?.length ?? rd.technical?.title_len ?? 0;
            const descLen = rd.description?.length ?? rd.technical?.description_len ?? 0;
            const aiRisk = rd.ai_insights?.ai_risk_composite ?? rd.summary?.ai_risk_composite ?? 0;
            metricsHtml = `
                <div class="grid grid-cols-3 md:grid-cols-6 gap-3 mb-3">
                    <div class="text-center"><div class="text-xl font-bold" style="color:${_unifiedScoreColor(opScore)}">${opScore}</div><div class="text-xs" style="color:var(--ds-text-muted)">Score</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${wordCount}</div><div class="text-xs" style="color:var(--ds-text-muted)">Слов</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${kwCoverage}%</div><div class="text-xs" style="color:var(--ds-text-muted)">Ключи</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:${titleLen > 30 && titleLen < 60 ? 'var(--ds-success)' : 'var(--ds-warning)'}">${titleLen}</div><div class="text-xs" style="color:var(--ds-text-muted)">Title</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:${descLen > 120 && descLen < 160 ? 'var(--ds-success)' : 'var(--ds-warning)'}">${descLen}</div><div class="text-xs" style="color:var(--ds-text-muted)">Desc</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:${aiRisk < 30 ? 'var(--ds-success)' : aiRisk < 60 ? 'var(--ds-warning)' : 'var(--ds-danger)'}">${Math.round(aiRisk)}</div><div class="text-xs" style="color:var(--ds-text-muted)">AI Risk</div></div>
                </div>`;
            // SERP preview if available
            const serp = rd.serp_preview;
            if (serp && serp.google) {
                detailsHtml += `
                <div class="mb-3 p-3 rounded-lg" style="background:var(--ds-bg);border:1px solid var(--ds-border);">
                    <div class="text-xs font-medium mb-2" style="color:var(--ds-text-muted);">SERP Preview (Google)</div>
                    <div style="font-family:arial,sans-serif;">
                        <div style="color:#1a0dab;font-size:18px;line-height:1.3;">${escapeHtml(serp.google.title || '')}</div>
                        <div style="color:#006621;font-size:13px;">${escapeHtml(serp.google.breadcrumb || '')}</div>
                        <div style="color:#545454;font-size:13px;line-height:1.4;">${escapeHtml(serp.google.description || '')}</div>
                    </div>
                </div>`;
            }
            const opIssues = rd.issues || [];
            if (opIssues.length > 0) {
                issuesHtml = _renderIssuesList(opIssues, 8);
            }
        }

        else if (toolKey === 'render') {
            const renderSummary = rd.summary || {};
            const renderVariants = Array.isArray(rd.variants) ? rd.variants : [];
            const renderScore = renderSummary.score ?? rd.comparison?.score ?? rd.score ?? 0;
            const allFrameworks = renderVariants.reduce((acc, v) => { (v.js_frameworks || []).forEach(f => { if (!acc.includes(f)) acc.push(f); }); return acc; }, []);
            const frameworks = allFrameworks.length > 0 ? allFrameworks : (rd.js_frameworks || rd.rendered_snapshot?.js_frameworks || []);
            const missingCount = renderSummary.missing_total ?? (rd.comparison?.missing ? Object.values(rd.comparison.missing).reduce((s,a) => s + (Array.isArray(a) ? a.length : 0), 0) : 0);
            const consoleErrors = renderVariants.reduce((s, v) => s + (v.console_log?.error_count ?? 0), 0) || (rd.console_log?.error_count ?? rd.rendered_snapshot?.console_log?.error_count ?? 0);
            metricsHtml = `
                <div class="grid grid-cols-4 gap-3 mb-3">
                    <div class="text-center"><div class="text-xl font-bold" style="color:${_unifiedScoreColor(renderScore)}">${Math.round(renderScore)}</div><div class="text-xs" style="color:var(--ds-text-muted)">Score</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${missingCount}</div><div class="text-xs" style="color:var(--ds-text-muted)">JS-only</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:${consoleErrors > 0 ? 'var(--ds-danger)' : 'var(--ds-success)'}">${consoleErrors}</div><div class="text-xs" style="color:var(--ds-text-muted)">JS Errors</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-brand)">${frameworks.length > 0 ? frameworks.join(', ') : '—'}</div><div class="text-xs" style="color:var(--ds-text-muted)">Framework</div></div>
                </div>`;
            const renderIssues = Array.isArray(rd.issues) ? rd.issues : [];
            // Deduplicate render issues (desktop + mobile variants may duplicate)
            const seenRender = new Set();
            const renderFailedIssues = renderIssues.filter(i => { if (i.severity !== 'critical' && i.severity !== 'warning') return false; const t = i.title || i.code || ''; if (seenRender.has(t)) return false; seenRender.add(t); return true; });
            if (renderFailedIssues.length > 0) {
                issuesHtml = `<div class="space-y-1">${renderFailedIssues.slice(0,8).map(i => `
                    <div class="flex items-center gap-2 text-sm">
                        <span class="ds-badge ${i.severity === 'critical' ? 'ds-badge-danger' : 'ds-badge-warning'}" style="font-size:0.65rem;">${i.severity}</span>
                        <span style="color:var(--ds-text);">${escapeHtml(i.title || i.code || '')}</span>
                    </div>`).join('')}</div>`;
            }
        }

        else if (toolKey === 'mobile') {
            const mobileFriendly = rd.mobile_friendly ?? false;
            const devicesCount = rd.devices_tested?.length ?? rd.device_results?.length ?? 0;
            const totalIssues = rd.issues_count ?? (rd.issues || []).length;
            metricsHtml = `
                <div class="grid grid-cols-3 gap-3 mb-3">
                    <div class="text-center"><div class="text-xl font-bold" style="color:${mobileFriendly ? 'var(--ds-success)' : 'var(--ds-danger)'}">${mobileFriendly ? 'Да' : 'Нет'}</div><div class="text-xs" style="color:var(--ds-text-muted)">Mobile-friendly</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${devicesCount}</div><div class="text-xs" style="color:var(--ds-text-muted)">Устройств</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:${totalIssues > 0 ? 'var(--ds-warning)' : 'var(--ds-success)'}">${totalIssues}</div><div class="text-xs" style="color:var(--ds-text-muted)">Проблем</div></div>
                </div>`;
            // Deduplicate mobile issues (portrait + landscape duplicates)
            const seenMobile = new Set();
            const dedupMobile = (rd.issues || []).filter(i => { const t = i.title || i.code || ''; if (seenMobile.has(t)) return false; seenMobile.add(t); return true; });
            if (dedupMobile.length > 0) issuesHtml = _renderIssuesList(dedupMobile, 6);
        }

        else if (toolKey === 'bot_check') {
            const botSummary = rd.summary || {};
            const accessible = botSummary.accessible ?? 0;
            const nonIndexable = botSummary.non_indexable ?? 0;
            const blocked = botSummary.robots_disallowed ?? 0;
            const avgMs = botSummary.avg_response_time_ms ?? 0;
            metricsHtml = `
                <div class="grid grid-cols-4 gap-3 mb-3">
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-success)">${accessible}</div><div class="text-xs" style="color:var(--ds-text-muted)">Доступны</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-warning)">${nonIndexable}</div><div class="text-xs" style="color:var(--ds-text-muted)">Не индекс.</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-danger)">${blocked}</div><div class="text-xs" style="color:var(--ds-text-muted)">Заблокир.</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${Math.round(avgMs)}ms</div><div class="text-xs" style="color:var(--ds-text-muted)">Avg time</div></div>
                </div>`;
            const blockers = rd.priority_blockers || [];
            if (blockers.length > 0) {
                issuesHtml = `<div class="space-y-1">${blockers.slice(0,5).map(b => `
                    <div class="flex items-center gap-2 text-sm">
                        <span class="ds-badge ds-badge-danger" style="font-size:0.65rem;">P0</span>
                        <span style="color:var(--ds-text);">${escapeHtml(b.title || '')} (${b.affected_bots || 0} bots)</span>
                    </div>`).join('')}</div>`;
            }
        }

        else if (toolKey === 'redirect') {
            const redSummary = rd.summary || {};
            const passed = redSummary.passed ?? 0;
            const redErrors = redSummary.errors ?? redSummary.failed ?? 0;
            const redWarnings = redSummary.warnings ?? 0;
            const grade = redSummary.quality_grade ?? redSummary.overall_grade ?? '—';
            metricsHtml = `
                <div class="grid grid-cols-4 gap-3 mb-3">
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${grade}</div><div class="text-xs" style="color:var(--ds-text-muted)">Оценка</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-success)">${passed}</div><div class="text-xs" style="color:var(--ds-text-muted)">Passed</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-danger)">${redErrors}</div><div class="text-xs" style="color:var(--ds-text-muted)">Errors</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-warning)">${redWarnings}</div><div class="text-xs" style="color:var(--ds-text-muted)">Warnings</div></div>
                </div>`;
            const scenarios = rd.scenarios || [];
            const failedScenarios = scenarios.filter(s => s.status === 'error' || s.status === 'failed' || s.status === 'warning');
            if (failedScenarios.length > 0) {
                issuesHtml = `<div class="space-y-1">${failedScenarios.slice(0,8).map(s => `
                    <div class="flex items-center gap-2 text-sm">
                        <span class="ds-badge ${s.status === 'error' || s.status === 'failed' ? 'ds-badge-danger' : 'ds-badge-warning'}" style="font-size:0.65rem;">${s.status}</span>
                        <span style="color:var(--ds-text);">${escapeHtml(s.title || '')}</span>
                    </div>`).join('')}</div>`;
            }
        }

        else if (toolKey === 'cwv') {
            const cwvData = rd.combined ? (rd.mobile || rd) : rd;
            const perfScore = cwvData.summary?.performance_score ?? cwvData.categories?.performance ?? cwvData.categories_scores?.performance ?? 0;
            const lcpMs = cwvData.metrics?.lcp?.lab_value_ms ?? cwvData.metrics?.lcp?.field_value_ms;
            const cls = cwvData.metrics?.cls?.lab_value ?? cwvData.metrics?.cls?.field_value;
            const grade = cwvData.summary?.core_web_vitals_status ?? cwvData.cwv_grade ?? '—';
            metricsHtml = `
                <div class="grid grid-cols-4 gap-3 mb-3">
                    <div class="text-center"><div class="text-xl font-bold" style="color:${_unifiedScoreColor(perfScore)}">${perfScore}</div><div class="text-xs" style="color:var(--ds-text-muted)">Performance</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${lcpMs != null ? (lcpMs/1000).toFixed(1)+'s' : '—'}</div><div class="text-xs" style="color:var(--ds-text-muted)">LCP</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${cls != null ? cls.toFixed(3) : '—'}</div><div class="text-xs" style="color:var(--ds-text-muted)">CLS</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:${grade === 'good' ? 'var(--ds-success)' : grade === 'needs_improvement' ? 'var(--ds-warning)' : 'var(--ds-danger)'}">${grade}</div><div class="text-xs" style="color:var(--ds-text-muted)">CWV Grade</div></div>
                </div>`;
            const opps = cwvData.opportunities || [];
            const topOpps = opps.filter(o => o.priority === 'critical' || o.priority === 'high').slice(0, 5);
            if (topOpps.length > 0) {
                issuesHtml = `<div class="space-y-1">${topOpps.map(o => `
                    <div class="flex items-center gap-2 text-sm">
                        <span class="ds-badge ${o.priority === 'critical' ? 'ds-badge-danger' : 'ds-badge-warning'}" style="font-size:0.65rem;">${o.priority}</span>
                        <span style="color:var(--ds-text);">${escapeHtml(o.title || '')}${o.savings_ms ? ` (−${o.savings_ms}ms)` : ''}</span>
                    </div>`).join('')}</div>`;
            }
        }

        return `
        <details class="ds-card" style="padding:0;" ${toolKey === 'onpage' ? 'open' : ''}>
            <summary style="padding:1rem;cursor:pointer;display:flex;align-items:center;justify-content:space-between;">
                <span class="font-medium" style="color:var(--ds-text);">${escapeHtml(toolName)}</span>
                <span class="text-lg font-bold" style="color:${scoreColor};">${scoreDisplay}</span>
            </summary>
            <div style="padding:0 1rem 1rem;border-top:1px solid var(--ds-border);">
                ${metricsHtml}
                ${detailsHtml}
                ${issuesHtml ? `<div class="mt-2">${issuesHtml}</div>` : '<div class="text-sm" style="color:var(--ds-text-muted);">Проблем не обнаружено</div>'}
            </div>
        </details>`;
    }

    function _renderIssuesList(issues, maxCount) {
        const critical = issues.filter(i => i.severity === 'critical');
        const warning = issues.filter(i => i.severity === 'warning');
        const shown = [...critical, ...warning].slice(0, maxCount);
        if (shown.length === 0) return '';
        return `<div class="space-y-1">${shown.map(i => `
            <div class="flex items-center gap-2 text-sm">
                <span class="ds-badge ${i.severity === 'critical' ? 'ds-badge-danger' : 'ds-badge-warning'}" style="font-size:0.65rem;">${i.severity}</span>
                <span style="color:var(--ds-text);">${escapeHtml(i.title || i.code || '')}</span>
            </div>`).join('')}</div>`;
    }

    // Build per-tool HTML
    const toolDisplayOrder = ['robots', 'sitemap', 'onpage', 'render', 'mobile', 'bot_check', 'redirect', 'cwv'];
    const toolDisplayNames = {robots:'Robots.txt',sitemap:'Sitemap',onpage:'OnPage Audit',render:'Render Audit',mobile:'Mobile Audit',bot_check:'Bot Checker',redirect:'Redirect Checker',cwv:'Core Web Vitals'};

    let perToolHtml = '';
    const availableTools = toolDisplayOrder.filter(k => toolResults[k]);
    if (availableTools.length > 0) {
        perToolHtml = `
        <div style="display:flex;flex-direction:column;gap:0.5rem;">
            <h4 class="font-semibold" style="color:var(--ds-text);margin-bottom:0.25rem;">Детали по инструментам</h4>
            ${availableTools.map(k => _renderToolSummary(k, toolDisplayNames[k] || k, toolResults[k])).join('')}
        </div>`;
    }

    return `
    <div class="space-y-4">
        ${headerHtml}
        <div class="grid grid-cols-1 lg:grid-cols-4 gap-4">
            <div class="lg:col-span-1">${gradeHtml}</div>
            <div class="lg:col-span-3">${chartsHtml}</div>
        </div>
        ${scoresGridHtml}
        ${insightsHtml}
        ${devTasksHtml}
        ${errorsHtml}
        ${perToolHtml}
    </div>`;
}

function downloadUnifiedAuditExport(format) {
    const data = unifiedAuditData;
    if (!data) { alert('Нет данных Unified Audit'); return; }
    const tid = data.task_id || taskId;
    if (!tid) { alert('task_id не найден'); return; }
    const url = `/api/tasks/unified-audit/${tid}/export/${format}`;
    const ext = format === 'docx' ? 'docx' : 'xlsx';
    fetch(url)
        .then(resp => {
            if (!resp.ok) throw new Error('Export failed: ' + resp.status);
            return resp.blob().then(blob => ({ blob, resp }));
        })
        .then(({ blob, resp }) => {
            const filename = filenameFromResponse(resp, 'unified-audit', ext, data.url || '');
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            a.click();
            URL.revokeObjectURL(a.href);
        })
        .catch(err => { console.error(err); alert('Ошибка экспорта: ' + err.message); });
}

// ---------------------------------------------------------------------------
// Batch Mode — results renderer
// ---------------------------------------------------------------------------
function _batchExtractScore(item, toolType) {
    if (!item || item.status !== 'success' || !item.result) return null;
    const r = item.result.results || item.result;
    if (!r) return null;
    const t = String(toolType || '').toLowerCase();
    if (t.includes('onpage')) return r.summary?.score ?? r.score ?? null;
    if (t.includes('redirect')) return r.summary?.quality_score ?? null;
    if (t.includes('cwv') || t.includes('core_web_vitals')) return r.summary?.performance_score ?? null;
    if (t.includes('bot')) return r.summary?.total ? Math.round((r.summary.crawlable || 0) / r.summary.total * 100) : null;
    if (t.includes('render')) return r.summary?.score ?? null;
    if (t.includes('mobile')) return r.summary?.score ?? null;
    if (t.includes('robots')) return r.quality_score ?? null;
    if (t.includes('sitemap')) return r.quality_score ?? null;
    if (t.includes('link_profile')) return r.summary?.score ?? null;
    // generic fallback
    if (typeof r.score === 'number') return r.score;
    if (r.summary && typeof r.summary.score === 'number') return r.summary.score;
    if (r.summary && typeof r.summary.quality_score === 'number') return r.summary.quality_score;
    return null;
}

function _batchExtractIssues(item, toolType) {
    if (!item || item.status !== 'success' || !item.result) return '';
    const r = item.result.results || item.result;
    if (!r) return '';
    const issues = r.issues || r.findings || [];
    if (!Array.isArray(issues) || issues.length === 0) return '—';
    let critical = 0, warning = 0, info = 0;
    issues.forEach(i => {
        const sev = String(i.severity || '').toLowerCase();
        if (sev === 'critical' || sev === 'error') critical++;
        else if (sev === 'warning') warning++;
        else info++;
    });
    const parts = [];
    if (critical > 0) parts.push(`<span style="color:var(--ds-danger);font-weight:600;">${critical} critical</span>`);
    if (warning > 0) parts.push(`<span style="color:var(--ds-warning);">${warning} warning</span>`);
    if (info > 0) parts.push(`<span style="color:var(--ds-info);">${info} info</span>`);
    return parts.join(', ') || '—';
}

function generateBatchResultsHTML(result) {
    // Unwrap: find the level that has .summary and .items
    const r = (result.summary && result.items) ? result
            : (result.results && result.results.summary) ? result.results
            : (result.result && result.result.summary) ? result.result
            : result;
    const summary = r.summary || {};
    const items = Array.isArray(r.items) ? r.items : [];
    const toolType = summary.tool || result.task_type || '';
    const totalUrls = Number(summary.total_urls ?? items.length);
    const successCount = Number(summary.success ?? items.filter(i => i.status === 'success').length);
    const errorCount = Number(summary.errors ?? items.filter(i => i.status === 'error').length);
    const url = result.url || r.url || '';
    const tid = result.task_id || taskId || '';

    const toolLabel = (toolType || 'batch').replace(/^batch_/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

    // --- Header ---
    const headerHtml = buildToolHeader({
        gradient: 'from-purple-600 to-indigo-700',
        label: 'Batch Mode',
        title: `Batch ${toolLabel}`,
        subtitle: `${totalUrls} URLs (${successCount} success, ${errorCount} error)`,
        score: totalUrls > 0 ? Math.round(successCount / totalUrls * 100) : null,
        scoreLabel: 'success rate',
        badges: [
            { text: `Total: ${totalUrls}`, cls: 'bg-white/20 text-white' },
            successCount > 0 ? { text: `OK: ${successCount}`, cls: 'bg-emerald-500/30 text-white' } : null,
            errorCount > 0 ? { text: `Error: ${errorCount}`, cls: 'bg-red-500/30 text-white' } : null
        ].filter(Boolean),
        metaLines: [],
        actionButtons: null
    });

    // --- Summary metrics ---
    const metricsHtml = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        ${buildMetricCard('Total URLs', totalUrls)}
        ${buildMetricCard('Success', successCount, successCount === totalUrls ? 'Все успешно' : '')}
        ${buildMetricCard('Errors', errorCount, errorCount > 0 ? 'Требуют внимания' : '')}
        ${buildMetricCard('Success Rate', totalUrls > 0 ? Math.round(successCount / totalUrls * 100) + '%' : '—')}
    </div>`;

    // --- Results table ---
    let tableHtml = '';
    if (items.length > 0) {
        const rows = items.map((item, i) => {
            const isSuccess = item.status === 'success';
            const statusBadge = isSuccess
                ? '<span class="ds-badge ds-badge-success">OK</span>'
                : '<span class="ds-badge ds-badge-danger">Error</span>';
            const score = _batchExtractScore(item, toolType);
            const scoreHtml = score !== null
                ? `<span style="color:${_unifiedScoreColor(score)};font-weight:600;">${score}</span>`
                : '—';
            const issuesHtml = isSuccess ? _batchExtractIssues(item, toolType) : escapeHtml(String(item.error || 'Unknown error'));
            const itemUrl = item.url || '';
            return `
            <tr>
                <td style="padding:0.5rem;white-space:nowrap;">${i + 1}</td>
                <td style="padding:0.5rem;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(itemUrl)}">${escapeHtml(itemUrl)}</td>
                <td style="padding:0.5rem;">${statusBadge}</td>
                <td style="padding:0.5rem;text-align:center;">${scoreHtml}</td>
                <td style="padding:0.5rem;">${issuesHtml}</td>
            </tr>`;
        }).join('');

        tableHtml = `
        <div class="ds-card" style="padding:1.25rem;">
            <h4 class="font-semibold mb-3" style="color:var(--ds-text);">Результаты по URL</h4>
            <div class="ds-table-wrap">
                <table class="ds-table">
                    <thead>
                        <tr><th>#</th><th>URL</th><th>Status</th><th>Score</th><th>Issues</th></tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        </div>`;
    }

    // --- Per-URL collapsible details ---
    let detailsHtml = '';
    if (items.length > 0) {
        const detailItems = items.map((item, i) => {
            const isSuccess = item.status === 'success';
            const itemUrl = item.url || `URL #${i + 1}`;
            const score = _batchExtractScore(item, toolType);
            const scoreStr = score !== null ? score : '—';
            const scoreColor = score !== null ? _unifiedScoreColor(score) : 'var(--ds-text-muted)';
            const content = isSuccess
                ? `<pre class="text-xs overflow-auto rounded p-3" style="background:var(--ds-bg);color:var(--ds-text);max-height:400px;">${escapeHtml(JSON.stringify(item.result, null, 2))}</pre>`
                : `<div class="text-sm" style="color:var(--ds-danger);padding:0.75rem;">${escapeHtml(String(item.error || 'Unknown error'))}</div>`;
            return `
            <details class="ds-card" style="padding:0;">
                <summary style="padding:0.75rem 1rem;cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:0.5rem;">
                    <span class="font-medium text-sm" style="color:var(--ds-text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;">${escapeHtml(itemUrl)}</span>
                    <span class="flex items-center gap-2 flex-shrink-0">
                        ${isSuccess
                            ? '<span class="ds-badge ds-badge-success" style="font-size:0.7em;">OK</span>'
                            : '<span class="ds-badge ds-badge-danger" style="font-size:0.7em;">Error</span>'}
                        <span class="text-lg font-bold" style="color:${scoreColor};">${scoreStr}</span>
                    </span>
                </summary>
                <div style="padding:0 1rem 1rem;border-top:1px solid var(--ds-border);">
                    ${content}
                </div>
            </details>`;
        }).join('');

        detailsHtml = `
        <div style="display:flex;flex-direction:column;gap:0.5rem;">
            <h4 class="font-semibold" style="color:var(--ds-text);margin-bottom:0.25rem;">Детали по URL</h4>
            ${detailItems}
        </div>`;
    }

    return `
    <div class="space-y-4">
        ${headerHtml}
        ${metricsHtml}
        ${tableHtml}
        ${detailsHtml}
    </div>`;
}

// Export copyToClipboard to global scope
window.copyToClipboard = copyToClipboard;
