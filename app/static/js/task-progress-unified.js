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
    const _firstFinite = (...values) => {
        for (const value of values) {
            const num = Number(value);
            if (Number.isFinite(num)) return num;
        }
        return NaN;
    };

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
    const cwvEntry = toolResults.cwv || toolResults.core_web_vitals || {};
    const cwvPayload = cwvEntry?.results?.results || cwvEntry?.results || cwvEntry || {};
    if (Object.keys(cwvEntry || {}).length > 0) {
        if (cwvPayload.combined) {
            const mobilePerf = _firstFinite(
                cwvPayload.mobile?.summary?.performance_score,
                cwvPayload.mobile?.categories?.performance,
                cwvPayload.summary_mobile?.performance_score,
                cwvPayload.mobile_score
            );
            const desktopPerf = _firstFinite(
                cwvPayload.desktop?.summary?.performance_score,
                cwvPayload.desktop?.categories?.performance,
                cwvPayload.summary_desktop?.performance_score,
                cwvPayload.desktop_score
            );
            if (Number.isFinite(mobilePerf)) scores.cwv_mobile = mobilePerf;
            if (Number.isFinite(desktopPerf)) scores.cwv_desktop = desktopPerf;
            const avgParts = [scores.cwv_mobile, scores.cwv_desktop].filter(v => Number.isFinite(Number(v)));
            if (avgParts.length > 0) scores.cwv_avg = Math.round((avgParts.reduce((a, b) => Number(a) + Number(b), 0) / avgParts.length) * 10) / 10;
        } else {
            const perf = _firstFinite(
                cwvPayload.summary?.performance_score,
                cwvPayload.categories?.performance,
                cwvPayload.performance_score,
                cwvPayload.score
            );
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
    const scoreKeys = ['onpage', 'render', 'mobile_friendly', 'bot_accessibility', 'redirect', 'cwv_mobile', 'cwv_desktop', 'cwv_avg', 'robots_ok']
        .filter(k => scores[k] !== undefined && scores[k] !== null);
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
                const mobilePerf = _firstFinite(
                    rd.mobile?.summary?.performance_score,
                    rd.mobile?.categories?.performance,
                    rd.summary_mobile?.performance_score,
                    rd.mobile_score
                );
                const desktopPerf = _firstFinite(
                    rd.desktop?.summary?.performance_score,
                    rd.desktop?.categories?.performance,
                    rd.summary_desktop?.performance_score,
                    rd.desktop_score
                );
                const parts = [mobilePerf, desktopPerf].filter(v => v > 0);
                return parts.length ? Math.round(parts.reduce((a, b) => a + b, 0) / parts.length) : Number(scores.cwv_avg ?? 0);
            }
            return _firstFinite(
                rd.summary?.performance_score,
                rd.categories?.performance,
                rd.performance_score,
                rd.score,
                scores.cwv_avg ?? 0
            );
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
            const statusLabel = status === 'valid' ? 'Валидна' : status === 'invalid' ? 'Невалидна' : status === 'unknown' ? 'Неизвестно' : status;
            metricsHtml = `
                <div class="grid grid-cols-3 gap-3 mb-3">
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${Number(totalUrls).toLocaleString()}</div><div class="text-xs" style="color:var(--ds-text-muted)">URL в sitemap</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:var(--ds-text)">${filesCount}</div><div class="text-xs" style="color:var(--ds-text-muted)">Файлов</div></div>
                    <div class="text-center"><div class="text-xl font-bold" style="color:${status === 'valid' ? 'var(--ds-success)' : status === 'invalid' ? 'var(--ds-danger)' : 'var(--ds-warning)'}">${statusLabel}</div><div class="text-xs" style="color:var(--ds-text-muted)">Статус</div></div>
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
