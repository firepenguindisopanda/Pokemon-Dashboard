/* Analytics dashboard charts — extracted from pokemon_dashboard.html in T21.
 *
 * Carried over unchanged. Depends on dashboard.js (apiFetch, waitForAnalytics,
 * showError, safeCreateChart, readPageData, analyticsReady) and on Chart.js,
 * both loaded ahead of this file.
 */

/* global apiFetch, waitForAnalytics, showError, safeCreateChart, readPageData,
   analyticsReady */

// Server-rendered, so it cannot live in a static file. See the JSON island in
// pokemon_dashboard.html.
const typeColors = readPageData('type-colors-data') || {};

// ── Load overview data ──
async function loadOverviewData() {
    if (!analyticsReady) {
        document.getElementById('overview-loading').style.display = 'block';
        const ready = await waitForAnalytics();
        if (!ready) {
            showError('Analytics failed to initialize. Reload to try again.');
            document.getElementById('overview-loading').style.display = 'none';
            return;
        }
    }
    try {
        document.getElementById('overview-loading').style.display = 'block';
        const response = await apiFetch('/api/pokemon-analytics/stats');
        if (!response.ok) throw new Error('Failed to load stats');
        const data = await response.json();
        document.getElementById('overview-loading').style.display = 'none';
        document.getElementById('overview-content').style.display = 'block';
        document.getElementById('total-pokemon').textContent = data.total_pokemon;
        document.getElementById('legendary-count').textContent = data.legendary_count;
        document.getElementById('avg-base-total').textContent = Math.round(data.stat_averages.base_total);
        document.getElementById('generation-count').textContent = Object.keys(data.generation_distribution).length;
        createTypeChart(data.type_distribution);
        createGenChart(data.generation_distribution);
        createRadarChart(data.stat_averages);
        createLegendaryCompareChart();
    } catch (error) {
        showError('Failed to load overview data');
        document.getElementById('overview-loading').style.display = 'none';
    }
}

// ── Charts ──
function createTypeChart(typeData) {
    const ctx = document.getElementById('type-chart').getContext('2d');
    const labels = Object.keys(typeData);
    const colors = labels.map(function(t) { return typeColors[t] || '#CCCCCC'; });
    safeCreateChart('type-distribution', ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{ data: Object.values(typeData), backgroundColor: colors, borderWidth: 2, borderColor: '#fff' }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, padding: 15, boxWidth: 10 } } } }
    });
}

function createGenChart(genData) {
    const ctx = document.getElementById('gen-chart').getContext('2d');
    const labels = Object.keys(genData).sort(function(a,b) { return parseInt(a) - parseInt(b); });
    const values = labels.map(function(l) { return genData[l]; });
    safeCreateChart('gen-distribution', ctx, {
        type: 'bar',
        data: {
            labels: labels.map(function(l) { return 'Gen ' + l; }),
            datasets: [{ label: 'Pokemon Count', data: values, backgroundColor: '#667eea', borderRadius: 4 }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, ticks: { stepSize: 50 } }, x: { grid: { display: false } } } }
    });
}

function createRadarChart(statAverages) {
    const ctx = document.getElementById('radar-chart').getContext('2d');
    const labels = ['HP', 'Attack', 'Defense', 'Sp. Atk', 'Sp. Def', 'Speed'];
    const mapKeys = { 'HP': 'hp', 'Attack': 'attack', 'Defense': 'defense', 'Sp. Atk': 'sp_attack', 'Sp. Def': 'sp_defense', 'Speed': 'speed' };
    const values = labels.map(function(l) { return statAverages[mapKeys[l]] || 0; });

    safeCreateChart('stats-radar', ctx, {
        type: 'radar',
        data: {
            labels: labels,
            datasets: [
                { label: 'Average Stats', data: values, backgroundColor: 'rgba(102, 126, 234, 0.2)', borderColor: 'rgba(102, 126, 234, 0.9)', borderWidth: 2, pointBackgroundColor: 'rgba(102, 126, 234, 1)', pointBorderColor: '#fff', pointBorderWidth: 2, pointRadius: 4 },
                { label: 'Max (255)', data: [255, 255, 255, 255, 255, 255], backgroundColor: 'rgba(255, 99, 132, 0.05)', borderColor: 'rgba(255, 99, 132, 0.3)', borderWidth: 1, borderDash: [5, 5], pointRadius: 0, fill: false }
            ]
        },
        options: { responsive: true, maintainAspectRatio: false,
            scales: { r: { beginAtZero: true, max: 255, ticks: { stepSize: 50 } } },
            plugins: { legend: { position: 'bottom', labels: { usePointStyle: true } } } }
    });
}

function createLegendaryCompareChart() {
    const ctx = document.getElementById('legendary-compare-chart').getContext('2d');
    const labels = ['HP', 'Attack', 'Defense', 'Sp. Atk', 'Sp. Def', 'Speed'];
    // Fetch filtered stats from API — for now use all-data averages
    const s = window._statAverages || {};
    const legendaryAvg = [s.hp ? s.hp * 1.3 : 85, s.attack ? s.attack * 1.3 : 85, s.defense ? s.defense * 1.3 : 85, s.sp_attack ? s.sp_attack * 1.3 : 85, s.sp_defense ? s.sp_defense * 1.3 : 85, s.speed ? s.speed * 1.3 : 85];
    const nonLegendaryAvg = [s.hp ? s.hp * 0.95 : 70, s.attack ? s.attack * 0.95 : 70, s.defense ? s.defense * 0.95 : 70, s.sp_attack ? s.sp_attack * 0.95 : 70, s.sp_defense ? s.sp_defense * 0.95 : 70, s.speed ? s.speed * 0.95 : 70];

    safeCreateChart('legendary-compare', ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                { label: 'Legendary', data: legendaryAvg, backgroundColor: 'rgba(255, 99, 132, 0.7)', borderRadius: 3 },
                { label: 'Non-Legendary', data: nonLegendaryAvg, backgroundColor: 'rgba(102, 126, 234, 0.7)', borderRadius: 3 }
            ]
        },
        options: { responsive: true, maintainAspectRatio: false,
            scales: { y: { beginAtZero: true }, x: { grid: { display: false } } },
            plugins: { legend: { position: 'bottom', labels: { usePointStyle: true } } } }
    });
}

// ── Filter chip handlers ──
document.addEventListener('DOMContentLoaded', function() {
    // Theme setup lives in layout.html and dashboard.js now.

    // Load overview data on page load
    loadOverviewData();
});
