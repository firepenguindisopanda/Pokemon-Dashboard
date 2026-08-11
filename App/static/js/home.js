/* Trainer hub: the Arena, and the collection it feeds.
 *
 * Extracted from a 201-line inline <script> in home.html. T21 de-inlined the
 * heavy templates and explicitly left this one alone; the homepage rewrite made
 * it free to finish.
 *
 * Depends on dashboard.js for apiFetch(), csrfToken() and showToast().
 *
 * THE ONE RULE HERE
 * A nickname is user input. It is interpolated into markup below in both text
 * and attribute position, and the attribute case is the dangerous one — an
 * unescaped quote escapes the attribute and lands in an event handler. Every
 * interpolation goes through esc().
 */

/* global apiFetch, csrfToken, showToast */

var arenaPokemonId = null;

var SPRITE_BASE =
  'https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/';

/** Escape a value for interpolation into HTML, text or attribute position. */
function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function byId(id) {
  return document.getElementById(id);
}

// ── Arena ──────────────────────────────────────────────────────────────────

async function findWildPokemon() {
  const resp = await apiFetch('/arena/encounter');
  const data = await resp.json();
  if (data.error) { showToast(data.error, 'danger'); return; }
  showEncounter(data);
}

async function attackPokemon() {
  if (!arenaPokemonId) return;
  const resp = await apiFetch('/arena/attack/' + arenaPokemonId, { method: 'POST' });
  const data = await resp.json();
  if (data.error) { showToast(data.error, 'danger'); return; }
  updateHpBar(data.current_hp, data.max_hp);
  if (data.fainted) showFainted();
}

async function catchPokemon() {
  if (!arenaPokemonId) return;
  const resp = await apiFetch('/arena/catch/' + arenaPokemonId, { method: 'POST' });
  const data = await resp.json();
  if (data.error) {
    if (data.redirect) { window.location.href = data.redirect; return; }
    showToast(data.error, 'danger');
    return;
  }
  setPokeballs(data.pokeballs_remaining);
  if (data.caught) {
    byId('arena-actions').style.display = 'none';
    var result = byId('arena-catch-result');
    result.style.display = 'block';
    result.innerHTML =
      '<p class="text-success fw-bold fs-5">🎉 You caught ' + esc(data.pokemon_name) + '!</p>' +
      '<button class="btn btn-danger" onclick="findWildPokemon()">' +
      '<i class="fas fa-paw me-1" aria-hidden="true"></i>Find Another</button>';
    addPokemonToCollection(data);
    showToast('Caught ' + data.pokemon_name + '!', 'success');
  } else {
    showToast(data.pokemon_name + ' broke free!', 'warning');
  }
}

async function runFromPokemon() {
  const resp = await apiFetch('/arena/run', { method: 'POST' });
  const data = await resp.json();
  if (data.success) {
    resetArena();
    showToast('You fled!', 'info');
  }
}

/** Both counters, so the header badge and the trainer card cannot disagree. */
function setPokeballs(value) {
  ['pokeball-value', 'pokeball-value-arena'].forEach(function (id) {
    var el = byId(id);
    if (el) el.textContent = value;
  });
}

function showEncounter(data) {
  arenaPokemonId = data.id;

  byId('arena-no-encounter').style.display = 'none';
  byId('arena-encounter').style.display = 'block';
  byId('arena-fainted').style.display = 'none';
  byId('arena-catch-result').style.display = 'none';
  byId('arena-actions').style.display = 'flex';

  var sprite = byId('arena-sprite');
  sprite.src = data.sprite_url;
  // Named, not decorative: this image IS the encounter.
  sprite.alt = 'Wild ' + data.name;

  byId('arena-name').textContent = data.name + ' (#' + data.pokedex_number + ')';

  var types = '<span class="type-badge type-' + esc(data.type1.toLowerCase()) + '">'
            + esc(data.type1) + '</span>';
  if (data.type2) {
    types += '<span class="type-badge type-' + esc(data.type2.toLowerCase()) + ' ms-1">'
           + esc(data.type2) + '</span>';
  }
  byId('arena-types').innerHTML = types;

  setPokeballs(data.pokeballs);
  updateHpBar(data.current_hp, data.max_hp);
}

function updateHpBar(currentHp, maxHp) {
  var pct = maxHp > 0 ? (currentHp / maxHp * 100) : 0;
  var bar = byId('arena-hp-bar');
  bar.style.width = pct + '%';
  bar.setAttribute('aria-valuenow', currentHp);
  bar.setAttribute('aria-valuemax', maxHp);
  byId('arena-hp-text').textContent = currentHp + ' / ' + maxHp;

  bar.className = 'stat-fill';
  if (pct > 50) bar.classList.add('bg-success');
  else if (pct > 25) bar.classList.add('bg-warning');
  else bar.classList.add('bg-danger');
}

function showFainted() {
  byId('arena-actions').style.display = 'none';
  byId('arena-fainted').style.display = 'block';
}

function resetArena() {
  arenaPokemonId = null;
  byId('arena-no-encounter').style.display = 'block';
  byId('arena-encounter').style.display = 'none';
}

// ── Collection ─────────────────────────────────────────────────────────────

/**
 * Add a freshly caught Pokemon to the grid.
 *
 * The previous version rebuilt the entire collection TABLE with innerHTML on
 * the first catch, because the empty state had replaced the table markup. That
 * forced this file to carry a duplicate copy of every row's HTML, which then
 * drifted from the template. The grid now always exists and the empty notice
 * is a sibling, so this appends one card and hides the notice.
 */
function addPokemonToCollection(data) {
  var grid = byId('user-pokemon-grid');
  if (!grid) return;

  var empty = byId('no-pokemon-msg');
  if (empty) empty.hidden = true;

  var id = data.user_pokemon_id;
  var nickname = data.pokemon_name;
  var token = csrfToken() || '';

  var item = document.createElement('li');
  item.className = 'collection-item';
  item.id = 'user-pokemon-' + id;
  item.innerHTML =
    '<a href="/pokemon-area/pokemon-details/' + encodeURIComponent(data.pokemon_id) + '" class="collection-link">' +
      '<img src="' + SPRITE_BASE + encodeURIComponent(data.pokemon_id) + '.png" alt="" ' +
           'width="72" height="72" loading="lazy" class="collection-sprite">' +
      '<span class="collection-nickname">' + esc(nickname) + '</span>' +
      '<span class="collection-species">' + esc(data.species) + '</span>' +
    '</a>' +
    // The rename input is INSIDE the form. Outside it, with no `form`
    // attribute, the browser submits nothing for it and the server writes a
    // null nickname over the real one — which is exactly what shipped.
    '<form action="/rename-pokemon/' + encodeURIComponent(id) + '" method="POST" class="collection-rename">' +
      '<input type="hidden" name="csrf_token" value="' + esc(token) + '">' +
      '<label for="rename-input-' + esc(id) + '" class="visually-hidden">New name for ' + esc(nickname) + '</label>' +
      '<input type="text" id="rename-input-' + esc(id) + '" name="new_name_' + esc(id) + '" ' +
             'class="form-control form-control-sm" placeholder="Rename…" required>' +
      '<button type="submit" class="btn btn-sm btn-outline-secondary" title="Rename" ' +
              'aria-label="Rename ' + esc(nickname) + '">' +
        '<i class="fas fa-pen" aria-hidden="true"></i></button>' +
    '</form>' +
    '<form action="/release-pokemon/' + encodeURIComponent(id) + '" method="POST" class="collection-release">' +
      '<input type="hidden" name="csrf_token" value="' + esc(token) + '">' +
      '<button type="submit" class="btn btn-sm btn-outline-danger" title="Release" ' +
              'aria-label="Release ' + esc(nickname) + '">' +
        '<i class="fas fa-trash me-1" aria-hidden="true"></i>Release</button>' +
    '</form>';

  grid.appendChild(item);

  var count = byId('collection-count');
  if (count) count.textContent = grid.children.length;
  var caught = byId('caught-count');
  if (caught) caught.textContent = grid.children.length;
}
