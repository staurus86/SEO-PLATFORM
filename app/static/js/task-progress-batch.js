// ---------------------------------------------------------------------------
// Batch Mode — results renderer
// Extracted from task-progress.js to keep the main runtime slimmer.
// Depends on shared globals defined in task-progress.js:
// escapeHtml, buildToolHeader, buildMetricCard, _unifiedScoreColor
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

function _batchFriendlyToolLabel(toolType) {
    const value = String(toolType || '').toLowerCase().replace(/^batch_/, '');
    const labels = {
        onpage: 'OnPage Audit',
        redirect: 'Redirect Checker',
        cwv: 'Core Web Vitals',
        core_web_vitals: 'Core Web Vitals',
        bot: 'Bot Checker',
        'bot-check': 'Bot Checker',
        render: 'Render Audit',
        mobile: 'Mobile Audit',
        robots: 'Robots.txt',
        sitemap: 'Sitemap',
        link_profile: 'Link Profile',
    };
    return labels[value] || value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || 'Batch';
}

function _batchRenderIssueList(items, maxItems = 6) {
    if (!Array.isArray(items) || items.length === 0) {
        return '<div class="text-sm" style="color:var(--ds-text-muted);">Проблем не обнаружено</div>';
    }
    return `<ul class="text-sm space-y-1" style="color:var(--ds-text-secondary);">${items.slice(0, maxItems).map((entry) => {
        const text = typeof entry === 'string'
            ? entry
            : entry?.title || entry?.text || entry?.message || entry?.code || JSON.stringify(entry);
        return `<li>• ${escapeHtml(String(text || ''))}</li>`;
    }).join('')}</ul>`;
}

function _batchMetricMini(label, value, tone = 'var(--ds-text)') {
    return `
        <div class="text-center rounded-lg p-3" style="background:var(--ds-bg-soft);border:1px solid var(--ds-border);">
            <div class="text-xl font-bold" style="color:${tone}">${escapeHtml(String(value ?? '—'))}</div>
            <div class="text-xs" style="color:var(--ds-text-muted)">${escapeHtml(String(label || ''))}</div>
        </div>`;
}

function _batchRenderSuccessDetails(item, toolType) {
    const r = item?.result?.results || item?.result || {};
    const t = String(toolType || '').toLowerCase();

    if (t.includes('robots')) {
        const found = Boolean(r.robots_txt_found);
        const recs = Array.isArray(r.recommendations) ? r.recommendations : [];
        return `
            <div class="space-y-3">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${_batchMetricMini('robots.txt', found ? 'Найден' : 'Не найден', found ? 'var(--ds-success)' : 'var(--ds-danger)')}
                    ${_batchMetricMini('Правила', Number(r.rule_count ?? r.total_rules ?? 0))}
                    ${_batchMetricMini('Sitemaps', Number(r.sitemap_count ?? (Array.isArray(r.sitemaps) ? r.sitemaps.length : 0)))}
                    ${_batchMetricMini('Quality', Number(r.quality_score ?? 0), _unifiedScoreColor(Number(r.quality_score ?? 0)))}
                </div>
                ${_batchRenderIssueList(recs.length ? recs : (r.issues || r.warnings || r.parser_warnings || []))}
            </div>`;
    }

    if (t.includes('sitemap')) {
        const valid = r.valid;
        const messages = [...(r.errors || []), ...(r.warnings || []), ...(r.highlights || [])];
        return `
            <div class="space-y-3">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${_batchMetricMini('Статус', valid === true ? 'Валидна' : valid === false ? 'Невалидна' : 'Неизвестно', valid === true ? 'var(--ds-success)' : valid === false ? 'var(--ds-danger)' : 'var(--ds-warning)')}
                    ${_batchMetricMini('URL', Number(r.urls_count ?? 0))}
                    ${_batchMetricMini('Файлов', Number(r.sitemaps_scanned ?? r.files_count ?? 0))}
                    ${_batchMetricMini('Quality', Number(r.quality_score ?? 0), _unifiedScoreColor(Number(r.quality_score ?? 0)))}
                </div>
                ${_batchRenderIssueList(messages)}
            </div>`;
    }

    if (t.includes('bot')) {
        const s = r.summary || {};
        return `
            <div class="space-y-3">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${_batchMetricMini('Доступны', Number(s.accessible ?? 0), 'var(--ds-success)')}
                    ${_batchMetricMini('Индекс.', Number(s.indexable ?? 0))}
                    ${_batchMetricMini('Заблокир.', Number(s.robots_disallowed ?? 0), 'var(--ds-danger)')}
                    ${_batchMetricMini('Avg time', `${Math.round(Number(s.avg_response_time_ms ?? 0))}ms`)}
                </div>
                ${_batchRenderIssueList(r.priority_blockers || r.issues || [])}
            </div>`;
    }

    if (t.includes('cwv') || t.includes('core_web_vitals')) {
        const summary = r.summary || {};
        const metrics = r.metrics || {};
        const lcpMs = metrics?.lcp?.lab_value_ms ?? metrics?.lcp?.field_value_ms;
        const cls = metrics?.cls?.lab_value ?? metrics?.cls?.field_value;
        const inpMs = metrics?.inp?.lab_value_ms ?? metrics?.inp?.field_value_ms;
        return `
            <div class="space-y-3">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${_batchMetricMini('Performance', Number(summary.performance_score ?? 0), _unifiedScoreColor(Number(summary.performance_score ?? 0)))}
                    ${_batchMetricMini('LCP', lcpMs != null ? `${(Number(lcpMs) / 1000).toFixed(1)}s` : '—')}
                    ${_batchMetricMini('CLS', cls != null ? Number(cls).toFixed(3) : '—')}
                    ${_batchMetricMini('INP', inpMs != null ? `${Math.round(Number(inpMs))}ms` : '—')}
                </div>
                ${_batchRenderIssueList(r.opportunities || r.issues || [])}
            </div>`;
    }

    if (t.includes('redirect')) {
        const summary = r.summary || {};
        const scenarios = Array.isArray(r.scenarios) ? r.scenarios.filter((entry) => ['warning', 'error', 'failed'].includes(String(entry.status || '').toLowerCase())) : [];
        return `
            <div class="space-y-3">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${_batchMetricMini('Оценка', summary.quality_grade ?? summary.overall_grade ?? '—')}
                    ${_batchMetricMini('Passed', Number(summary.passed ?? 0), 'var(--ds-success)')}
                    ${_batchMetricMini('Errors', Number(summary.errors ?? summary.failed ?? 0), 'var(--ds-danger)')}
                    ${_batchMetricMini('Warnings', Number(summary.warnings ?? 0), 'var(--ds-warning)')}
                </div>
                ${_batchRenderIssueList(scenarios)}
            </div>`;
    }

    if (t.includes('render')) {
        const summary = r.summary || {};
        const findings = [...(r.issues || []), ...(r.findings || [])];
        return `
            <div class="space-y-3">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${_batchMetricMini('Score', Number(summary.score ?? summary.quality_score ?? 0), _unifiedScoreColor(Number(summary.score ?? summary.quality_score ?? 0)))}
                    ${_batchMetricMini('JS-only', Number(summary.js_only_nodes ?? 0))}
                    ${_batchMetricMini('JS Errors', Number(summary.javascript_errors ?? summary.js_errors ?? 0))}
                    ${_batchMetricMini('Framework', summary.framework || '—')}
                </div>
                ${_batchRenderIssueList(findings)}
            </div>`;
    }

    if (t.includes('mobile')) {
        const summary = r.summary || {};
        return `
            <div class="space-y-3">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${_batchMetricMini('Score', Number(summary.score ?? r.score ?? 0), _unifiedScoreColor(Number(summary.score ?? r.score ?? 0)))}
                    ${_batchMetricMini('Mobile-friendly', summary.mobile_friendly ? 'Да' : 'Нет', summary.mobile_friendly ? 'var(--ds-success)' : 'var(--ds-danger)')}
                    ${_batchMetricMini('Устройств', Number(r.devices_tested?.length ?? r.device_results?.length ?? 0))}
                    ${_batchMetricMini('Проблем', Number(r.issues?.length ?? 0), 'var(--ds-warning)')}
                </div>
                ${_batchRenderIssueList(r.issues || [])}
            </div>`;
    }

    if (t.includes('onpage')) {
        const summary = r.summary || {};
        return `
            <div class="space-y-3">
                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                    ${_batchMetricMini('Score', Number(summary.score ?? r.score ?? 0), _unifiedScoreColor(Number(summary.score ?? r.score ?? 0)))}
                    ${_batchMetricMini('Слов', Number(r.word_count ?? 0))}
                    ${_batchMetricMini('Title', Number(r.title_length ?? 0))}
                    ${_batchMetricMini('Desc', Number(r.meta_description_length ?? 0))}
                </div>
                ${_batchRenderIssueList(r.issues || r.recommendations || [])}
            </div>`;
    }

    return `<pre class="text-xs overflow-auto rounded p-3" style="background:var(--ds-bg);color:var(--ds-text);max-height:400px;">${escapeHtml(JSON.stringify(item.result, null, 2))}</pre>`;
}

function generateBatchResultsHTML(result) {
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

    const headerHtml = buildToolHeader({
        gradient: 'from-purple-600 to-indigo-700',
        label: 'Batch Mode',
        title: `Batch ${_batchFriendlyToolLabel(toolType)}`,
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

    const metricsHtml = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        ${buildMetricCard('Total URLs', totalUrls)}
        ${buildMetricCard('Success', successCount, successCount === totalUrls ? 'Все успешно' : '')}
        ${buildMetricCard('Errors', errorCount, errorCount > 0 ? 'Требуют внимания' : '')}
        ${buildMetricCard('Success Rate', totalUrls > 0 ? Math.round(successCount / totalUrls * 100) + '%' : '—')}
    </div>`;

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

    let detailsHtml = '';
    if (items.length > 0) {
        const detailItems = items.map((item, i) => {
            const isSuccess = item.status === 'success';
            const itemUrl = item.url || `URL #${i + 1}`;
            const score = _batchExtractScore(item, toolType);
            const scoreStr = score !== null ? score : '—';
            const scoreColor = score !== null ? _unifiedScoreColor(score) : 'var(--ds-text-muted)';
            const content = isSuccess
                ? _batchRenderSuccessDetails(item, toolType)
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
