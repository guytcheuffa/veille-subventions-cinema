// ============================================================================
// Programme des aides au cinéma — logique front (aucune dépendance externe)
// ============================================================================

const DATA_URL = 'data/site_data.json';
const SELECTION_STORAGE_KEY = 'veille-cinema-selection';

const ETAPE_LABELS = {
  ecriture: 'Écriture',
  developpement: 'Développement',
  production: 'Production',
  'post-production': 'Post-production',
  autre: 'Autre',
};

const MOIS_FR = [
  'Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
  'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre',
];

const state = {
  aides: [],
  filters: { region: new Set(), etape: new Set(), search: '', hideClosed: true },
  selection: loadSelection(),
};

// ----------------------------------------------------------------------------
// Chargement des données
// ----------------------------------------------------------------------------

async function init() {
  try {
    const res = await fetch(DATA_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.aides = Array.isArray(data.aides) ? data.aides : [];
    renderStats(data);
    buildFilters(state.aides);
    render();
  } catch (err) {
    renderLoadError(err);
  }
  bindGlobalEvents();
  updateSelectionUI();
}

function renderLoadError(err) {
  const el = document.getElementById('programme');
  el.innerHTML = `
    <div class="programme-empty">
      <h3>Impossible de charger le programme</h3>
      <p>Le fichier <code>${DATA_URL}</code> n'a pas pu être lu.</p>
      <p>Si vous testez ce site en local, ouvrez-le via un petit serveur
      (ex. <code>python -m http.server</code>) plutôt que directement depuis
      le disque — les navigateurs bloquent la lecture de fichiers locaux par
      sécurité.</p>
    </div>`;
  console.error(err);
}

function renderStats(data) {
  const regions = new Set(state.aides.map((a) => a.region));
  // Sessions non terminées (à venir ou en cours), pas toutes les fiches : les
  // sessions terminées gonflaient ce chiffre.
  document.getElementById('stat-total').textContent = state.aides.filter((a) => statutAide(a).code !== 'terminee').length;
  document.getElementById('stat-regions').textContent = regions.size;
  document.getElementById('stat-updated').textContent = formatDateShort(data.generated_at) || '—';
}

// ----------------------------------------------------------------------------
// Dates
// ----------------------------------------------------------------------------

function todayISO() {
  // window.__TODAY_OVERRIDE__ n'existe que dans les tests (date fixe) ;
  // en production c'est toujours la vraie date du jour.
  if (typeof window !== 'undefined' && window.__TODAY_OVERRIDE__) return window.__TODAY_OVERRIDE__;
  return new Date().toISOString().slice(0, 10);
}

function parseISO(d) {
  if (!d) return null;
  const [y, m, day] = d.split('-').map(Number);
  return new Date(y, m - 1, day);
}

function formatDateShort(iso) {
  const d = parseISO(iso);
  if (!d) return '';
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`;
}

/** Étape la plus proche à venir (>= aujourd'hui) parmi les étapes d'une aide,
 *  ou la dernière étape connue si tout est déjà passé (pour affichage/tri
 *  cohérent des aides "closes" ou sans échéance future). */
function etapeDeReference(aide) {
  const today = todayISO();
  const etapes = (aide.etapes || []).filter((e) => e.date_fin);
  if (etapes.length === 0) return null;
  const aVenir = etapes.filter((e) => e.date_fin >= today).sort((a, b) => a.date_fin.localeCompare(b.date_fin));
  if (aVenir.length > 0) return aVenir[0];
  return etapes.slice().sort((a, b) => b.date_fin.localeCompare(a.date_fin))[0];
}

function estUrgent(dateFin) {
  const today = todayISO();
  const d = parseISO(dateFin);
  const now = parseISO(today);
  if (!d) return false;
  const diffJours = Math.round((d - now) / 86400000);
  return diffJours >= 0 && diffJours <= 14;
}

const DEPOT_RE = /d[ée]p[oô]t|date limite|candidature|inscription/i;

/** Statut d'une aide déduit UNIQUEMENT de ses dates (des faits), jamais du
 *  drapeau `clos` du pipeline — qui signifie seulement "non retrouvée à la
 *  dernière extraction" et prêtait à confusion avec "fermée".
 *    terminee   : toutes les dates sont passées
 *    a_venir    : le dépôt ouvrira à une date future (fenêtre connue)
 *    ouverte    : on est dans la fenêtre de dépôt (fenêtre connue)
 *    depot_clos : le dépôt est passé mais d'autres étapes (comité...) restent
 *    en_cours   : sinon (ex: une seule échéance connue, pas de badge) */
function statutAide(aide) {
  const today = todayISO();
  const etapes = (aide.etapes || []).filter((e) => e.date_fin);
  if (etapes.length === 0) return { code: 'inconnu' };
  if (etapes.every((e) => e.date_fin < today)) return { code: 'terminee', label: 'Terminée' };

  const depots = etapes.filter((e) => DEPOT_RE.test(e.label || ''));
  if (depots.length > 0) {
    const aVenir = depots.filter((e) => e.date_fin >= today).sort((a, b) => a.date_fin.localeCompare(b.date_fin));
    if (aVenir.length === 0) return { code: 'depot_clos', label: 'Dépôt clos' };
    const d = aVenir[0];
    if (d.date_debut && d.date_debut > today) return { code: 'a_venir', label: `Ouvre le ${formatDateShort(d.date_debut)}` };
    if (d.date_debut && d.date_debut <= today) return { code: 'ouverte', label: 'Dépôt ouvert' };
  }
  return { code: 'en_cours' };
}

// ----------------------------------------------------------------------------
// Filtres
// ----------------------------------------------------------------------------

function buildFilters(aides) {
  const regionCounts = new Map();
  const etapeCounts = new Map();
  aides.forEach((a) => {
    regionCounts.set(a.region, (regionCounts.get(a.region) || 0) + 1);
    etapeCounts.set(a.etape_filiere, (etapeCounts.get(a.etape_filiere) || 0) + 1);
  });

  const regionEl = document.getElementById('filter-region');
  [...regionCounts.entries()].sort((a, b) => a[0].localeCompare(b[0])).forEach(([region, count]) => {
    regionEl.appendChild(buildCheckbox('region', region, region, count));
  });

  const etapeEl = document.getElementById('filter-etape');
  [...etapeCounts.entries()].sort((a, b) => a[0].localeCompare(b[0])).forEach(([etape, count]) => {
    const label = ETAPE_LABELS[etape] || etape;
    etapeEl.appendChild(buildCheckbox('etape', etape, label, count));
  });
}

function buildCheckbox(group, value, label, count) {
  const wrap = document.createElement('label');
  wrap.className = 'filters-checkbox';
  wrap.innerHTML = `
    <input type="checkbox" data-group="${group}" value="${escapeAttr(value)}">
    <span>${escapeHTML(label)}</span>
    <span class="count">${count}</span>`;
  wrap.querySelector('input').addEventListener('change', (e) => {
    const set = state.filters[group];
    if (e.target.checked) set.add(value); else set.delete(value);
    render();
  });
  return wrap;
}

function matchesFilters(aide) {
  const f = state.filters;
  if (f.region.size > 0 && !f.region.has(aide.region)) return false;
  if (f.etape.size > 0 && !f.etape.has(aide.etape_filiere)) return false;
  if (f.hideClosed && statutAide(aide).code === 'terminee') return false;
  if (f.search) {
    const haystack = `${aide.fonds} ${aide.organisme} ${aide.region} ${aide.typologie_oeuvre || ''}`.toLowerCase();
    if (!haystack.includes(f.search)) return false;
  }
  return true;
}

// ----------------------------------------------------------------------------
// Rendu du programme
// ----------------------------------------------------------------------------

function render() {
  const container = document.getElementById('programme');
  container.innerHTML = '';

  const visibles = state.aides
    .map((aide) => ({ aide, ref: etapeDeReference(aide) }))
    .filter(({ aide }) => matchesFilters(aide))
    .filter(({ ref }) => ref !== null)
    .sort((a, b) => a.ref.date_fin.localeCompare(b.ref.date_fin));

  if (visibles.length === 0) {
    container.innerHTML = `
      <div class="programme-empty">
        <h3>Aucune échéance ne correspond à ces filtres</h3>
        <p>Essayez d'élargir votre recherche ou de réinitialiser les filtres.</p>
      </div>`;
    return;
  }

  let moisCourant = null;
  let listeMois = null;

  visibles.forEach(({ aide, ref }) => {
    const d = parseISO(ref.date_fin);
    const cleMois = `${d.getFullYear()}-${d.getMonth()}`;
    if (cleMois !== moisCourant) {
      moisCourant = cleMois;
      const section = document.createElement('section');
      section.className = 'programme-month';
      const heading = document.createElement('h2');
      heading.className = 'programme-month-heading';
      heading.textContent = `${MOIS_FR[d.getMonth()]} ${d.getFullYear()}`;
      section.appendChild(heading);
      listeMois = section;
      container.appendChild(section);
    }
    listeMois.appendChild(buildEntry(aide, ref, d));
  });

  document.getElementById('filters-toggle-count').textContent = `(${visibles.length})`;
}

function buildEntry(aide, ref, dateRef) {
  const tpl = document.getElementById('tpl-entry');
  const node = tpl.content.cloneNode(true);
  const article = node.querySelector('.entry');

  const statut = statutAide(aide);
  if (statut.code === 'terminee') article.classList.add('is-closed');
  if (estUrgent(ref.date_fin)) article.classList.add('is-urgent');

  node.querySelector('.entry-date-day').textContent = String(dateRef.getDate()).padStart(2, '0');
  node.querySelector('.entry-date-month').textContent = MOIS_FR[dateRef.getMonth()].slice(0, 3);

  node.querySelector('.entry-fonds').textContent = aide.fonds;
  const meta = node.querySelector('.entry-meta');
  const badge = statut.label ? ` <span class="entry-badge badge-${statut.code}">${escapeHTML(statut.label)}</span>` : '';
  // `clos` (pipeline) = non retrouvée à la dernière extraction : ce n'est PAS
  // "fermée". On le signale sobrement comme "à vérifier" (sauf si terminée).
  const aVerifier = aide.clos && statut.code !== 'terminee'
    ? ' <span class="entry-flag" title="Non retrouvée sur la page officielle lors de la dernière vérification : consultez la source avant de vous fier à ces dates.">à vérifier</span>'
    : '';
  meta.innerHTML = `<span class="region-tag">${escapeHTML(aide.region)}</span> · ${escapeHTML(aide.organisme)}${aide.session_label ? ' · ' + escapeHTML(aide.session_label) : ''}${badge}${aVerifier}`;

  const step = node.querySelector('.entry-next-step');
  const quand = ref.date_debut ? `${formatDateShort(ref.date_debut)} → ${formatDateShort(ref.date_fin)}` : formatDateShort(ref.date_fin);
  step.innerHTML = `<span class="step-label">${escapeHTML(ref.label)} —</span> ${quand}${ref.heure_limite ? ' à ' + ref.heure_limite : ''}`;

  const addBtn = node.querySelector('.entry-add');
  if (state.selection.has(aide.id)) addBtn.classList.add('is-added');
  addBtn.addEventListener('click', () => toggleSelection(aide, addBtn));

  const expandBtn = node.querySelector('.entry-expand');
  const details = node.querySelector('.entry-details');
  expandBtn.addEventListener('click', () => {
    const isHidden = details.hasAttribute('hidden');
    if (isHidden) {
      fillDetails(details, aide);
      details.removeAttribute('hidden');
      expandBtn.textContent = 'Masquer';
    } else {
      details.setAttribute('hidden', '');
      expandBtn.textContent = 'Détails';
    }
  });

  return node;
}

function fillDetails(container, aide) {
  if (container.dataset.filled) return;
  container.dataset.filled = '1';

  const etapesEl = container.querySelector('.entry-etapes');
  if (aide.etapes && aide.etapes.length) {
    etapesEl.innerHTML = '<h4>Calendrier</h4>' + aide.etapes.map((e) => `
      <div class="etape-row">
        <span>${escapeHTML(e.label)}</span>
        <span class="etape-date">${e.date_debut ? formatDateShort(e.date_debut) + ' → ' : ''}${formatDateShort(e.date_fin)}${e.heure_limite ? ' (' + e.heure_limite + ')' : ''}</span>
      </div>`).join('');
  }

  const prealablesEl = container.querySelector('.entry-actions-prealables');
  if (aide.actions_prealables && aide.actions_prealables.length) {
    prealablesEl.innerHTML = '<h4>Démarches préalables</h4>' + aide.actions_prealables.map((a) => `
      <div class="action-prealable">
        <strong>${escapeHTML(a.type_action)}</strong>
        ${escapeHTML(a.condition_declenchement || '')}
      </div>`).join('');
  }

  const conditionsEl = container.querySelector('.entry-conditions');
  if (aide.conditions_eliminatoires && aide.conditions_eliminatoires.length) {
    conditionsEl.innerHTML = '<h4>Conditions</h4><ul>' +
      aide.conditions_eliminatoires.map((c) => `<li>${escapeHTML(c)}</li>`).join('') + '</ul>';
  }

  const montantEl = container.querySelector('.entry-montant');
  if (aide.montant_min_eur || aide.montant_max_eur) {
    const min = aide.montant_min_eur ? `${aide.montant_min_eur.toLocaleString('fr-FR')} €` : null;
    const max = aide.montant_max_eur ? `${aide.montant_max_eur.toLocaleString('fr-FR')} €` : null;
    montantEl.innerHTML = `<h4>Montant</h4><p>${min && max ? `${min} — ${max}` : (max || min)}</p>`;
  }

  const linksEl = container.querySelector('.entry-links');
  const links = [];
  if (aide.lien_formulaire) links.push(`<a href="${escapeAttr(aide.lien_formulaire)}" target="_blank" rel="noopener">Formulaire de dépôt</a>`);
  links.push(`<a href="${escapeAttr(aide.url_source)}" target="_blank" rel="noopener">Page officielle</a>`);
  linksEl.innerHTML = links.join(' · ');
}

// ----------------------------------------------------------------------------
// Sélection + export .ics
// ----------------------------------------------------------------------------

function loadSelection() {
  try {
    const raw = localStorage.getItem(SELECTION_STORAGE_KEY);
    return new Map(raw ? JSON.parse(raw) : []);
  } catch {
    return new Map();
  }
}

function saveSelection() {
  try {
    localStorage.setItem(SELECTION_STORAGE_KEY, JSON.stringify([...state.selection]));
  } catch {
    /* stockage indisponible (navigation privée, etc.) — pas bloquant */
  }
}

function toggleSelection(aide, btnEl) {
  if (state.selection.has(aide.id)) {
    state.selection.delete(aide.id);
    btnEl.classList.remove('is-added');
  } else {
    state.selection.set(aide.id, aide);
    btnEl.classList.add('is-added');
  }
  saveSelection();
  updateSelectionUI();
}

function removeFromSelection(id) {
  state.selection.delete(id);
  saveSelection();
  updateSelectionUI();
  document.querySelectorAll('.entry-add.is-added').forEach((btn) => {
    // Resynchronise l'état visuel des boutons '+' actuellement affichés.
  });
  render(); // ré-affiche les boutons '+' avec le bon état
}

function updateSelectionUI() {
  const list = document.getElementById('selection-list');
  const empty = document.getElementById('selection-empty');
  const exportBtn = document.getElementById('selection-export');
  const count = state.selection.size;

  document.getElementById('selection-count').textContent = count;
  document.getElementById('mobile-selection-count').textContent = count;
  document.getElementById('mobile-selection-bar').classList.toggle('is-visible', count > 0);
  exportBtn.disabled = count === 0;
  empty.style.display = count === 0 ? 'block' : 'none';

  list.innerHTML = '';
  [...state.selection.values()].forEach((aide) => {
    const ref = etapeDeReference(aide);
    const li = document.createElement('li');
    li.className = 'selection-item';
    li.innerHTML = `
      <div>
        <div class="selection-item-name">${escapeHTML(aide.fonds)}</div>
        <div class="selection-item-date">${ref ? formatDateShort(ref.date_fin) : ''}</div>
      </div>
      <button class="selection-item-remove" aria-label="Retirer">×</button>`;
    li.querySelector('.selection-item-remove').addEventListener('click', () => removeFromSelection(aide.id));
    list.appendChild(li);
  });
}

function exportICS() {
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Veille aides cinema//FR',
    'CALSCALE:GREGORIAN',
  ];

  [...state.selection.values()].forEach((aide) => {
    (aide.etapes || []).filter((e) => e.date_fin).forEach((e) => {
      const dt = e.date_fin.replace(/-/g, '');
      const uid = `${aide.id}-${e.date_fin}-${slugify(e.label)}@veille-cinema`;
      lines.push(
        'BEGIN:VEVENT',
        `UID:${uid}`,
        `DTSTAMP:${icsTimestamp()}`,
        `DTSTART;VALUE=DATE:${dt}`,
        `DTEND;VALUE=DATE:${dt}`,
        `SUMMARY:${icsEscape(`${aide.fonds} — ${e.label}`)}`,
        `DESCRIPTION:${icsEscape(`${aide.region} · ${aide.organisme}${e.heure_limite ? ' · limite ' + e.heure_limite : ''}\\n${aide.url_source || ''}`)}`,
        aide.url_source ? `URL:${aide.url_source}` : null,
        'END:VEVENT',
      );
    });
  });

  lines.push('END:VCALENDAR');
  const content = lines.filter(Boolean).join('\r\n');
  const blob = new Blob([content], { type: 'text/calendar;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'aides-cinema.ics';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function icsTimestamp() {
  return new Date().toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
}

function icsEscape(text) {
  return String(text).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n');
}

function slugify(text) {
  return String(text).toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').slice(0, 40);
}

// ----------------------------------------------------------------------------
// Utilitaires
// ----------------------------------------------------------------------------

function escapeHTML(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

function escapeAttr(str) {
  return escapeHTML(str).replace(/"/g, '&quot;');
}

// ----------------------------------------------------------------------------
// Événements globaux
// ----------------------------------------------------------------------------

function bindGlobalEvents() {
  document.getElementById('search').addEventListener('input', (e) => {
    state.filters.search = e.target.value.trim().toLowerCase();
    render();
  });

  document.getElementById('hide-closed').addEventListener('change', (e) => {
    state.filters.hideClosed = e.target.checked;
    render();
  });

  document.getElementById('filters-reset').addEventListener('click', () => {
    state.filters.region.clear();
    state.filters.etape.clear();
    state.filters.search = '';
    state.filters.hideClosed = true;
    document.getElementById('search').value = '';
    document.getElementById('hide-closed').checked = true;
    document.querySelectorAll('.filters-checkbox input[type="checkbox"]').forEach((cb) => { cb.checked = false; });
    render();
  });

  const filtersToggle = document.getElementById('filters-toggle');
  const filtersEl = document.getElementById('filters');
  filtersToggle.addEventListener('click', () => {
    const open = filtersEl.classList.toggle('is-open');
    filtersToggle.setAttribute('aria-expanded', String(open));
  });

  document.getElementById('selection-export').addEventListener('click', exportICS);

  const mobileBar = document.getElementById('mobile-selection-bar');
  const selectionEl = document.getElementById('selection');
  mobileBar.addEventListener('click', () => selectionEl.classList.add('is-open'));
  document.getElementById('selection-close').addEventListener('click', () => selectionEl.classList.remove('is-open'));
}

init();
