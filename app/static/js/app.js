/**
 * SEO Tools Platform - Main JavaScript
 */

// API base URL
const API_BASE = '/api';
const SP_SCAN_TOKEN = 'U3tLKuZZUgagLYRMGGugcgUgGQetYAkj';
const SP_SCAN_HEADER = 'X-SP-Token';
const SP_SCAN_ENABLE_HEADER = 'X-SP-Token-Enabled';

// Toast notifications
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    
    const colors = {
        info: 'bg-blue-500',
        success: 'bg-green-500',
        error: 'bg-red-500',
        warning: 'bg-yellow-500'
    };
    
    toast.className = `${colors[type]} text-white px-6 py-3 rounded-lg shadow-lg transform translate-x-full transition-transform duration-300`;
    toast.innerHTML = `
        <div class="flex items-center">
            <i class="fas ${type === 'success' ? 'fa-check' : type === 'error' ? 'fa-exclamation-circle' : 'fa-info-circle'} mr-2"></i>
            <span>${message}</span>
        </div>
    `;
    
    container.appendChild(toast);
    
    // Animate in
    setTimeout(() => {
        toast.classList.remove('translate-x-full');
    }, 100);
    
    // Remove after 3 seconds
    setTimeout(() => {
        toast.classList.add('translate-x-full');
        setTimeout(() => {
            container.removeChild(toast);
        }, 300);
    }, 3000);
}

function _spTokenCheckboxEnabled(form) {
    return Boolean(form?.querySelector('.js-sp-token-toggle')?.checked);
}

function _shouldAttachSpToken(form, endpoint, data) {
    if (!_spTokenCheckboxEnabled(form)) return false;
    if (!data || typeof data !== 'object') return false;
    if (endpoint === 'site-audit-pro') return !Boolean(data.batch_mode);
    if (endpoint === 'bot-check') return String(data.scan_mode || 'single').toLowerCase() !== 'batch';
    if (endpoint === 'core-web-vitals') return String(data.scan_mode || 'single').toLowerCase() !== 'batch' && !Boolean(data.competitor_mode);
    if (endpoint === 'onpage-audit') return !String(data.competitor_urls || '').trim();
    if (endpoint === 'competitor-compare') return false;
    return Boolean(String(data.url || '').trim());
}

function _buildSpTokenHeaders(enabled) {
    if (!enabled) return {};
    return {
        [SP_SCAN_HEADER]: SP_SCAN_TOKEN,
        [SP_SCAN_ENABLE_HEADER]: 'true',
    };
}

function initScanTokenCheckboxes() {
    const forms = document.querySelectorAll('form[onsubmit]');
    forms.forEach((form) => {
        if (!form.querySelector('input[name="url"]')) return;
        if (form.querySelector('.js-sp-token-row')) return;
        const submitButton = form.querySelector('button[type="submit"]');
        if (!submitButton) return;

        const row = document.createElement('label');
        row.className = 'js-sp-token-row flex items-center gap-2 text-sm cursor-pointer';
        row.style.color = 'var(--ds-text-secondary)';
        row.innerHTML = `
            <input type="checkbox" class="rounded js-sp-token-toggle">
            <span>SP token для single-site сканирования</span>
        `;
        submitButton.insertAdjacentElement('beforebegin', row);
    });
}

// Start task
async function startTask(event, endpoint) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    const data = {};
    
    const parseValue = (value) => {
        if (value === 'true') return true;
        if (value === 'false') return false;
        if (!isNaN(value) && value !== '') return parseInt(value);
        return value;
    };

    formData.forEach((value, key) => {
        const parsed = parseValue(value);
        if (Object.prototype.hasOwnProperty.call(data, key)) {
            if (!Array.isArray(data[key])) {
                data[key] = [data[key]];
            }
            data[key].push(parsed);
        } else {
            data[key] = parsed;
        }
    });

    ['selected_bots', 'bot_groups', 'devices', 'skip_tools'].forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(data, key) && !Array.isArray(data[key])) {
            data[key] = [data[key]];
        }
    });

    if (!Object.prototype.hasOwnProperty.call(data, 'use_proxy')) {
        data.use_proxy = false;
    }

    if (endpoint === 'site-audit-pro') {
        const scanMode = (data.scan_mode || 'crawl').toString();
        const batchMode = scanMode === 'batch';
        const rawBatch = (data.batch_urls_text || '').toString();
        const parsedBatchUrls = rawBatch
            .split(/\r?\n/)
            .map((x) => x.trim())
            .filter((x) => x.length > 0);
        if (batchMode && parsedBatchUrls.length > 1500) {
            showToast('Batch scan limit: maximum 1500 URLs', 'warning');
            return;
        }
        const batchUrls = parsedBatchUrls.slice(0, 1500);

        data.batch_mode = batchMode;
        if (batchMode) {
            if (batchUrls.length === 0) {
                showToast('Add at least one URL for batch scan', 'warning');
                return;
            }
            data.batch_urls = batchUrls;
            data.max_pages = Math.min(1500, Math.max(1, batchUrls.length));
            data.mode = 'full';
            if (!data.url || String(data.url).trim() === '') {
                data.url = batchUrls[0];
            }
        } else {
            delete data.batch_urls;
        }
        delete data.scan_mode;
        delete data.batch_urls_text;
    }
    if (endpoint === 'bot-check') {
        const scanMode = (data.scan_mode || 'single').toString();
        const batchMode = scanMode === 'batch';
        const rawBatch = (data.batch_urls_text || '').toString();
        const parsedBatchUrls = rawBatch
            .split(/\r?\n/)
            .map((x) => x.trim())
            .filter((x) => x.length > 0);
        if (batchMode && parsedBatchUrls.length > 100) {
            showToast('Batch bot check limit: maximum 100 URLs', 'warning');
            return;
        }
        const batchUrls = parsedBatchUrls.slice(0, 100);
        data.scan_mode = batchMode ? 'batch' : 'single';
        if (batchMode) {
            if (batchUrls.length === 0) {
                showToast('Add at least one URL for batch bot check', 'warning');
                return;
            }
            data.batch_urls = batchUrls;
            if (!data.url || String(data.url).trim() === '') {
                data.url = batchUrls[0];
            }
        } else {
            delete data.batch_urls;
        }
        delete data.batch_urls_text;
    }
    if (endpoint === 'core-web-vitals') {
        const scanMode = (data.scan_mode || 'single').toString();
        let batchMode = scanMode === 'batch';
        const competitorMode = Boolean(data.competitor_mode);
        if (competitorMode) {
            batchMode = true;
        }
        const rawBatch = (data.batch_urls_text || '').toString();
        const parsedBatchUrls = rawBatch
            .split(/\r?\n/)
            .map((x) => x.trim())
            .filter((x) => x.length > 0);
        if (batchMode && parsedBatchUrls.length > 999) {
            showToast('Batch Core Web Vitals limit: maximum 999 URLs', 'warning');
            return;
        }
        const batchUrls = parsedBatchUrls.slice(0, 999);
        data.scan_mode = batchMode ? 'batch' : 'single';
        data.competitor_mode = competitorMode;
        if (batchMode) {
            if (competitorMode) {
                const primaryUrl = String(data.url || '').trim();
                if (!primaryUrl) {
                    showToast('Укажите ваш сайт в поле URL для режима "Анализ конкурентов"', 'warning');
                    return;
                }
                const seen = new Set();
                const combined = [primaryUrl, ...batchUrls].filter((item) => {
                    if (!item || seen.has(item)) return false;
                    seen.add(item);
                    return true;
                });
                if (combined.length > 10) {
                    showToast('В режиме "Анализ конкурентов" максимум 10 URL (включая ваш сайт)', 'warning');
                    return;
                }
                if (combined.length < 2) {
                    showToast('Добавьте минимум один URL конкурента (второй URL в списке)', 'warning');
                    return;
                }
                data.batch_urls = combined;
                data.url = combined[0];
            } else {
                if (batchUrls.length === 0) {
                    showToast('Add at least one URL for Core Web Vitals batch scan', 'warning');
                    return;
                }
                data.batch_urls = batchUrls;
                if (!data.url || String(data.url).trim() === '') {
                    data.url = batchUrls[0];
                }
            }
        } else {
            delete data.batch_urls;
            data.competitor_mode = false;
        }
        delete data.batch_urls_text;
    }
    if (endpoint === 'clusterizer') {
        const fileInput = form.querySelector('[name="keywords_file"]');
        const hasFile = fileInput && fileInput.files && fileInput.files.length > 0;

        if (hasFile) {
            // File upload path → FormData + /upload endpoint
            const button = form.querySelector('button[type="submit"]');
            const originalText = button.innerHTML;
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Starting...';
            try {
                const uploadForm = new FormData();
                uploadForm.append('keywords_file', fileInput.files[0]);
                uploadForm.append('method', data.method || 'jaccard');
                uploadForm.append('clustering_mode', data.clustering_mode || 'balanced');
                uploadForm.append('similarity_threshold_pct', data.similarity_threshold_pct || 35);
                uploadForm.append('min_cluster_size', data.min_cluster_size || 2);
                const uploadResponse = await fetch(`${API_BASE}/tasks/clusterizer/upload`, {
                    method: 'POST',
                    body: uploadForm,
                });
                if (uploadResponse.status === 429) {
                    const errorData = await uploadResponse.json();
                    showRateLimitModal(errorData.detail);
                    return;
                }
                if (!uploadResponse.ok) {
                    let errorMessage = `HTTP ${uploadResponse.status}`;
                    try {
                        const errorPayload = await uploadResponse.json();
                        errorMessage = errorPayload?.detail || errorPayload?.error || errorPayload?.message || errorMessage;
                    } catch (_) {}
                    throw new Error(errorMessage);
                }
                const uploadResult = await uploadResponse.json();
                addToHistory({ taskId: uploadResult.task_id, tool: endpoint, url: fileInput.files[0].name, status: uploadResult.status, timestamp: new Date().toISOString() });
                showToast('Task created successfully! Redirecting...', 'success');
                setTimeout(() => { window.location.href = `/results/${uploadResult.task_id}`; }, 1000);
            } catch (error) {
                console.error('Error starting clusterizer file task:', error);
                showToast(error?.message || 'Failed to create task. Try again later.', 'error');
            } finally {
                button.disabled = false;
                button.innerHTML = originalText;
            }
            return;
        }

        // Text / textarea path
        const rawKeywords = (data.keywords_text || '').toString();
        const sourceLines = rawKeywords.replace(/\r/g, '\n').split('\n');
        const lines = [];
        let quoteBuffer = [];
        let inQuoteBlock = false;
        for (const rawLine of sourceLines) {
            const line = String(rawLine || '').trim();
            if (!line && !inQuoteBlock) continue;
            if (!inQuoteBlock) {
                const startsQuote = /^["']/.test(line);
                const endsQuote = /["']$/.test(line);
                if (startsQuote && !endsQuote) {
                    quoteBuffer = [line];
                    inQuoteBlock = true;
                    continue;
                }
                lines.push(line);
                continue;
            }
            quoteBuffer.push(line);
            if (/["']$/.test(line)) {
                lines.push(quoteBuffer.join('\n'));
                quoteBuffer = [];
                inQuoteBlock = false;
            }
        }
        if (quoteBuffer.length > 0) {
            lines.push(quoteBuffer.join('\n'));
        }

        const parsedKeywords = [];
        for (const line of lines) {
            // Handle quoted multiline blocks: "keyword\n123"
            if (line.includes('\n')) {
                const normalized = line.replace(/\r/g, '\n');
                const blockMatch = normalized.match(/^["']?(.+?)\n([0-9]+(?:[.,][0-9]+)?)["']?$/s);
                if (blockMatch) {
                    parsedKeywords.push(`${blockMatch[1].trim()};${blockMatch[2].trim()}`);
                    continue;
                }
            }
            // Ignore pure numeric leftovers (e.g. split frequency rows).
            if (/^[+-]?\d+(?:[.,]\d+)?$/.test(line.replace(/["']/g, '').trim())) {
                continue;
            }
            // Keep "keyword;123" or "keyword<TAB>123" lines intact.
            if (/^.+(?:\t+|[;>|:]|,\s*)\s*\d+(?:[.,]\d+)?\s*$/u.test(line)) {
                parsedKeywords.push(line);
                continue;
            }
            // Backward compatibility for comma/semicolon keyword lists in one line.
            if (!line.includes('\t') && /[;,]/.test(line)) {
                const parts = line.split(/[;,]+/).map((x) => x.trim()).filter((x) => x.length > 0);
                parsedKeywords.push(...parts);
                continue;
            }
            parsedKeywords.push(line);
        }
        if (parsedKeywords.length === 0) {
            showToast('Добавьте хотя бы один ключ или выберите файл', 'warning');
            return;
        }
        if (parsedKeywords.length > 25000) {
            showToast('Лимит кластеризатора: максимум 25 000 ключей', 'warning');
            return;
        }
        data.keywords_text = parsedKeywords.join('\n');
    }

    // OnPage: switch to competitor-compare endpoint when competitor URLs are provided
    if (endpoint === 'onpage-audit') {
        const rawCompUrls = (data.competitor_urls || '').toString().trim();
        if (rawCompUrls) {
            const compUrls = rawCompUrls.replace(/\r/g, '\n').split('\n').map(u => u.trim()).filter(u => u.length > 0);
            if (compUrls.length > 0) {
                data.competitor_urls = compUrls.slice(0, 5);
                endpoint = 'competitor-compare';
            }
        }
        if (!data.competitor_urls || (typeof data.competitor_urls === 'string' && !data.competitor_urls.trim())) {
            delete data.competitor_urls;
        }
    }

    // Show loading state
    const button = form.querySelector('button[type="submit"]');
    const originalText = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Starting...';

    try {
        const spHeaders = _buildSpTokenHeaders(_shouldAttachSpToken(form, endpoint, data));
        const response = await fetch(`${API_BASE}/tasks/${endpoint}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...spHeaders,
            },
            body: JSON.stringify(data)
        });
        
        if (response.status === 429) {
            // Rate limit exceeded
            const errorData = await response.json();
            showRateLimitModal(errorData.detail);
            return;
        }
        
        if (!response.ok) {
            let errorMessage = `HTTP ${response.status}`;
            try {
                const errorPayload = await response.json();
                errorMessage = errorPayload?.detail || errorPayload?.error || errorPayload?.message || errorMessage;
            } catch (_) {
                // Keep default HTTP status text when body is not JSON.
            }
            throw new Error(errorMessage);
        }
        
        const result = await response.json();
        
        // Add to history
        addToHistory({
            taskId: result.task_id,
            tool: endpoint,
            url: data.url,
            status: result.status,
            timestamp: new Date().toISOString()
        });
        
        showToast('Task created successfully! Redirecting...', 'success');
        
        // Redirect to results page
        setTimeout(() => {
            window.location.href = `/results/${result.task_id}`;
        }, 1000);
        
    } catch (error) {
        console.error('Error starting task:', error);
        showToast(error?.message || 'Failed to create task. Try again later.', 'error');
    } finally {
        button.disabled = false;
        button.innerHTML = originalText;
    }
}

async function startBatchTask(event) {
    event.preventDefault();
    const form = event.target;
    const tool = form.querySelector('[name="tool"]').value;
    const urlsText = form.querySelector('[name="urls"]').value;
    const useProxy = form.querySelector('[name="use_proxy"]')?.checked || false;

    const urls = urlsText.split('\n').map(u => u.trim()).filter(u => u.length > 0);
    if (urls.length === 0) { showToast('Add at least one URL', 'warning'); return; }
    if (urls.length > 50) { showToast('Maximum 50 URLs', 'warning'); return; }

    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Starting...';

    try {
        const resp = await fetch('/api/tasks/batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({tool, urls, use_proxy: useProxy}),
        });
        const data = await resp.json();
        if (data.task_id) {
            window.location.href = '/results/' + data.task_id;
        } else {
            showToast(data.error || 'Failed to start batch', 'error');
        }
    } catch(e) {
        showToast('Network error', 'error');
    } finally {
        button.disabled = false;
        button.innerHTML = '<i class="fas fa-play"></i>Запустить Batch';
    }
}

async function startLinkProfileTask(event) {
    event.preventDefault();
    const form = event.target;
    const formData = new FormData(form);

    const button = form.querySelector('button[type="submit"]');
    const originalText = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Starting...';

    try {
        const response = await fetch(`${API_BASE}/tasks/link-profile-audit`, {
            method: 'POST',
            body: formData
        });

        if (response.status === 429) {
            const errorData = await response.json();
            showRateLimitModal(errorData.detail);
            return;
        }

        if (!response.ok) {
            let errorMessage = `HTTP ${response.status}`;
            try {
                const errorPayload = await response.json();
                errorMessage = errorPayload?.detail || errorPayload?.error || errorPayload?.message || errorMessage;
            } catch (_) {
                // Keep default status message.
            }
            throw new Error(errorMessage);
        }

        const result = await response.json();
        addToHistory({
            taskId: result.task_id,
            tool: 'link-profile-audit',
            url: String(formData.get('our_domain') || ''),
            status: result.status,
            timestamp: new Date().toISOString()
        });

        showToast('Task created successfully! Redirecting...', 'success');
        setTimeout(() => {
            window.location.href = `/results/${result.task_id}`;
        }, 1000);
    } catch (error) {
        console.error('Error starting link profile task:', error);
        showToast(error?.message || 'Failed to create task. Try again later.', 'error');
    } finally {
        button.disabled = false;
        button.innerHTML = originalText;
    }
}

function initSiteAuditProBatchUI() {
    const form = document.querySelector('form[data-tool="site-audit-pro"]');
    if (!form) return;

    const modeSelect = form.querySelector('.js-sitepro-scan-mode');
    const batchBox = form.querySelector('.js-sitepro-batch-box');
    const batchFlag = form.querySelector('.js-sitepro-batch-flag');
    const maxPagesInput = form.querySelector('input[name="max_pages"]');
    const rootUrlInput = form.querySelector('.js-sitepro-root-url');
    const reportModeSelect = form.querySelector('select[name="mode"]');
    if (!modeSelect || !batchBox || !batchFlag || !maxPagesInput || !rootUrlInput || !reportModeSelect) return;

    const applyCrawlLimitByMode = () => {
        const isBatch = modeSelect.value === 'batch';
        const isFull = reportModeSelect.value === 'full';
        if (isBatch) return;
        const crawlMax = isFull ? 1500 : 200;
        maxPagesInput.max = String(crawlMax);
        if (parseInt(maxPagesInput.value || '1', 10) > crawlMax) {
            maxPagesInput.value = String(crawlMax);
        }
        maxPagesInput.title = `Max pages in crawl mode (${isFull ? 'full' : 'quick'})`;
    };

    const sync = () => {
        const isBatch = modeSelect.value === 'batch';
        batchBox.classList.toggle('hidden', !isBatch);
        batchFlag.value = isBatch ? 'true' : 'false';
        if (isBatch) {
            maxPagesInput.value = '1500';
            maxPagesInput.max = '1500';
            maxPagesInput.title = 'Max URLs in batch mode';
            rootUrlInput.required = false;
            rootUrlInput.disabled = true;
            reportModeSelect.value = 'full';
            reportModeSelect.disabled = true;
        } else {
            applyCrawlLimitByMode();
            rootUrlInput.disabled = false;
            rootUrlInput.required = true;
            reportModeSelect.disabled = false;
        }
    };

    modeSelect.addEventListener('change', sync);
    reportModeSelect.addEventListener('change', applyCrawlLimitByMode);
    sync();
}

function initBotBatchUI() {
    const form = document.querySelector("form[onsubmit*=\"bot-check\"]");
    if (!form) return;
    const modeSelect = form.querySelector('.js-bot-scan-mode');
    const batchBox = form.querySelector('.js-bot-batch-box');
    const rootUrlInput = form.querySelector('input[name="url"]');
    if (!modeSelect || !batchBox || !rootUrlInput) return;

    const sync = () => {
        const isBatch = modeSelect.value === 'batch';
        batchBox.classList.toggle('hidden', !isBatch);
        if (isBatch) {
            rootUrlInput.required = false;
        } else {
            rootUrlInput.required = true;
        }
    };
    modeSelect.addEventListener('change', sync);
    sync();
}

function initCoreWebVitalsBatchUI() {
    const form = document.querySelector("form[onsubmit*=\"core-web-vitals\"]");
    if (!form) return;
    const modeSelect = form.querySelector('.js-cwv-scan-mode');
    const batchBox = form.querySelector('.js-cwv-batch-box');
    const urlInput = form.querySelector('.js-cwv-url');
    const competitorCheckbox = form.querySelector('.js-cwv-competitor-mode');
    const competitorHint = form.querySelector('.js-cwv-competitor-hint');
    if (!modeSelect || !batchBox || !urlInput) return;

    const sync = () => {
        const isBatch = modeSelect.value === 'batch';
        const competitorMode = Boolean(competitorCheckbox?.checked);
        batchBox.classList.toggle('hidden', !isBatch);
        if (!isBatch && competitorCheckbox) {
            competitorCheckbox.checked = false;
        }
        if (isBatch && competitorMode) {
            urlInput.required = true;
            urlInput.placeholder = 'Ваш сайт (primary), например https://example.com';
        } else {
            urlInput.required = !isBatch;
            urlInput.placeholder = 'example.com или https://example.com/page';
        }
        if (competitorHint) {
            competitorHint.classList.toggle('hidden', !(isBatch && competitorMode));
        }
    };
    modeSelect.addEventListener('change', sync);
    if (competitorCheckbox) {
        competitorCheckbox.addEventListener('change', sync);
    }
    sync();
}

// Show rate limit modal
function showRateLimitModal(detail) {
    const modal = document.getElementById('rate-limit-modal');
    const limit = detail?.limit || 999;
    const resetIn = detail?.reset_in || 10;
    const minutes = Math.ceil(resetIn / 60);
    
    document.getElementById('modal-limit').textContent = limit;
    document.getElementById('modal-time').textContent = minutes;
    
    modal.classList.remove('hidden');
    modal.classList.add('flex');
}

// Close rate limit modal
function closeRateLimitModal() {
    const modal = document.getElementById('rate-limit-modal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}

// Update rate limit badge
async function updateRateLimitBadge() {
    try {
        const response = await fetch(`${API_BASE}/rate-limit`);
        const data = await response.json();
        
        const badge = document.getElementById('rate-limit-badge');
        const text = document.getElementById('rate-limit-text');
        
        text.textContent = `${data.remaining}/${data.limit}`;
        badge.classList.remove('hidden');
        
        if (data.remaining <= 2) {
            badge.classList.add('bg-red-500');
            badge.classList.remove('bg-white/20');
        }
    } catch (error) {
        console.error('Error fetching rate limit:', error);
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    updateRateLimitBadge();
    initSiteAuditProBatchUI();
    initBotBatchUI();
    initCoreWebVitalsBatchUI();
    initScanTokenCheckboxes();
    
    // Update badge every minute
    setInterval(updateRateLimitBadge, 60000);
});

// Export functions for use in other scripts
window.startBatchTask = startBatchTask;
window.startLinkProfileTask = startLinkProfileTask;
window.startTask = startTask;
window.showToast = showToast;
window.showRateLimitModal = showRateLimitModal;
window.closeRateLimitModal = closeRateLimitModal;
window._spTokenCheckboxEnabled = _spTokenCheckboxEnabled;
window._buildSpTokenHeaders = _buildSpTokenHeaders;
