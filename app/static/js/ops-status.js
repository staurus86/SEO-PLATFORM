(function () {
    const statusEl = document.getElementById('ops-global-status');
    const updatedAtEl = document.getElementById('ops-updated-at');
    const refreshBtn = document.getElementById('ops-refresh-btn');
    const rssEl = document.getElementById('ops-rss');
    const redisStateEl = document.getElementById('ops-redis-state');
    const workerStateEl = document.getElementById('ops-worker-state');
    const bytesSavedEl = document.getElementById('ops-bytes-saved');
    const redisUrlsEl = document.getElementById('ops-redis-urls');
    const redisPrefixesEl = document.getElementById('ops-redis-prefixes');
    const workerMetaEl = document.getElementById('ops-worker-meta');
    const memoryMetaEl = document.getElementById('ops-memory-meta');
    const compactionGridEl = document.getElementById('ops-compaction-grid');
    const observabilityBodyEl = document.querySelector('#ops-observability-table tbody');
    const memoryCleanupBtn = document.getElementById('ops-memory-cleanup-btn');
    const memoryCleanupSoftBtn = document.getElementById('ops-memory-cleanup-soft-btn');
    const artifactsCleanupBtn = document.getElementById('ops-artifacts-cleanup-btn');
    const artifactsDaysInput = document.getElementById('ops-artifacts-days');
    const maintenanceRunBtn = document.getElementById('ops-maintenance-run-btn');
    const maintenanceDaysInput = document.getElementById('ops-maintenance-days');
    const maintenanceForceGcInput = document.getElementById('ops-maintenance-force-gc');
    const actionResultEl = document.getElementById('ops-action-result');
    const actionHistoryEl = document.getElementById('ops-action-history');
    const rawSnapshotEl = document.getElementById('ops-raw-snapshot');
    const copySnapshotBtn = document.getElementById('ops-copy-snapshot-btn');
    const ACTION_HISTORY_KEY = 'ops_status_action_history_v1';
    let timer = null;
    let latestPayload = null;

    function fmtInt(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '-';
        return new Intl.NumberFormat('ru-RU').format(Math.round(n));
    }

    function fmtBytes(value) {
        const bytes = Number(value);
        if (!Number.isFinite(bytes)) return '-';
        if (bytes < 1024) return `${fmtInt(bytes)} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    }

    function fmtMs(value) {
        const n = Number(value);
        if (!Number.isFinite(n)) return '-';
        if (n < 1000) return `${fmtInt(n)} ms`;
        return `${(n / 1000).toFixed(2)} s`;
    }

    function fmtAgo(iso) {
        if (!iso) return '-';
        const dt = new Date(iso);
        if (Number.isNaN(dt.getTime())) return '-';
        const diff = Math.max(0, Date.now() - dt.getTime());
        const sec = Math.floor(diff / 1000);
        if (sec < 5) return 'just now';
        if (sec < 60) return `${sec}s ago`;
        const min = Math.floor(sec / 60);
        if (min < 60) return `${min}m ago`;
        const hrs = Math.floor(min / 60);
        return `${hrs}h ago`;
    }

    function statusPill(label, tone) {
        return `<span class="ops-status-pill ${tone}">${label}</span>`;
    }

    function renderDl(container, rows) {
        container.innerHTML = rows.map(([term, value]) => `
            <dt>${term}</dt>
            <dd>${value}</dd>
        `).join('');
    }

    function setGlobalStatus(payload) {
        const ok = payload?.status === 'healthy';
        statusEl.className = `ops-status-pill ${ok ? 'ok' : 'warn'}`;
        statusEl.textContent = ok ? 'Healthy' : 'Degraded';
        updatedAtEl.textContent = new Date().toLocaleTimeString('ru-RU');
    }

    function compactionCard(title, data) {
        const saved = Number(data?.bytes_saved_total || 0);
        const threshold = data?.threshold_bytes ?? data?.max_job_bytes ?? null;
        const limits = data?.inline_limits
            ? Object.entries(data.inline_limits).map(([key, value]) => `${key}: ${fmtInt(value)}`).join(' · ')
            : '';
        return `
            <article class="ops-metric">
                <div class="text-xs uppercase tracking-[0.16em] mb-2" style="color:var(--ds-text-muted);">${title}</div>
                <div class="text-2xl font-black ops-mono" style="color:var(--ds-text);">${fmtInt(data?.compactions_total || 0)}</div>
                <div class="text-sm mb-3" style="color:var(--ds-text-secondary);">compactions</div>
                <div class="space-y-1 text-sm">
                    <div><strong>Saved:</strong> <span class="ops-mono">${fmtBytes(saved)}</span></div>
                    <div><strong>Original:</strong> <span class="ops-mono">${fmtBytes(data?.original_bytes_total || 0)}</span></div>
                    <div><strong>Stored:</strong> <span class="ops-mono">${fmtBytes(data?.stored_bytes_total || 0)}</span></div>
                    ${threshold !== null ? `<div><strong>Threshold:</strong> <span class="ops-mono">${fmtBytes(threshold)}</span></div>` : ''}
                    ${limits ? `<div><strong>Limits:</strong> <span>${limits}</span></div>` : ''}
                    <div><strong>Last:</strong> <span class="ops-mono">${fmtAgo(data?.last_compacted_at)}</span></div>
                </div>
            </article>
        `;
    }

    function flattenObservabilityRows(observability) {
        return [
            ['Tasks Queue Wait', observability?.tasks?.queue_wait_ms, 'duration'],
            ['Tasks Runtime', observability?.tasks?.run_duration_ms, 'duration'],
            ['Tasks End-to-End', observability?.tasks?.end_to_end_ms, 'duration'],
            ['Tasks Result Payload', observability?.tasks?.result_payload_bytes, 'size'],
            ['LLM Queue Wait', observability?.llm_jobs?.queue_wait_ms, 'duration'],
            ['LLM Runtime', observability?.llm_jobs?.run_duration_ms, 'duration'],
            ['LLM End-to-End', observability?.llm_jobs?.end_to_end_ms, 'duration'],
            ['LLM Result Payload', observability?.llm_jobs?.result_payload_bytes, 'size'],
            ['Exports Generation', observability?.exports?.generation_ms, 'duration'],
            ['Exports File Size', observability?.exports?.file_size_bytes, 'export_size'],
        ];
    }

    function renderObservabilityTable(observability) {
        const rows = flattenObservabilityRows(observability);
        observabilityBodyEl.innerHTML = rows.map(([label, metric, kind]) => {
            const recent15 = metric?.recent_15m || {};
            const recent60 = metric?.recent_60m || {};
            const lifetime = kind === 'duration'
                ? `${fmtInt(metric?.count || 0)} · avg ${fmtMs(metric?.avg_ms || 0)}`
                : kind === 'size'
                    ? `${fmtInt(metric?.count || 0)} · avg ${fmtBytes(metric?.avg_stored_bytes || 0)}`
                    : `${fmtInt(metric?.count || 0)} · avg ${fmtBytes(metric?.avg_bytes || 0)}`;
            const recent15Text = kind === 'duration'
                ? `${fmtInt(recent15.count || 0)} · avg ${fmtMs(recent15.avg_ms || 0)}`
                : kind === 'size'
                    ? `${fmtInt(recent15.count || 0)} · avg ${fmtBytes(recent15.avg_stored_bytes || 0)}`
                    : `${fmtInt(recent15.count || 0)} · avg ${fmtBytes(recent15.avg_bytes || 0)}`;
            const recent60Text = kind === 'duration'
                ? `${fmtInt(recent60.count || 0)} · avg ${fmtMs(recent60.avg_ms || 0)}`
                : kind === 'size'
                    ? `${fmtInt(recent60.count || 0)} · avg ${fmtBytes(recent60.avg_stored_bytes || 0)}`
                    : `${fmtInt(recent60.count || 0)} · avg ${fmtBytes(recent60.avg_bytes || 0)}`;
            const lastText = kind === 'duration'
                ? `${fmtMs(metric?.last_ms || 0)} · ${fmtAgo(metric?.last_at)}`
                : kind === 'size'
                    ? `${fmtBytes(metric?.last_stored_bytes || 0)} · ${fmtAgo(metric?.last_at)}`
                    : `${fmtBytes(metric?.last_bytes || 0)} · ${fmtAgo(metric?.last_at)}`;
            return `
                <tr>
                    <td class="font-semibold">${label}</td>
                    <td class="ops-mono">${lifetime}</td>
                    <td class="ops-mono">${recent15Text}</td>
                    <td class="ops-mono">${recent60Text}</td>
                    <td class="ops-mono">${lastText}</td>
                </tr>
            `;
        }).join('');
    }

    function renderActionResult(title, payload) {
        actionResultEl.innerHTML = `
            <div class="font-semibold mb-2" style="color:var(--ds-text);">${title}</div>
            <pre style="white-space:pre-wrap;word-break:break-word;margin:0;">${JSON.stringify(payload, null, 2)}</pre>
        `;
    }

    function getActionHistory() {
        try {
            const raw = localStorage.getItem(ACTION_HISTORY_KEY);
            const items = JSON.parse(raw || '[]');
            return Array.isArray(items) ? items : [];
        } catch {
            return [];
        }
    }

    function saveActionHistory(items) {
        try {
            localStorage.setItem(ACTION_HISTORY_KEY, JSON.stringify(items.slice(0, 10)));
        } catch {}
    }

    function renderActionHistory() {
        const items = getActionHistory();
        if (!items.length) {
            actionHistoryEl.textContent = 'No actions yet.';
            return;
        }
        actionHistoryEl.innerHTML = items.map((item) => `
            <div class="rounded-xl border p-3" style="border-color:var(--ds-border);background:var(--ds-surface-soft);">
                <div class="flex items-center justify-between gap-3">
                    <div class="font-semibold" style="color:var(--ds-text);">${item.title}</div>
                    <div class="text-xs ops-mono">${fmtAgo(item.ts)}</div>
                </div>
                <div class="mt-1 text-xs ops-mono">${item.meta || '-'}</div>
            </div>
        `).join('');
    }

    function pushActionHistory(title, payload, meta) {
        const items = getActionHistory();
        items.unshift({
            title,
            meta: meta || JSON.stringify(payload).slice(0, 140),
            ts: new Date().toISOString(),
        });
        saveActionHistory(items);
        renderActionHistory();
    }

    async function postAction(url) {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
        });
        if (!response.ok) {
            let message = `HTTP ${response.status}`;
            try {
                const payload = await response.json();
                message = payload?.error || payload?.detail || message;
            } catch (_) {}
            throw new Error(message);
        }
        return response.json();
    }

    async function loadOpsStatus() {
        refreshBtn.disabled = true;
        try {
            const response = await fetch('/api/ops/status', { headers: { 'Accept': 'application/json' } });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const payload = await response.json();
            const compaction = payload?.payload_compaction || {};
            const observability = payload?.observability || {};
            const processMemory = payload?.memory?.process || {};
            const taskStore = payload?.memory?.task_store || {};
            const progressStore = payload?.memory?.progress_store || {};
            const redisOk = Boolean(payload?.redis?.ok);
            const workerHealthy = Boolean(payload?.llm_worker?.healthy);
            const totalSaved = ['task_store', 'progress', 'llm_crawler', 'site_audit_pro']
                .reduce((sum, key) => sum + Number(compaction?.[key]?.bytes_saved_total || 0), 0);
            latestPayload = payload;

            setGlobalStatus(payload);
            rssEl.textContent = `${fmtInt(processMemory?.rss_mb || 0)} MB`;
            redisStateEl.innerHTML = redisOk ? statusPill('Connected', 'ok') : statusPill('Fallback', 'warn');
            workerStateEl.innerHTML = workerHealthy ? statusPill('Healthy', 'ok') : statusPill('Stale', 'warn');
            bytesSavedEl.textContent = fmtBytes(totalSaved);

            renderDl(redisUrlsEl, Object.entries(payload?.redis?.urls || {}).map(([k, v]) => [k, `<span class="ops-mono">${v || '-'}</span>`]));
            renderDl(redisPrefixesEl, Object.entries(payload?.redis?.prefixes || {}).map(([k, v]) => [k, `<span class="ops-mono">${v || '-'}</span>`]));
            renderDl(workerMetaEl, [
                ['Heartbeat age', `<span class="ops-mono">${fmtInt(payload?.llm_worker?.heartbeat_age_sec || 0)} s</span>`],
                ['Queue depth', `<span class="ops-mono">${fmtInt(payload?.llm_worker?.queue_depth || 0)}</span>`],
                ['Last heartbeat', `<span class="ops-mono">${fmtAgo(payload?.llm_worker?.heartbeat?.updatedAt)}</span>`],
                ['Error', payload?.llm_worker?.error ? `<span style="color:#b91c1c;">${payload.llm_worker.error}</span>` : '—'],
            ]);
            renderDl(memoryMetaEl, [
                ['Task store items', `<span class="ops-mono">${fmtInt(taskStore?.items_total || 0)}</span>`],
                ['Task store bytes', `<span class="ops-mono">${fmtBytes(taskStore?.bytes_total || 0)}</span>`],
                ['Progress items', `<span class="ops-mono">${fmtInt(progressStore?.items_total || 0)}</span>`],
                ['Progress bytes', `<span class="ops-mono">${fmtBytes(progressStore?.bytes_total || 0)}</span>`],
                ['Guard idle', `<span class="ops-mono">${fmtInt(payload?.memory?.guard?.idle_seconds || 0)} s</span>`],
            ]);

            compactionGridEl.innerHTML = [
                compactionCard('Task Store', compaction?.task_store || {}),
                compactionCard('Progress', compaction?.progress || {}),
                compactionCard('LLM Crawler', compaction?.llm_crawler || {}),
                compactionCard('Site Audit Pro', compaction?.site_audit_pro || {}),
            ].join('');

            renderObservabilityTable(observability);
            rawSnapshotEl.textContent = JSON.stringify(payload, null, 2);
        } catch (error) {
            statusEl.className = 'ops-status-pill bad';
            statusEl.textContent = 'Fetch Error';
            rawSnapshotEl.textContent = JSON.stringify({ error: error.message }, null, 2);
            if (typeof showToast === 'function') {
                showToast(`Ops status load failed: ${error.message}`, 'error');
            }
        } finally {
            refreshBtn.disabled = false;
        }
    }

    refreshBtn?.addEventListener('click', loadOpsStatus);
    memoryCleanupBtn?.addEventListener('click', async function () {
        memoryCleanupBtn.disabled = true;
        try {
            const payload = await postAction('/api/memory/cleanup?aggressive=true');
            renderActionResult('Memory cleanup completed', payload);
            pushActionHistory('Aggressive memory cleanup', payload, `removed=${payload?.task_store?.removed_total ?? '-'} task items`);
            if (typeof showToast === 'function') showToast('Aggressive memory cleanup completed', 'success');
            await loadOpsStatus();
        } catch (error) {
            renderActionResult('Memory cleanup failed', { error: error.message });
            if (typeof showToast === 'function') showToast(`Memory cleanup failed: ${error.message}`, 'error');
        } finally {
            memoryCleanupBtn.disabled = false;
        }
    });
    memoryCleanupSoftBtn?.addEventListener('click', async function () {
        memoryCleanupSoftBtn.disabled = true;
        try {
            const payload = await postAction('/api/memory/cleanup?aggressive=false');
            renderActionResult('Soft cleanup completed', payload);
            pushActionHistory('Soft memory cleanup', payload, `removed=${payload?.task_store?.removed_total ?? '-'} task items`);
            if (typeof showToast === 'function') showToast('Soft memory cleanup completed', 'success');
            await loadOpsStatus();
        } catch (error) {
            renderActionResult('Soft cleanup failed', { error: error.message });
            if (typeof showToast === 'function') showToast(`Soft cleanup failed: ${error.message}`, 'error');
        } finally {
            memoryCleanupSoftBtn.disabled = false;
        }
    });
    artifactsCleanupBtn?.addEventListener('click', async function () {
        artifactsCleanupBtn.disabled = true;
        const days = Math.max(1, Number(artifactsDaysInput?.value || 7));
        try {
            const payload = await postAction(`/api/tasks/cleanup-stale-artifacts?days=${encodeURIComponent(days)}`);
            renderActionResult(`Stale artifacts cleanup (${days}d) completed`, payload);
            pushActionHistory(`Artifacts cleanup (${days}d)`, payload, `deleted=${payload?.cleanup?.deleted_files ?? payload?.cleanup?.removed_total ?? '-'}`);
            if (typeof showToast === 'function') showToast('Stale artifacts cleanup completed', 'success');
            await loadOpsStatus();
        } catch (error) {
            renderActionResult('Stale artifacts cleanup failed', { error: error.message, days });
            if (typeof showToast === 'function') showToast(`Artifacts cleanup failed: ${error.message}`, 'error');
        } finally {
            artifactsCleanupBtn.disabled = false;
        }
    });
    maintenanceRunBtn?.addEventListener('click', async function () {
        maintenanceRunBtn.disabled = true;
        const days = Math.max(1, Number(maintenanceDaysInput?.value || 7));
        const forceGc = Boolean(maintenanceForceGcInput?.checked);
        try {
            const payload = await postAction(`/api/maintenance/run?days=${encodeURIComponent(days)}&force_gc=${forceGc ? 'true' : 'false'}`);
            renderActionResult(`Maintenance runner (${days}d, force_gc=${forceGc}) completed`, payload);
            pushActionHistory(
                `Maintenance runner (${days}d)`,
                payload,
                `queue=${payload?.llm_queue_depth ?? '-'} · gc=${forceGc ? 'on' : 'off'}`
            );
            if (typeof showToast === 'function') showToast('Maintenance runner completed', 'success');
            await loadOpsStatus();
        } catch (error) {
            renderActionResult('Maintenance runner failed', { error: error.message, days, force_gc: forceGc });
            if (typeof showToast === 'function') showToast(`Maintenance runner failed: ${error.message}`, 'error');
        } finally {
            maintenanceRunBtn.disabled = false;
        }
    });
    copySnapshotBtn?.addEventListener('click', async function () {
        try {
            const text = JSON.stringify(latestPayload || { error: 'No snapshot loaded' }, null, 2);
            await navigator.clipboard.writeText(text);
            if (typeof showToast === 'function') showToast('Snapshot copied to clipboard', 'success');
        } catch (error) {
            if (typeof showToast === 'function') showToast(`Copy failed: ${error.message}`, 'error');
        }
    });
    renderActionHistory();
    loadOpsStatus();
    timer = window.setInterval(loadOpsStatus, 30000);
    window.addEventListener('pagehide', function () {
        if (timer) window.clearInterval(timer);
    });
})();
