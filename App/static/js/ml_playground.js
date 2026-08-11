/* ML Playground — extracted from pokemon_ml.html in T21.
 *
 * The template held 525 lines of this inline, which meant it could not be
 * cached, linted, or read without scrolling past the markup. The code is
 * carried over unchanged apart from the fix noted at displayTeam().
 *
 * Depends on dashboard.js (apiFetch, waitForAnalytics, showError, showSuccess,
 * showToast, safeCreateChart, destroyChart, TEAM_COLORS, readPageData) and on
 * Chart.js. layout.html loads dashboard.js for every page; the template loads
 * Chart.js ahead of this file.
 */

/* global apiFetch, waitForAnalytics, showError, showSuccess, showToast,
   safeCreateChart, destroyChart, TEAM_COLORS, readPageData */

// T22: team badges take the `.type-<name>` class instead of an inline
// background-color. The class carries an AA-checked text colour with it; an
// inline background left the text to inherit the page colour, which is
// 3.0:1 on fighting in light mode and 1.59:1 on grass in dark.

// Server-rendered, so it cannot live in a static file. See the JSON island in
// pokemon_ml.html.
const typeColors = readPageData('type-colors-data') || {};

let currentStats = {};

// ── Tab Management ──
function showTab(tabName) {
    document.querySelectorAll('.tab-content').forEach(function(t) { t.classList.remove('active'); });
    document.querySelectorAll('.tab-button').forEach(function(b) { b.classList.remove('active'); });
    document.getElementById(tabName).classList.add('active');
    document.querySelectorAll('.tab-button').forEach(function(btn) {
        if (btn.getAttribute('onclick') && btn.getAttribute('onclick').includes("'" + tabName + "'")) {
            btn.classList.add('active');
        }
    });
    window.location.hash = tabName;
}

// ── Keyboard Shortcuts ──
document.addEventListener('keydown', function(e) {
    if (e.ctrlKey && ['1','2'].includes(e.key)) {
        e.preventDefault();
        showTab(['team-builder', 'analytics'][parseInt(e.key) - 1]);
    }
});

// ── Team Builder Form Submission ──
document.getElementById('team-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    const btn = document.getElementById('generate-team-btn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating...';
    btn.disabled = true;
    try {
        await waitForAnalytics();
        const preferences = {};
        const playstyle = document.getElementById('playstyle').value;
        if (playstyle && playstyle.trim() !== '') preferences.playstyle = playstyle;
        const difficulty = document.getElementById('difficulty').value;
        if (difficulty && difficulty.trim() !== '') preferences.difficulty_level = difficulty;
        const includeLegendary = document.getElementById('include-legendary').value;
        if (includeLegendary && includeLegendary.trim() !== '') preferences.include_legendary = includeLegendary === 'true';
        const generation = document.getElementById('generation').value;
        if (generation && generation.trim() !== '') {
            try {
                const parts = generation.split(',').map(Number);
                if (!isNaN(parts[0]) && !isNaN(parts[1])) preferences.generation_preference = [parts[0], parts[1]];
            } catch (err) { showError('Invalid generation preference format'); }
        }
        const preferredTypesSelect = document.getElementById('preferred-types');
        const preferredTypes = Array.from(preferredTypesSelect.selectedOptions)
            .map(function(o) { return o.value; })
            .filter(function(t) { return t && t.trim() !== ''; });
        if (preferredTypes.length > 0) preferences.preferred_types = preferredTypes;
        preferences.team_role_balance = true;
        const response = await apiFetch('/api/pokemon-analytics/team-recommend', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(preferences)
        });
        if (!response.ok) {
            const errorData = await response.json().catch(function() { return { error: 'Unknown error' }; });
            throw new Error(errorData.error || 'Failed to generate team');
        }
        const data = await response.json();
        displayTeam(data, preferences);
    } catch (error) {
        showError('Failed to generate team recommendation');
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
});

// ── Display recommended team ──
// `preferences` was read from the enclosing scope, where it does not exist.
// It is a parameter now — see the T21 entry in tasks/todo.md.
function displayTeam(data, preferences) {
    const container = document.getElementById('team-container');
    const analysisContainer = document.getElementById('team-analysis');
    container.innerHTML = '';
    analysisContainer.innerHTML = '';
    data.recommended_team.forEach(function(pokemon) {
        const pokedexNum = pokemon.pokedex_number || 0;
        const type2Badge = pokemon.type2
            ? '<span class="type-badge type-' + String(pokemon.type2).toLowerCase() + '">' + pokemon.type2 + '</span>'
            : '';
        const pokemonCard = document.createElement('div');
        pokemonCard.className = 'pokemon-card';
        pokemonCard.innerHTML =
            '<div class="pokemon-sprite-wrap">' +
                '<img src="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/' + pokedexNum + '.png" alt="' + pokemon.name + '" class="pokemon-sprite" loading="lazy" onerror="this.src=\'https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/0.png\'">' +
            '</div>' +
            '<div class="pokemon-name">' + pokemon.name + '</div>' +
            '<div class="pokemon-role">' + pokemon.role + '</div>' +
            '<div class="pokemon-types">' +
                '<span class="type-badge type-' + String(pokemon.type1).toLowerCase() + '">' + pokemon.type1 + '</span>' +
                type2Badge +
            '</div>' +
            '<div class="pokemon-stats">' +
                '<div class="stat-mini"><div class="stat-mini-value">' + pokemon.stats.hp + '</div><div class="stat-mini-label">HP</div></div>' +
                '<div class="stat-mini"><div class="stat-mini-value">' + pokemon.stats.attack + '</div><div class="stat-mini-label">ATK</div></div>' +
                '<div class="stat-mini"><div class="stat-mini-value">' + pokemon.stats.speed + '</div><div class="stat-mini-label">SPD</div></div>' +
                '<div class="stat-mini"><div class="stat-mini-value">' + pokemon.stats.defense + '</div><div class="stat-mini-label">DEF</div></div>' +
                '<div class="stat-mini"><div class="stat-mini-value">' + pokemon.stats.sp_attack + '</div><div class="stat-mini-label">SP.A</div></div>' +
                '<div class="stat-mini"><div class="stat-mini-value">' + pokemon.stats.base_total + '</div><div class="stat-mini-label">TOTAL</div></div>' +
            '</div>';
        container.appendChild(pokemonCard);
    });
    const analysis = data.team_analysis;
    analysisContainer.innerHTML =
        '<div class="info-item"><div class="info-value">' + analysis.total_base_stats + '</div><div class="info-label">Total Base Stats</div></div>' +
        '<div class="info-item"><div class="info-value">' + Math.round(analysis.average_base_total) + '</div><div class="info-label">Avg Base Total</div></div>' +
        '<div class="info-item"><div class="info-value">' + analysis.legendary_count + '</div><div class="info-label">Legendaries</div></div>' +
        '<div class="info-item"><div class="info-value">' + analysis.type_coverage.length + '</div><div class="info-label">Type Coverage</div></div>' +
        '<div class="info-item"><div class="info-value">' + Math.round(analysis.synergy_score * 100) + '%</div><div class="info-label">Synergy Score</div></div>';
    document.getElementById('team-result').style.display = 'block';
    renderTeamRadar(data.recommended_team);
    fetchTypeMatchup(data.recommended_team);
    showSuccess('Team generated successfully!');
    localStorage.setItem('saved-team-prefs', JSON.stringify(preferences));
    localStorage.setItem('saved-team-data', JSON.stringify(data));
    document.getElementById('save-team-bar').style.display = 'flex';
}

// ── Copy share link ──
function copyShareLink() {
    var prefs = localStorage.getItem('saved-team-prefs');
    if (!prefs) { showError('Generate a team first!'); return; }
    var encoded = btoa(unescape(encodeURIComponent(prefs)));
    var url = window.location.origin + window.location.pathname + '?team=' + encoded;
    navigator.clipboard.writeText(url).then(function() {
        showSuccess('Share link copied to clipboard!');
    }).catch(function() {
        var input = document.createElement('input');
        input.value = url;
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        document.body.removeChild(input);
        showSuccess('Share link copied!');
    });
}

// ── Load saved/shared team ──
function loadSavedTeam(teamData, prefs) {
    if (teamData) displayTeam(teamData, prefs);
    if (prefs) {
        var p = prefs;
        if (p.playstyle) document.getElementById('playstyle').value = p.playstyle;
        if (p.difficulty_level) document.getElementById('difficulty').value = p.difficulty_level;
        if (p.include_legendary !== undefined) document.getElementById('include-legendary').value = p.include_legendary ? 'true' : 'false';
        if (p.generation_preference) {
            var genOpt = document.querySelector('#generation option[value^="' + p.generation_preference[0] + ',' + p.generation_preference[1] + '"]');
            if (genOpt) document.getElementById('generation').value = genOpt.value;
        }
        if (p.preferred_types) {
            var sel = document.getElementById('preferred-types');
            for (var j = 0; j < sel.options.length; j++) {
                if (p.preferred_types.indexOf(sel.options[j].value) !== -1) sel.options[j].selected = true;
            }
        }
    }
}

// ── Fetch Type Coverage ──

/** Escape text bound for innerHTML.
 *
 * Pokemon names come from our own dataset, so this is belt-and-braces rather
 * than a live hole — but this file builds markup by concatenation, and the one
 * place a name enters that string is the place to stop worrying about it. */
function esc(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** One matrix cell, from a multiplier to markup.
 *
 * The multiplier is compared against exact values because every one of them
 * is a product of exact binary fractions — 0, 1/4, 1/2, 1, 2, 4 — so there is
 * no rounding to be tolerant of. The old code used range comparisons plus a
 * `|| 1.0` default, which silently reclassified every immunity as neutral.
 *
 * An absent multiplier renders as a dash rather than inventing a number. */
function matchupCell(mult) {
    var styles = {
        0: ['immune', '×0'],
        0.25: ['resist-4x', '¼'],
        0.5: ['resist-2x', '½'],
        1: ['neutral', '×1'],
        2: ['weak-2x', '×2'],
        4: ['weak-4x', '×4']
    };
    if (typeof mult !== 'number' || !styles[mult]) {
        return '<td class="matchup-cell neutral">–</td>';
    }
    return '<td class="matchup-cell ' + styles[mult][0] + '">'
         + styles[mult][1] + '</td>';
}

async function fetchTypeMatchup(team) {
    const card = document.getElementById('matchup-card');
    const grid = document.getElementById('matchup-grid');
    if (!card || !grid || !team || team.length === 0) return;
    const teamNames = team.map(function(p) { return p.name; });
    try {
        await waitForAnalytics();
        const res = await apiFetch('/api/pokemon-analytics/type-coverage', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ team: teamNames })
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data.error) return;
        card.style.display = 'block';
        const allTypes = ['normal','fire','water','electric','grass','ice','fighting',
                          'poison','ground','flying','psychic','bug','rock','ghost',
                          'dragon','dark','steel','fairy'];
        let html = '<div class="d-flex justify-content-between align-items-center mb-2">';
        html += '<span><strong>Weaknesses:</strong> <span class="text-danger">' + (data.weaknesses.length || 0) + '</span> | ';
        html += '<strong>Resistances:</strong> <span class="text-success">' + (data.resistances.length || 0) + '</span> | ';
        html += '<strong>Immune:</strong> <span class="text-secondary">' + (data.immunity_count || 0) + '</span> | ';
        html += '<strong>Coverage Score:</strong> ' + Math.round(data.coverage_score * 100) + '%</span></div>';
        // tabindex=0: this scrolls horizontally at narrow widths, and axe's
        // `scrollable-region-focusable` is right that a mouse-only scroller
        // hides its overflow from keyboard users entirely.
        html += '<div class="matchup-grid-wrapper" tabindex="0" role="region" '
             + 'aria-label="Type defence matrix"><table class="matchup-table">';
        html += '<caption class="visually-hidden">Damage each team member takes '
             + 'from every attacking type</caption>';
        html += '<thead><tr><th scope="col">Pok\u00e9mon</th>';
        allTypes.forEach(function(t) {
            // Three letters fit the column; the full name stays available to a
            // screen reader rather than being abbreviated away from it.
            html += '<th scope="col" class="type-label type-' + t + '">'
                 + '<span class="visually-hidden">' + t + '</span>'
                 + '<span aria-hidden="true">' + t.substring(0, 3) + '</span></th>';
        });
        html += '</tr></thead><tbody>';

        // ONE ROW PER TEAM MEMBER.
        //
        // This loop used to run over `allTypes` and look the cell value up by
        // COLUMN only \u2014 `defenses[defType]` \u2014 so the row variable was used for
        // the label and nothing else and all 18 rows rendered identically. An
        // 18x18 grid displaying 18 distinct numbers, repeated eighteen times.
        //
        // The second defect was in that same expression: `|| 1.0` turned every
        // genuine 0 into neutral, so an immunity could never be shown at all.
        (data.members || []).forEach(function(member) {
            html += '<tr><th scope="row" class="type-label type-'
                 + (member.type1 || 'normal') + '">' + esc(member.name) + '</th>';
            allTypes.forEach(function(attType) {
                html += matchupCell(member.multipliers[attType]);
            });
            html += '</tr>';
        });
        html += '</tbody>';

        // The team's actual exposure: the worst member for each attacking
        // type. This is the row the old grid was drawing eighteen times.
        html += '<tfoot><tr><th scope="row">Worst case</th>';
        allTypes.forEach(function(attType) {
            html += matchupCell((data.worst_case || {})[attType]);
        });
        html += '</tr></tfoot></table></div>';
        grid.innerHTML = html;
    } catch (e) {}
}

// ── Team Radar Chart ──
function renderTeamRadar(team) {
    const card = document.getElementById('team-radar-card');
    if (!card || !team || team.length === 0) return;
    card.style.display = 'block';
    const ctx = document.getElementById('team-radar-chart').getContext('2d');
    const labels = ['HP', 'Attack', 'Defense', 'Sp. Atk', 'Sp. Def', 'Speed'];
    const statKeys = ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed'];
    const datasets = team.map(function(p, i) {
        return {
            label: p.name,
            data: statKeys.map(function(k) { return p.stats[k]; }),
            borderColor: TEAM_COLORS[i % TEAM_COLORS.length],
            backgroundColor: TEAM_COLORS[i % TEAM_COLORS.length].replace(')', ',0.1)').replace('rgb', 'rgba'),
            borderWidth: 2, pointRadius: 3
        };
    });
    const avg = statKeys.map(function(k) {
        return team.reduce(function(s, p) { return s + p.stats[k]; }, 0) / team.length;
    });
    datasets.push({
        label: 'Team Avg', data: avg, borderColor: '#F9A825',
        backgroundColor: 'rgba(249, 168, 37, 0.05)', borderWidth: 3, borderDash: [5, 5], pointRadius: 0
    });
    safeCreateChart('team-radar', ctx, {
        type: 'radar', data: { labels: labels, datasets: datasets },
        options: {
            responsive: true, maintainAspectRatio: false,
            scales: { r: { beginAtZero: true, max: 255, ticks: { stepSize: 50 } } },
            plugins: { legend: { position: 'bottom', labels: { usePointStyle: true, padding: 15, boxWidth: 10, font: { size: 10 } } } }
        }
    });
}

// ── Load optimal build ──
async function loadOptimalBuild() {
    await waitForAnalytics();
    const container = document.getElementById('optimal-build-container');
    container.innerHTML = '<div class="loading"><i class="fas fa-spinner fa-spin"></i> Calculating...</div>';
    try {
        const response = await apiFetch('/api/pokemon-analytics/optimize');
        if (!response.ok) throw new Error('Failed to load optimal build');
        const data = await response.json();
        container.innerHTML =
            '<h4>Optimal Pokemon Build</h4><div class="info-grid">' +
            '<div class="info-item"><div class="info-value">' + data.hp + '</div><div class="info-label">HP</div></div>' +
            '<div class="info-item"><div class="info-value">' + data.attack + '</div><div class="info-label">Attack</div></div>' +
            '<div class="info-item"><div class="info-value">' + data.defense + '</div><div class="info-label">Defense</div></div>' +
            '<div class="info-item"><div class="info-value">' + data.sp_attack + '</div><div class="info-label">Sp. Attack</div></div>' +
            '<div class="info-item"><div class="info-value">' + data.sp_defense + '</div><div class="info-label">Sp. Defense</div></div>' +
            '<div class="info-item"><div class="info-value">' + data.speed + '</div><div class="info-label">Speed</div></div></div>' +
            '<p><strong>Predicted Base Total:</strong> ' + data.predicted_base_total + '</p>';
    } catch (error) {
        container.innerHTML = '<div class="error">Failed to load optimal build</div>';
    }
}

// ── Load model performance ──
async function loadModelPerformance() {
    await waitForAnalytics();
    const container = document.getElementById('model-performance-container');
    container.innerHTML = '<div class="loading"><i class="fas fa-spinner fa-spin"></i> Loading...</div>';
    try {
        const response = await apiFetch('/api/pokemon-analytics/model-performance');
        if (!response.ok) throw new Error('Failed to load model performance');
        const data = await response.json();
        function formatParams(params) {
            if (!params) return '\u2014';
            return Object.entries(params).map(function(e) { return e[0] + ': ' + e[1]; }).join(', ');
        }
        container.innerHTML =
            '<h4>Model Performance</h4><div class="info-grid">' +
            '<div class="info-item"><div class="info-value">' + Math.round(data.legendary_prediction.accuracy * 100) + '%</div><div class="info-label">Legendary Accuracy</div></div>' +
            '<div class="info-item"><div class="info-value">' + Math.round((data.legendary_prediction.f1_cv_score || data.legendary_prediction.accuracy) * 100) + '%</div><div class="info-label">F1 Score (CV)</div></div>' +
            '<div class="info-item"><div class="info-value">' + Math.round(data.stats_prediction.r2_score * 100) + '%</div><div class="info-label">Stats Prediction R\u00b2</div></div>' +
            '<div class="info-item"><div class="info-value">' + Math.round(data.stats_prediction.mse) + '</div><div class="info-label">Mean Squared Error</div></div></div>' +
            '<div class="mt-2 small" style="color: var(--text-secondary);">' +
            '<div><strong>Legendary params:</strong> ' + formatParams(data.legendary_prediction.best_params) + '</div>' +
            '<div><strong>Regressor params:</strong> ' + formatParams(data.stats_prediction.best_params) + '</div>' +
            '<div class="mt-1"><em>' + (data.cached ? '\u26a1 From cache' : '\U0001f504 Freshly trained') + '</em></div></div>';
    } catch (error) {
        container.innerHTML = '<div class="error">Failed to load model performance</div>';
    }
}

// ── Load clustering analysis ──
async function loadClustering(method) {
    await waitForAnalytics();
    if (!method) method = 'pca';
    document.querySelectorAll('[id^="viz-"]').forEach(function(b) {
        b.style.background = 'rgba(255,255,255,0.1)';
        b.style.color = 'var(--text-primary)';
        b.style.border = '1px solid var(--glass-border)';
    });
    var activeBtn = document.getElementById('viz-' + method + '-btn');
    if (activeBtn) {
        activeBtn.style.background = 'var(--accent-color)';
        activeBtn.style.color = 'white';
        activeBtn.style.border = 'none';
    }
    document.getElementById('cluster-chart-container').style.display = 'none';
    document.getElementById('cluster-analysis-cards').style.display = 'none';
    var container = document.getElementById('clustering-container');
    container.innerHTML = '<div class="loading"><i class="fas fa-spinner fa-spin"></i> Running ' + method.toUpperCase() + ' clustering...</div>';
    try {
        var response = await apiFetch('/api/pokemon-analytics/clustering?clusters=5&viz=' + method);
        if (!response.ok) throw new Error('Failed to load clustering');
        var data = await response.json();
        if (data.visualization && data.visualization.length > 0) {
            destroyChart('cluster-scatter');
            var ctx = document.getElementById('cluster-scatter-chart').getContext('2d');
            var clusterColors = ['#e6194b','#3cb44b','#ffe119','#4363d8','#f58231','#911eb4','#42d4f4','#f032e6','#bfef45','#fabed4'];
            var datasets = [];
            for (var i = 0; i < data.total_clusters; i++) {
                var points = data.visualization.filter(function(p) { return p.cluster === i; });
                if (points.length === 0) continue;
                var clusterKey = Object.keys(data.cluster_analysis)[i] || 'cluster_' + i;
                var clusterInfo = data.cluster_analysis[clusterKey] || {};
                var label = 'Cluster ' + (i + 1) + ' (' + (clusterInfo.size || points.length) + ' mons)';
                datasets.push({
                    label: label,
                    data: points.map(function(p) { return {x: p.x, y: p.y}; }),
                    backgroundColor: clusterColors[i % clusterColors.length],
                    borderColor: clusterColors[i % clusterColors.length],
                    pointRadius: 4, pointHoverRadius: 7, pointHitRadius: 30
                });
            }
            safeCreateChart('cluster-scatter', ctx, {
                type: 'scatter',
                data: { datasets: datasets },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom', labels: { usePointStyle: true, padding: 12 } },
                        tooltip: {
                            callbacks: {
                                label: function(ctx) {
                                    var pt = datasets[ctx.datasetIndex].data[ctx.dataIndex];
                                    var original = data.visualization.find(function(p) { return p.x === pt.x && p.y === pt.y; });
                                    if (original) return original.name + ' (' + original.type1 + (original.type2 ? '/' + original.type2 : '') + ') BT:' + original.base_total;
                                    return '';
                                }
                            }
                        }
                    },
                    scales: {
                        x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { display: false }, title: { display: true, text: method === 'pca' ? 'PC1' : 't-SNE 1', color: 'var(--text-secondary)' } },
                        y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { display: false }, title: { display: true, text: method === 'pca' ? 'PC2' : 't-SNE 2', color: 'var(--text-secondary)' } }
                    }
                }
            });
            document.getElementById('cluster-chart-container').style.display = 'block';
            container.innerHTML = '';
            if (data.variance_explained) {
                container.innerHTML = '<p class="text-muted small">Variance explained: PC1=' + (data.variance_explained[0]*100).toFixed(1) + '%, PC2=' + (data.variance_explained[1]*100).toFixed(1) + '%</p>';
            }
        }
        var cardsHtml = '';
        Object.entries(data.cluster_analysis).forEach(function(entry) {
            var clusterName = entry[0];
            var cluster = entry[1];
            cardsHtml +=
                '<div class="card">' +
                '<h5>' + clusterName.replace('_', ' ').toUpperCase() + '</h5>' +
                '<p><strong>Size:</strong> ' + cluster.size + ' Pokemon</p>' +
                '<p><strong>Avg Base Total:</strong> ' + Math.round(cluster.avg_base_total) + '</p>' +
                '<p><strong>Legendary Rate:</strong> ' + Math.round(cluster.legendary_rate * 100) + '%</p>' +
                '<p><strong>Top Types:</strong> ' + Object.keys(cluster.dominant_types).slice(0, 3).join(', ') + '</p>' +
                '</div>';
        });
        document.getElementById('cluster-analysis-cards').innerHTML = cardsHtml;
        document.getElementById('cluster-analysis-cards').style.display = 'grid';
    } catch (error) {
        document.getElementById('clustering-container').innerHTML = '<div class="error">Failed to load clustering analysis</div>';
    }
}

// ── Theme selection functions ──
function setTheme(theme) {
    document.getElementById('playstyle').value = '';
    document.getElementById('difficulty').value = '';
    document.getElementById('generation').value = '';
    document.getElementById('include-legendary').value = '';
    const typesSelect = document.getElementById('preferred-types');
    for (let i = 0; i < typesSelect.options.length; i++) {
        typesSelect.options[i].selected = false;
    }
    switch(theme) {
        case 'competitive':
            document.getElementById('playstyle').value = 'balanced';
            document.getElementById('difficulty').value = 'advanced';
            document.getElementById('include-legendary').value = 'false';
            break;
        case 'beginner':
            document.getElementById('playstyle').value = 'balanced';
            document.getElementById('difficulty').value = 'beginner';
            document.getElementById('generation').value = '1,3';
            document.getElementById('include-legendary').value = 'false';
            break;
        case 'legendary':
            document.getElementById('playstyle').value = 'offensive';
            document.getElementById('difficulty').value = 'advanced';
            document.getElementById('include-legendary').value = 'true';
            break;
        case 'classic':
            document.getElementById('playstyle').value = 'balanced';
            document.getElementById('difficulty').value = 'intermediate';
            document.getElementById('generation').value = '1,1';
            document.getElementById('include-legendary').value = 'false';
            break;
    }
    document.querySelectorAll('.btn-theme').forEach(function(btn) { btn.classList.remove('active'); });
    event.target.classList.add('active');
}

// ── Prediction Playground ──
let playgroundTimeout = null;
function updatePlayground() {
    if (playgroundTimeout) clearTimeout(playgroundTimeout);
    playgroundTimeout = setTimeout(doPlaygroundUpdate, 250);
}

async function doPlaygroundUpdate() {
    await waitForAnalytics();
    const stats = {};
    document.querySelectorAll('.stat-slider').forEach(function(slider) {
        const stat = slider.dataset.stat;
        const value = parseInt(slider.value);
        stats[stat] = value;
        document.getElementById('value-' + stat).textContent = value;
    });
    const type1 = document.getElementById('playground-type1').value;
    const type2 = document.getElementById('playground-type2').value;
    const resultsDiv = document.getElementById('playground-results');
    resultsDiv.style.display = 'block';
    document.getElementById('predicted-total').textContent = '...';
    document.getElementById('legendary-chance').textContent = '...';
    document.getElementById('is-legendary-pred').textContent = '...';
    try {
        const payload = {
            hp: stats.hp, attack: stats.attack, defense: stats.defense,
            sp_attack: stats.sp_attack, sp_defense: stats.sp_defense,
            speed: stats.speed, type1: type1, type2: type2 || 'None',
            generation: 5, height_m: 1.5, weight_kg: 50,
            capture_rate: 45, num_abilities: 2, base_total: 510
        };
        const predRes = await apiFetch('/api/pokemon-analytics/predict', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (predRes.ok) {
            const predData = await predRes.json();
            document.getElementById('predicted-total').textContent = Math.round(predData.stats_prediction.predicted_base_total);
            const legProb = predData.legendary_prediction.legendary_probability;
            document.getElementById('legendary-chance').textContent = (legProb * 100).toFixed(1) + '%';
            document.getElementById('is-legendary-pred').textContent = predData.legendary_prediction.is_legendary ? 'Legendary' : 'Normal';
        }
        const reverseRes = await apiFetch('/api/pokemon-analytics/reverse-search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hp: stats.hp, attack: stats.attack, defense: stats.defense, sp_attack: stats.sp_attack, sp_defense: stats.sp_defense, speed: stats.speed, type1: type1, type2: type2 || null, n_results: 5 })
        });
        if (reverseRes.ok) {
            const reverseData = await reverseRes.json();
            renderClosestPokemon(reverseData.results);
        }
    } catch (e) {
        document.getElementById('predicted-total').textContent = 'Error';
        document.getElementById('legendary-chance').textContent = 'Error';
    }
}

function renderClosestPokemon(results) {
    const container = document.getElementById('closest-pokemon-container');
    if (!results || results.length === 0) {
        container.innerHTML = '<p class="text-white-50">No matches found.</p>';
        return;
    }
    let html = '<div class="closest-pokemon-list">';
    results.forEach(function(p) {
        const spriteUrl = 'https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/' + p.pokedex_number + '.png';
        html +=
            '<div class="closest-pokemon-item">' +
                '<img src="' + spriteUrl + '" alt="" class="cp-sprite" loading="lazy" onerror="this.style.display=\'none\'">' +
                '<div>' +
                    '<div class="cp-name">' + p.name + '</div>' +
                    '<div>' +
                        '<span class="cp-type-badge type-' + String(p.type1).toLowerCase() + '">' + p.type1 + '</span>' +
                        (p.type2 ? '<span class="cp-type-badge type-' + String(p.type2).toLowerCase() + '">' + p.type2 + '</span>' : '') +
                    '</div>' +
                    '<div class="cp-sim">Match: ' + (p.similarity * 100).toFixed(1) + '%</div>' +
                '</div>' +
            '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
}

// ── Initialize ──
document.addEventListener('DOMContentLoaded', function() {
    // Theme setup lives in layout.html and dashboard.js now.
    const hash = window.location.hash.replace('#', '') || 'team-builder';
    showTab(hash);
    var params = new URLSearchParams(window.location.search);
    var teamParam = params.get('team');
    if (teamParam) {
        try {
            var decoded = decodeURIComponent(escape(atob(teamParam)));
            var prefs = JSON.parse(decoded);
            localStorage.setItem('saved-team-prefs', JSON.stringify(prefs));
            showToast('Loading shared team preferences!', 'info');
            if (window.location.hash === '#team-builder') loadSavedTeam(null, prefs);
        } catch (e) {}
    }
    if (window.location.hash === '#team-builder') {
        var savedData = localStorage.getItem('saved-team-data');
        var savedPrefs = localStorage.getItem('saved-team-prefs');
        if (savedData && savedPrefs) {
            try {
                var teamData = JSON.parse(savedData);
                var prefs = JSON.parse(savedPrefs);
                loadSavedTeam(teamData, prefs);
                showToast('Restored your saved team!', 'info');
            } catch (e) {}
        }
    }
});
