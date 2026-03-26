/**
 * LLM Crawler Simulation frontend helpers.
 */

const LLM_API_BASE = '/api/tools/llm-crawler';

function _humanizeNetworkError(error) {
    const msg = String(error?.message || '');
    if (msg.toLowerCase().includes('failed to fetch')) {
        return 'Сеть недоступна или сервис перезапускается. Проверьте деплой web/worker и повторите.';
    }
    return msg || 'Request failed';
}

function _escapeHtml(input) {
    return String(input ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _safeNum(value, fallback = '-') {
    if (value === null || value === undefined || value === '') return fallback;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function _statusChip(status) {
    const token = String(status || '').toLowerCase();
    if (token === 'done') return '<span class="px-2 py-1 rounded-full text-xs font-semibold bg-green-100 text-green-700">DONE</span>';
    if (token === 'running') return '<span class="px-2 py-1 rounded-full text-xs font-semibold bg-blue-100 text-blue-700">RUNNING</span>';
    if (token === 'queued') return '<span class="px-2 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-700">QUEUED</span>';
    return '<span class="px-2 py-1 rounded-full text-xs font-semibold bg-red-100 text-red-700">ERROR</span>';
}

const LLM_STAGE_ORDER = ['queue', 'fetch', 'policy', 'render', 'analyze', 'done'];

function _llmStageFromStatus(status, progress, message) {
    const msg = String(message || '').toLowerCase();
    const p = Number(progress || 0);
    if (String(status || '').toLowerCase() === 'done') return 'done';
    if (msg.includes('queue')) return 'queue';
    if (msg.includes('no-js') || msg.includes('fetch')) return 'fetch';
    if (msg.includes('policy') || msg.includes('robots')) return 'policy';
    if (msg.includes('render')) return 'render';
    if (msg.includes('diff') || msg.includes('score')) return 'analyze';
    if (p >= 95) return 'analyze';
    if (p >= 70) return 'render';
    if (p >= 50) return 'policy';
    if (p >= 20) return 'fetch';
    return 'queue';
}

function _updateLlmStageRail(stageToken) {
    const nodes = document.querySelectorAll('#llm-stage-rail [data-llm-stage]');
    if (!nodes.length) return;
    const active = LLM_STAGE_ORDER.includes(stageToken) ? stageToken : 'queue';
    const activeIdx = LLM_STAGE_ORDER.indexOf(active);
    nodes.forEach((el) => {
        const token = el.getAttribute('data-llm-stage');
        const idx = LLM_STAGE_ORDER.indexOf(token);
        const dot = el.querySelector('span.w-2\\.5');
        el.classList.remove('bg-slate-50', 'text-slate-600', 'border-slate-200');
        el.classList.remove('bg-blue-50', 'text-blue-700', 'border-blue-200');
        el.classList.remove('bg-green-50', 'text-green-700', 'border-green-200');
        if (idx < activeIdx) {
            el.classList.add('bg-green-50', 'text-green-700', 'border-green-200');
            if (dot) dot.className = 'w-2.5 h-2.5 rounded-full bg-green-500';
        } else if (idx === activeIdx) {
            el.classList.add('bg-blue-50', 'text-blue-700', 'border-blue-200');
            if (dot) dot.className = 'w-2.5 h-2.5 rounded-full bg-blue-500';
        } else {
            el.classList.add('bg-slate-50', 'text-slate-600', 'border-slate-200');
            if (dot) dot.className = 'w-2.5 h-2.5 rounded-full bg-slate-300';
        }
    });
}

async function startLlmCrawlerTask(event) {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector('button[type="submit"]');
    const originalText = button?.innerHTML || '';

    const url = String(form.querySelector('input[name="url"]')?.value || '').trim();
    const timeoutRaw = Number(form.querySelector('input[name="timeoutMs"]')?.value || 20000);
    const renderJs = Boolean(form.querySelector('input[name="renderJs"]')?.checked);
    const showHeaders = Boolean(form.querySelector('input[name="showHeaders"]')?.checked);
    const profiles = Array.from(form.querySelectorAll('input[name="profile"]:checked')).map((x) => String(x.value || '').trim());

    if (!url) {
        showToast('Введите URL', 'warning');
        return;
    }
    if (profiles.length === 0) {
        showToast('Выберите хотя бы один профиль policy', 'warning');
        return;
    }

    const payload = {
        url,
        options: {
            renderJs,
            timeoutMs: Math.min(120000, Math.max(3000, timeoutRaw || 20000)),
            profile: profiles,
            showHeaders,
        },
    };

    if (button) {
        button.disabled = true;
        button.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Running...';
    }

    try {
        const spEnabled = typeof window._spTokenCheckboxEnabled === 'function' ? window._spTokenCheckboxEnabled(form) : false;
        const spHeaders = typeof window._buildSpTokenHeaders === 'function' ? window._buildSpTokenHeaders(spEnabled) : {};
        const response = await fetch(`${LLM_API_BASE}/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...spHeaders },
            body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const message = data?.detail?.message || data?.detail || data?.error || data?.status_message || `HTTP ${response.status}`;
            throw new Error(message);
        }
        const jobId = String(data?.jobId || '');
        if (!jobId) {
            throw new Error('Job ID is empty');
        }

        if (typeof window.addToHistory === 'function') {
            window.addToHistory({
                taskId: jobId,
                tool: 'llm-crawler-simulation',
                url,
                status: 'queued',
                timestamp: new Date().toISOString(),
            });
        }
        showToast('LLM crawler job queued', 'success');
        setTimeout(() => {
            window.location.href = `/llm-crawler/results/${jobId}`;
        }, 500);
    } catch (error) {
        showToast(_humanizeNetworkError(error) || 'Failed to start LLM crawler job', 'error');
    } finally {
        if (button) {
            button.disabled = false;
            button.innerHTML = originalText;
        }
    }
}

function initLlmCrawlerResult(jobId) {
    const progressBar = document.getElementById('llm-progress-bar');
    const statusText = document.getElementById('llm-status-text');
    const statusBadge = document.getElementById('llm-status-badge');
    const progressValue = document.getElementById('llm-progress-value');
    const errorBox = document.getElementById('llm-error-box');
    const resultBox = document.getElementById('llm-result-box');
    const downloadBtn = document.getElementById('llm-download-json');
    const downloadDocxBtn = document.getElementById('llm-download-docx');

    let pollHandle = null;
    let latestResult = null;

    function setProgress(status, progress, message) {
        const p = Math.max(0, Math.min(100, Number(progress || 0)));
        if (progressBar) progressBar.style.width = `${p}%`;
        if (progressValue) progressValue.textContent = `${p}%`;
        if (statusText) statusText.textContent = String(message || status || 'queued');
        if (statusBadge) statusBadge.innerHTML = _statusChip(status);
        _updateLlmStageRail(_llmStageFromStatus(status, p, message));
    }

    function renderSummary(result) {
        const score = result?.score || {};
        const breakdown = score?.breakdown || {};
        const topIssues = Array.isArray(score?.top_issues) ? score.top_issues : [];
        const botMatrix = Array.isArray(result?.bot_matrix) ? result.bot_matrix : [];
        const recs = Array.isArray(result?.recommendations) ? result.recommendations : [];
        const nojs = result?.nojs || {};
        const signals = nojs.signals || {};
        const schema = nojs.schema || {};
        const cloaking = result?.cloaking || {};
        const jsDep = result?.js_dependency || {};
        const eeat = result?.eeat_score || {};
        const citationProb = result?.citation_probability;
        const ingestion = result?.llm_ingestion || {};
        const vectorScore = result?.vector_quality_score;
        return `
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="bg-slate-50 border border-slate-200 rounded-lg p-4">
                    <div class="text-sm text-slate-600">AI-ready score</div>
                    <div class="text-4xl font-bold text-slate-800 mt-1">${_safeNum(score?.total, 0)}</div>
                </div>
                <div class="bg-slate-50 border border-slate-200 rounded-lg p-4">
                    <div class="text-sm text-slate-600">Breakdown</div>
                    <div class="mt-2 text-sm text-slate-700 space-y-1">
                        <div>Access: <span class="font-semibold">${_safeNum(breakdown.access, 0)}</span></div>
                        <div>Content: <span class="font-semibold">${_safeNum(breakdown.content, 0)}</span></div>
                        <div>Structure: <span class="font-semibold">${_safeNum(breakdown.structure, 0)}</span></div>
                        <div>Signals: <span class="font-semibold">${_safeNum(breakdown.signals, 0)}</span></div>
                    </div>
                </div>
                <div class="bg-white border rounded-lg p-3">
                    <h4 class="font-semibold text-slate-800 mb-2">Trust & schema</h4>
                    <div class="text-sm text-slate-700 space-y-1">
                        <div>Author: <span class="font-semibold">${signals.author_present ? 'yes' : 'no'}</span></div>
                        <div>Date: <span class="font-semibold">${signals.date_present ? 'yes' : 'no'}</span></div>
                        <div>Schema types: <span class="font-semibold">${_safeNum(schema.count, 0)}</span></div>
                        <div>Schema coverage: <span class="font-semibold">${_safeNum(schema.coverage_score, 0)}%</span></div>
                        <div>EEAT score: <span class="font-semibold">${_safeNum(eeat.score, '-')}</span></div>
                        <div>Citation probability: <span class="font-semibold">${_safeNum(citationProb, '-')}%</span></div>
                        <div>Vector clarity: <span class="font-semibold">${_safeNum(vectorScore, '-')}</span></div>
                        <div>JS dependency: <span class="font-semibold">${_safeNum(jsDep.score, '-')}</span></div>
                        <div>Cloaking risk: <span class="font-semibold">${_escapeHtml(cloaking.risk || 'n/a')}</span></div>
                    </div>
                </div>
            </div>
            <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="bg-white border rounded-lg p-3">
                    <h4 class="font-semibold text-slate-800 mb-2">LLM ingestion</h4>
                    <div class="text-sm text-slate-700 space-y-1">
                        <div>Chunks: <span class="font-semibold">${_safeNum(ingestion.chunks_count, '-')}</span></div>
                        <div>Avg quality: <span class="font-semibold">${_safeNum(ingestion.avg_chunk_quality, '-')}</span></div>
                        <div>Lost content: <span class="font-semibold">${_safeNum(ingestion.lost_content_percent, '-')}%</span></div>
                        <div>Risk: <span class="font-semibold">${_escapeHtml(ingestion.ingestion_risk || '-')}</span></div>
                    </div>
                </div>
                <div class="bg-white border rounded-lg p-3">
                    <h4 class="font-semibold text-slate-800 mb-2">Cloaking</h4>
                    <div class="text-sm text-slate-700 space-y-1">
                        <div>Risk: <span class="font-semibold">${_escapeHtml(cloaking.risk || 'n/a')}</span></div>
                        <div>Browser vs GPTBot: <span class="font-semibold">${_safeNum((cloaking.similarity_scores||{}).browser_vs_gptbot, '-')}</span></div>
                        <div>Browser vs Googlebot: <span class="font-semibold">${_safeNum((cloaking.similarity_scores||{}).browser_vs_googlebot, '-')}</span></div>
                    </div>
                </div>
            </div>
            <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="bg-white border rounded-lg p-3">
                    <h4 class="font-semibold text-slate-800 mb-2">Bot access matrix</h4>
                    ${botMatrix.length ? `
                        <div class="overflow-auto border rounded-lg">
                            <table class="min-w-full text-sm">
                                <thead class="bg-slate-50"><tr><th class="text-left p-2">Profile</th><th class="text-left p-2">Access</th><th class="text-left p-2">Reason</th></tr></thead>
                                <tbody>
                                    ${botMatrix.map((row) => `
                                        <tr class="border-t">
                                            <td class="p-2">${_escapeHtml(row.profile || '')}</td>
                                            <td class="p-2">${row.allowed ? 'Allowed' : 'Blocked'}</td>
                                            <td class="p-2">${_escapeHtml(row.reason || '-')}</td>
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>
                        </div>` : '<p class="text-sm text-slate-500">No bot data</p>'}
                </div>
                <div class="bg-white border rounded-lg p-3">
                    <h4 class="font-semibold text-slate-800 mb-2">Recommended fixes</h4>
                    ${recs.length ? `
                        <div class="overflow-auto border rounded-lg">
                            <table class="min-w-full text-sm">
                                <thead class="bg-slate-50"><tr><th class="text-left p-2">Pri</th><th class="text-left p-2">Area</th><th class="text-left p-2">Fix</th></tr></thead>
                                <tbody>
                                    ${recs.map((r) => `
                                        <tr class="border-t">
                                            <td class="p-2 font-semibold">${_escapeHtml(r.priority || 'P2')}</td>
                                            <td class="p-2 text-slate-600">${_escapeHtml(r.area || '-')}</td>
                                            <td class="p-2">${_escapeHtml(r.title || '')}</td>
                                        </tr>
                                    `).join('')}
                                </tbody>
                            </table>
                        </div>` : '<p class="text-sm text-slate-500">No high-priority issues.</p>'}
                </div>
            </div>
            <div class="mt-4">
                <h4 class="font-semibold text-slate-800 mb-2">Top issues</h4>
                ${topIssues.length ? `<ul class="list-disc pl-5 text-sm text-slate-700">${topIssues.map((x) => `<li>${_escapeHtml(x)}</li>`).join('')}</ul>` : '<p class="text-sm text-slate-500">No critical issues detected.</p>'}
            </div>
        `;
    }

    function renderSnapshot(snapshot, title) {
        if (!snapshot) {
            return `<p class="text-sm text-slate-500">${_escapeHtml(title)} not available.</p>`;
        }
        const meta = snapshot.meta || {};
        const content = snapshot.content || {};
        const headings = snapshot.headings || {};
        const links = snapshot.links || {};
        const schema = snapshot.schema || {};
        const social = snapshot.social || {};
        const renderDebug = snapshot.render_debug || {};
        const resources = snapshot.resources || {};
        const entityGraph = snapshot.entity_graph || {};
        const jsDep = snapshot.js_dependency || {};
        return `
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Final URL:</span><div class="font-medium break-all">${_escapeHtml(snapshot.final_url || '-')}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Status:</span><div class="font-medium">${_safeNum(snapshot.status_code, '-')}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Content-Type:</span><div class="font-medium">${_escapeHtml((snapshot.http || {}).content_type || '-')}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Size / timing:</span><div class="font-medium">${_safeNum((snapshot.http || {}).size_bytes, 0)} bytes / ${_safeNum((snapshot.http || {}).timing_ms, 0)} ms</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Title:</span><div class="font-medium">${_escapeHtml(meta.title || '-')}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Meta robots:</span><div class="font-medium">${_escapeHtml(meta.meta_robots || '-')}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Canonical:</span><div class="font-medium break-all">${_escapeHtml(meta.canonical || '-')}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">X-Robots-Tag:</span><div class="font-medium">${_escapeHtml(meta.x_robots_tag || '-')}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Text len / words:</span><div class="font-medium">${_safeNum(content.main_text_length, 0)} / ${_safeNum(content.word_count, 0)}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Readability:</span><div class="font-medium">${_safeNum(content.readability_score, 0)}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Reader (readability):</span><div class="font-medium text-slate-700 text-xs break-words">${_escapeHtml((content.readability_text || '').slice(0,240) || '-')}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Reader (trafilatura):</span><div class="font-medium text-slate-700 text-xs break-words">${_escapeHtml((content.trafilatura_text || '').slice(0,240) || '-')}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">H1/H2/H3:</span><div class="font-medium">${_safeNum(headings.h1, 0)} / ${_safeNum(headings.h2, 0)} / ${_safeNum(headings.h3, 0)}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Links / js-only / schema:</span><div class="font-medium">${_safeNum(links.count, 0)} / ${_safeNum(links.js_only_count, 0)} / ${_safeNum(schema.count, 0)}</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Anchor quality:</span><div class="font-medium">${_safeNum(links.anchor_quality_score, 0)}%</div></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">OG / Twitter:</span><div class="font-medium">${social.og_present ? 'yes' : 'no'} / ${social.twitter_present ? 'yes' : 'no'}</div></div>
            </div>
            <div class="mt-4">
                <h4 class="font-semibold text-slate-800 mb-2">Top links (20)</h4>
                ${(links.top || []).length ? `<div class="overflow-auto border rounded-lg"><table class="min-w-full text-sm"><thead class="bg-slate-50"><tr><th class="text-left p-2">Anchor</th><th class="text-left p-2">URL</th></tr></thead><tbody>${(links.top || []).map((row) => `<tr class="border-t"><td class="p-2">${_escapeHtml(row.anchor || '-')}</td><td class="p-2 break-all">${_escapeHtml(row.url || '-')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="text-sm text-slate-500">No links found.</p>'}
            </div>
            ${(jsDep.score || jsDep.failures || jsDep.blocked) ? `
            <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
                <div class="bg-white border rounded-lg p-3">
                    <h4 class="font-semibold text-slate-800 mb-2">JS dependency</h4>
                    <div>Score: <span class="font-semibold">${_safeNum(jsDep.score, '-')}</span></div>
                    <div>Failed: <span class="font-semibold">${_safeNum(jsDep.failures, '-')}</span></div>
                    <div>Blocked scripts/css: <span class="font-semibold">${_safeNum(jsDep.blocked, '-')}</span></div>
                </div>
            </div>` : ''}
            ${(entityGraph.organizations || entityGraph.persons || entityGraph.products) ? `
            <div class="mt-4">
                <h4 class="font-semibold text-slate-800 mb-2">Entity graph</h4>
                <div class="text-xs bg-slate-50 border rounded-lg p-3 max-h-48 overflow-auto">
                    <div><strong>Organizations:</strong> ${(entityGraph.organizations || []).slice(0,10).join(', ') || '—'}</div>
                    <div><strong>Persons:</strong> ${(entityGraph.persons || []).slice(0,10).join(', ') || '—'}</div>
                    <div><strong>Products:</strong> ${(entityGraph.products || []).slice(0,10).join(', ') || '—'}</div>
                    <div><strong>Locations:</strong> ${(entityGraph.locations || []).slice(0,10).join(', ') || '—'}</div>
                </div>
            </div>` : ''}
            ${(resources && (resources.cookie_wall || resources.paywall || resources.login_wall || resources.csp_strict || resources.mixed_content_count > 0)) ? `
            <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
                <div class="bg-amber-50 border border-amber-200 rounded-lg p-3">
                    <div class="font-semibold text-amber-800 mb-1">Access barriers</div>
                    <ul class="list-disc pl-4 text-amber-900 space-y-1">
                        ${resources.cookie_wall ? '<li>Cookie/consent wall</li>' : ''}
                        ${resources.paywall ? '<li>Paywall detected</li>' : ''}
                        ${resources.login_wall ? '<li>Login wall detected</li>' : ''}
                        ${resources.csp_strict ? '<li>Very strict CSP (script-src \'none\'/default-src \'none\')</li>' : ''}
                        ${resources.mixed_content_count > 0 ? `<li>Mixed content: ${_safeNum(resources.mixed_content_count, 0)} resources</li>` : ''}
                    </ul>
                </div>
            </div>` : ''}
            ${renderDebug.console_errors && renderDebug.console_errors.length ? `
            <div class="mt-4">
                <h4 class="font-semibold text-slate-800 mb-2">Console errors/warnings</h4>
                <div class="text-xs bg-slate-50 border rounded-lg p-3 max-h-48 overflow-auto">${renderDebug.console_errors.map((x) => `<div class="py-0.5 break-words">${_escapeHtml(x)}</div>`).join('')}</div>
            </div>` : ''}
            ${renderDebug.failed_requests && renderDebug.failed_requests.length ? `
            <div class="mt-4">
                <h4 class="font-semibold text-slate-800 mb-2">Failed requests</h4>
                <div class="text-xs bg-slate-50 border rounded-lg p-3 max-h-48 overflow-auto">${renderDebug.failed_requests.map((x) => `<div class="py-0.5 break-words">[${_escapeHtml(String(x.resource_type || '-'))}] ${_escapeHtml(String(x.url || '-'))} ${_escapeHtml(String(x.failure_text || ''))}</div>`).join('')}</div>
            </div>` : ''}
        `;
    }

    function renderDiff(result) {
        const diff = result?.diff || {};
        const links = diff.linksDiff || {};
        const h = diff.headingsDiff || {};
        const missing = Array.isArray(diff.missing) ? diff.missing : [];
        return `
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">textCoverage:</span> <span class="font-semibold">${_safeNum(diff.textCoverage, '-')}</span></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Links diff:</span> <span class="font-semibold">+${_safeNum(links.added, 0)} / -${_safeNum(links.removed, 0)}</span></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Headings diff:</span> <span class="font-semibold">H1 ${_safeNum(h.h1, 0)}, H2 ${_safeNum(h.h2, 0)}, H3 ${_safeNum(h.h3, 0)}</span></div>
                <div class="bg-white border rounded-lg p-3"><span class="text-slate-500">Note:</span> <span class="font-semibold">${_escapeHtml(diff.note || '-')}</span></div>
            </div>
            <div class="mt-4">
                <h4 class="font-semibold text-slate-800 mb-2">What bots miss</h4>
                ${missing.length ? `<ul class="list-disc pl-5 text-sm text-slate-700">${missing.map((x) => `<li>${_escapeHtml(x)}</li>`).join('')}</ul>` : '<p class="text-sm text-slate-500">No major gaps detected.</p>'}
            </div>
            <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <h4 class="font-semibold text-slate-800 mb-2">Added links</h4>
                    <div class="text-xs bg-slate-50 border rounded-lg p-3 max-h-52 overflow-auto">${(links.added_top || []).map((x) => `<div class="break-all py-0.5">${_escapeHtml(x)}</div>`).join('') || '<span class="text-slate-500">None</span>'}</div>
                </div>
                <div>
                    <h4 class="font-semibold text-slate-800 mb-2">Removed links</h4>
                    <div class="text-xs bg-slate-50 border rounded-lg p-3 max-h-52 overflow-auto">${(links.removed_top || []).map((x) => `<div class="break-all py-0.5">${_escapeHtml(x)}</div>`).join('') || '<span class="text-slate-500">None</span>'}</div>
                </div>
            </div>
        `;
    }

    function renderPolicies(result) {
        const policies = result?.policies || {};
        const robots = policies.robots || {};
        const profiles = robots.profiles || {};
        const meta = policies.meta || {};
        const rows = Object.keys(profiles).map((key) => {
            const p = profiles[key] || {};
            return `<tr class="border-t"><td class="p-2">${_escapeHtml(key)}</td><td class="p-2">${p.allowed ? 'Allowed' : 'Disallowed'}</td><td class="p-2">${_escapeHtml(p.reason || '-')}</td></tr>`;
        }).join('');
        return `
            <div class="text-sm">
                <div class="bg-white border rounded-lg p-3 mb-4">
                    <div><span class="text-slate-500">robots.txt URL:</span> <span class="font-medium break-all">${_escapeHtml(robots.url || '-')}</span></div>
                    <div><span class="text-slate-500">Final URL:</span> <span class="font-medium break-all">${_escapeHtml(robots.final_url || '-')}</span></div>
                    <div><span class="text-slate-500">Status:</span> <span class="font-medium">${_safeNum(robots.status_code, '-')}</span></div>
                </div>
                <div class="overflow-auto border rounded-lg">
                    <table class="min-w-full text-sm">
                        <thead class="bg-slate-50"><tr><th class="text-left p-2">Profile</th><th class="text-left p-2">Access</th><th class="text-left p-2">Reason</th></tr></thead>
                        <tbody>${rows || '<tr><td class="p-2" colspan="3">No profile data</td></tr>'}</tbody>
                    </table>
                </div>
                <div class="mt-4 bg-white border rounded-lg p-3">
                    <div><span class="text-slate-500">Meta robots:</span> <span class="font-medium">${_escapeHtml(meta.meta_robots || '-')}</span></div>
                    <div><span class="text-slate-500">X-Robots-Tag:</span> <span class="font-medium">${_escapeHtml(meta.x_robots_tag || '-')}</span></div>
                </div>
            </div>
        `;
    }

    function activateTab(tabId) {
        document.querySelectorAll('[data-llm-tab]').forEach((btn) => {
            const active = btn.getAttribute('data-llm-tab') === tabId;
            btn.classList.toggle('bg-slate-700', active);
            btn.classList.toggle('text-white', active);
            btn.classList.toggle('bg-slate-100', !active);
            btn.classList.toggle('text-slate-700', !active);
        });
        document.querySelectorAll('[data-llm-panel]').forEach((panel) => {
            panel.classList.toggle('hidden', panel.getAttribute('data-llm-panel') !== tabId);
        });
    }

    function renderTabs(result) {
        if (!resultBox) return;
        resultBox.innerHTML = `
            <div class="mb-4 flex flex-wrap gap-2">
                <button class="px-3 py-2 rounded-lg text-sm bg-slate-700 text-white" data-llm-tab="summary">Summary</button>
                <button class="px-3 py-2 rounded-lg text-sm bg-slate-100 text-slate-700" data-llm-tab="nojs">No-JS snapshot</button>
                <button class="px-3 py-2 rounded-lg text-sm bg-slate-100 text-slate-700" data-llm-tab="rendered">Rendered snapshot</button>
                <button class="px-3 py-2 rounded-lg text-sm bg-slate-100 text-slate-700" data-llm-tab="diff">Diff</button>
                <button class="px-3 py-2 rounded-lg text-sm bg-slate-100 text-slate-700" data-llm-tab="policies">Policies</button>
                ${result?.llm ? '<button class="px-3 py-2 rounded-lg text-sm bg-slate-100 text-slate-700" data-llm-tab="llm">LLM Simulation</button>' : ''}
                <button class="px-3 py-2 rounded-lg text-sm bg-slate-100 text-slate-700" data-llm-tab="export">Export JSON</button>
            </div>
            <div data-llm-panel="summary">${renderSummary(result)}</div>
            <div class="hidden" data-llm-panel="nojs">${renderSnapshot(result?.nojs, 'No-JS snapshot')}</div>
            <div class="hidden" data-llm-panel="rendered">${renderSnapshot(result?.rendered, 'Rendered snapshot')}</div>
            <div class="hidden" data-llm-panel="diff">${renderDiff(result)}</div>
            <div class="hidden" data-llm-panel="policies">${renderPolicies(result)}</div>
            ${result?.llm ? `<div class="hidden" data-llm-panel="llm">
                <div class="bg-white border rounded-lg p-4 text-sm space-y-2">
                    <div><span class="text-slate-500">Summary:</span> <div class="font-medium text-slate-800">${_escapeHtml(result.llm.summary || '-')}</div></div>
                    <div><span class="text-slate-500">Key facts:</span> ${(result.llm.key_facts || []).length ? `<ul class="list-disc pl-4">${result.llm.key_facts.map((x)=>`<li>${_escapeHtml(x)}</li>`).join('')}</ul>` : '<span class="text-slate-500">None</span>'}</div>
                    <div><span class="text-slate-500">Entities:</span> ${(result.llm.entities || []).join(', ') || '—'}</div>
                    <div><span class="text-slate-500">Scores:</span> citation ${_safeNum((result.llm.scores||{}).citation_likelihood,'-')} / reco ${_safeNum((result.llm.scores||{}).recommendation_likelihood,'-')} / hallucination ${_safeNum((result.llm.scores||{}).hallucination_risk,'-')} / answer quality ${_safeNum((result.llm.scores||{}).answer_quality_score,'-')}</div>
                </div>
            </div>` : ''}
            <div class="hidden" data-llm-panel="export">
                <p class="text-sm text-slate-600 mb-3">Скачать полный JSON payload результата.</p>
                <button id="llm-inline-download" class="bg-slate-700 text-white px-4 py-2 rounded-lg hover:bg-slate-800">Download JSON</button>
            </div>
        `;
        resultBox.querySelectorAll('[data-llm-tab]').forEach((btn) => {
            btn.addEventListener('click', () => activateTab(btn.getAttribute('data-llm-tab')));
        });
        activateTab('summary');

        const inlineDownload = document.getElementById('llm-inline-download');
        if (inlineDownload) {
            inlineDownload.addEventListener('click', () => {
                if (!latestResult) return;
                const blob = new Blob([JSON.stringify(latestResult, null, 2)], { type: 'application/json' });
                const href = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = href;
                a.download = `llm_crawler_${jobId}.json`;
                a.click();
                URL.revokeObjectURL(href);
            });
        }
    }

    async function poll() {
        try {
            const response = await fetch(`${LLM_API_BASE}/jobs/${encodeURIComponent(jobId)}`);
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                const message = data?.detail || data?.error || `HTTP ${response.status}`;
                throw new Error(message);
            }
            setProgress(data.status, data.progress, data.status_message || data.status);
            if (data.status === 'done') {
                latestResult = data.result || null;
                renderTabs(latestResult || {});
                if (downloadBtn) {
                    downloadBtn.disabled = false;
                    downloadBtn.classList.remove('opacity-50');
                }
                if (downloadDocxBtn) {
                    downloadDocxBtn.disabled = false;
                    downloadDocxBtn.classList.remove('opacity-50');
                }
                if (pollHandle) {
                    clearInterval(pollHandle);
                    pollHandle = null;
                }
            } else if (data.status === 'error') {
                if (errorBox) {
                    errorBox.classList.remove('hidden');
                    errorBox.textContent = data.error || 'Job failed';
                }
                if (pollHandle) {
                    clearInterval(pollHandle);
                    pollHandle = null;
                }
            }
        } catch (error) {
            if (errorBox) {
                errorBox.classList.remove('hidden');
                errorBox.textContent = _humanizeNetworkError(error) || 'Polling failed';
            }
            if (pollHandle) {
                clearInterval(pollHandle);
                pollHandle = null;
            }
        }
    }

    if (downloadBtn) {
        downloadBtn.addEventListener('click', () => {
            if (!latestResult) return;
            const blob = new Blob([JSON.stringify(latestResult, null, 2)], { type: 'application/json' });
            const href = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = href;
            a.download = `llm_crawler_${jobId}.json`;
            a.click();
            URL.revokeObjectURL(href);
        });
    }
    if (downloadDocxBtn) {
        downloadDocxBtn.addEventListener('click', async () => {
            try {
                const resp = await fetch(`${LLM_API_BASE}/jobs/${encodeURIComponent(jobId)}/report.docx`);
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const blob = await resp.blob();
                const href = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = href;
                a.download = `llm_crawler_${jobId}.docx`;
                a.click();
                URL.revokeObjectURL(href);
            } catch (e) {
                showToast(_humanizeNetworkError(e) || 'DOCX export failed', 'error');
            }
        });
    }

    setProgress('queued', 0, 'queued');
    poll();
    pollHandle = setInterval(poll, 2000);
}

window.startLlmCrawlerTask = startLlmCrawlerTask;
window.initLlmCrawlerResult = initLlmCrawlerResult;
