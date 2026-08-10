/* Pokemon Dashboard — shared JS utilities */

/* global Chart, bootstrap */

// ── Constants ──
const TEAM_COLORS = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40'];

// ── Server-rendered page data ──
// T21 moved the page scripts into static files, which cannot hold Jinja
// output. Each page emits its data as a <script type="application/json">
// island instead; `| tojson` escapes angle brackets, so no value can close the
// tag early. Returns null when the island is absent, which is the normal case
// on the six pages that have no data to pass.
function readPageData(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return null;
    try {
        return JSON.parse(el.textContent);
    } catch (e) {
        console.error('Malformed page data in #' + elementId, e);
        return null;
    }
}

window.readPageData = readPageData;

// ── Chart.js Memory Management ──
const activeCharts = {};

function destroyChart(key) {
    if (activeCharts[key]) { activeCharts[key].destroy(); delete activeCharts[key]; }
}

function safeCreateChart(key, ctx, config) {
    destroyChart(key);
    activeCharts[key] = new Chart(ctx, config);
    return activeCharts[key];
}

// ── Theme Toggle ──
// The theme itself is applied by an inline script in layout.html's <head>, so
// it is already in place before the first paint. This only keeps the button's
// icon and label in sync and handles clicks.
function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try {
        localStorage.setItem('pokemon-theme', theme);
    } catch (e) {
        /* Private mode: the theme still applies, it just will not persist. */
    }
    const icon = document.getElementById('theme-icon');
    const label = document.getElementById('theme-label');
    if (icon) icon.className = theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
    if (label) label.textContent = theme === 'dark' ? 'Light' : 'Dark';
}

function currentTheme() {
    return document.documentElement.getAttribute('data-theme') || 'light';
}

// Wired here rather than per page: the toggle lives in the shared layout, and
// two of the nine pages used to carry their own copy of this.
document.addEventListener('DOMContentLoaded', function () {
    applyTheme(currentTheme());
    const toggle = document.getElementById('theme-toggle');
    if (toggle) {
        toggle.addEventListener('click', function () {
            applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
        });
    }
});

// ── Toast Notifications ──
function showToast(message, type) {
    if (type === undefined) type = 'success';
    const container = document.getElementById('toast-container');
    if (!container) return;

    const icons = { success: 'fa-check-circle', error: 'fa-exclamation-circle', info: 'fa-info-circle', warning: 'fa-exclamation-triangle' };
    // T22: bg-info and bg-warning are pale Bootstrap fills — white text on
    // #0DCAF0 is 1.96:1. Those two take dark text; the dark fills keep white.
    const bgClasses = { success: 'bg-success', error: 'bg-danger', info: 'bg-info', warning: 'bg-warning' };
    const darkText = { info: true, warning: true };

    const toastEl = document.createElement('div');
    toastEl.className = 'toast align-items-center border-0 '
        + (darkText[type] ? 'text-dark ' : 'text-white ')
        + (bgClasses[type] || 'bg-secondary');
    // T22: axe flagged the close button as `button-name` (critical) — it renders
    // as an empty <button>, so a screen reader announces nothing at all.
    // Errors interrupt (assertive); routine confirmations wait their turn.
    toastEl.setAttribute('role', type === 'error' ? 'alert' : 'status');
    toastEl.setAttribute('aria-live', type === 'error' ? 'assertive' : 'polite');
    toastEl.setAttribute('aria-atomic', 'true');
    toastEl.innerHTML = '<div class="d-flex"><div class="toast-body"><i class="fas ' + (icons[type] || 'fa-info-circle') + ' me-2"></i>' + message + '</div><button type="button" class="btn-close ' + (darkText[type] ? '' : 'btn-close-white ') + 'me-2 m-auto" data-bs-dismiss="toast" aria-label="Close notification"></button></div>';
    container.appendChild(toastEl);
    const toast = new bootstrap.Toast(toastEl, { delay: 4000 });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', function() { toastEl.remove(); });
}

function showError(message) { showToast(message, 'error'); }
function showSuccess(message) { showToast(message, 'success'); }

// ── Analytics Readiness Polling ──
let analyticsReady = false;

async function waitForAnalytics() {
    const statusEl = document.getElementById('analytics-status-banner');
    if (statusEl) statusEl.style.display = '';
    while (true) {
        try {
            const resp = await fetch('/api/pokemon-analytics/status');
            const data = await resp.json();
            if (data.ready) {
                analyticsReady = true;
                if (statusEl) statusEl.style.display = 'none';
                console.log('Analytics ready');
                return true;
            }
            if (data.error) {
                if (statusEl) {
                    statusEl.innerHTML = '<i class="fas fa-exclamation-triangle me-2"></i>Analytics failed: ' +
                        data.error + ' <button class="btn btn-sm btn-outline-light ms-2" onclick="location.reload()">Retry</button>';
                }
                return false;
            }
        } catch (e) {
            // Server not reachable yet — keep polling
        }
        // Poll every 2 seconds
        await new Promise(function(resolve) { setTimeout(resolve, 2000); });
    }
}

// ── Token Refresh Interceptor ──
// Wraps fetch() calls with automatic 401 → refresh → retry logic.
// Prevents concurrent refresh calls via a simple flag + subscriber pattern.

let isRefreshing = false;
let refreshSubscribers = [];

// ── CSRF ──
// Auth rides in cookies, so the browser attaches it to cross-site requests as
// well. flask-jwt-extended sets a readable csrf_access_token cookie; echoing
// it back in a header proves the request came from our own page, which a
// cross-origin attacker cannot do.
const CSRF_SAFE_METHODS = ['GET', 'HEAD', 'OPTIONS'];

function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^|; )' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[2]) : null;
}

function csrfToken() { return getCookie('csrf_access_token'); }
function csrfRefreshToken() { return getCookie('csrf_refresh_token'); }

/** Attach the CSRF header to any state-changing request. */
function withCsrf(options) {
    const method = (options.method || 'GET').toUpperCase();
    if (CSRF_SAFE_METHODS.indexOf(method) !== -1) return options;

    const token = csrfToken();
    if (!token) return options;

    options.headers = Object.assign({}, options.headers);
    options.headers['X-CSRF-TOKEN'] = token;
    return options;
}

window.getCookie = getCookie;
window.csrfToken = csrfToken;

function onRefreshed() {
    refreshSubscribers.forEach(function(callback) { callback(); });
    refreshSubscribers = [];
}

function addRefreshSubscriber(callback) {
    refreshSubscribers.push(callback);
}

/**
 * Wrapped fetch with automatic 401 handling.
 * On a 401 response: POST /api/auth/refresh once; on success retry the
 * original request; on failure redirect to login. If a refresh is already
 * in-flight, queue the retry via subscribers.
 */
async function apiFetch(url, options) {
    if (!options) options = {};

    // Clone options to avoid mutation from retry
    options = withCsrf(Object.assign({}, options));

    let response = await fetch(url, options);

    // Not a 401 → return as-is
    if (response.status !== 401) {
        return response;
    }

    // If a refresh is already in progress, wait for it and retry
    if (isRefreshing) {
        return new Promise(function(resolve) {
            addRefreshSubscriber(function() {
                resolve(fetch(url, options));
            });
        });
    }

    // Start refresh
    isRefreshing = true;

    try {
        // The refresh endpoint validates against its own CSRF token, not the
        // access one — they are separate cookies.
        const refreshHeaders = {};
        const refreshCsrf = csrfRefreshToken();
        if (refreshCsrf) refreshHeaders['X-CSRF-TOKEN'] = refreshCsrf;

        const refreshResponse = await fetch('/api/auth/refresh', {
            method: 'POST',
            credentials: 'same-origin',
            headers: refreshHeaders,
        });

        if (!refreshResponse.ok) {
            // Refresh failed — redirect to login
            isRefreshing = false;
            refreshSubscribers = [];
            window.location.href = '/';
            return response; // unreachable, but return for type safety
        }

        // Refresh succeeded — notify subscribers
        isRefreshing = false;
        onRefreshed();

        // Retry the original request
        return fetch(url, options);

    } catch (error) {
        // Network error during refresh — redirect to login
        isRefreshing = false;
        refreshSubscribers = [];
        window.location.href = '/';
        return response;
    }
}

// Export apiFetch globally
window.apiFetch = apiFetch;
