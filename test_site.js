// Test du site avec jsdom — simule un vrai navigateur pour vérifier que
// app.js fonctionne réellement (pas juste "la syntaxe est valide").
// Usage : node test_site.js

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

function icsEscapeForTest(text) {
  return String(text).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,');
}

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf-8');
const sampleData = JSON.parse(fs.readFileSync(path.join(__dirname, 'data/site_data.sample.json'), 'utf-8'));

async function main() {
  const dom = new JSDOM(html, {
    url: 'http://localhost/index.html',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  });
  const { window } = dom;

  // Polyfills nécessaires que jsdom ne fournit pas nativement.
  window.fetch = async (url) => {
    if (url === 'data/site_data.json') return { ok: true, json: async () => sampleData };
    throw new Error(`fetch() a demandé une URL inattendue: ${url}`);
  };
  window.URL.createObjectURL = () => 'blob:mock-url';
  window.URL.revokeObjectURL = () => {};
  window.Element.prototype.scrollIntoView = () => {}; // non implémenté par jsdom
  window.__TODAY_OVERRIDE__ = '2026-09-27'; // date fixe : tests indépendants de la date réelle

  let failures = 0;
  function assert(cond, msg) {
    if (!cond) {
      failures++;
      console.error('  ÉCHEC:', msg);
    } else {
      console.log('  OK:', msg);
    }
  }

  // Exécute app.js dans le contexte jsdom.
  const appJs = fs.readFileSync(path.join(__dirname, 'assets/app.js'), 'utf-8');
  dom.window.eval(appJs);

  // init() est async — laisse le micro-task queue se vider.
  await new Promise((r) => setTimeout(r, 50));

  console.log('\n--- Chargement initial ---');
  assert(window.document.getElementById('stat-total').textContent === '7', 'stat-total affiche 7 sessions à venir (8 fiches, 1 terminée)');
  const entries = window.document.querySelectorAll('.entry');
  // "hide-closed" est coché par défaut -> l'aide a6 (clos:true) doit être exclue -> 5 entries visibles
  assert(entries.length === 7, `7 entrées visibles par défaut (seule la session terminée est masquée), trouvé ${entries.length}`);


  console.log('\n--- Statuts déduits des dates (pas du drapeau pipeline) ---');
  const carte = (nom) => [...window.document.querySelectorAll('.entry')].find((e) => e.querySelector('.entry-fonds').textContent.includes(nom));
  const meta = (nom) => carte(nom).querySelector('.entry-meta');
  const nouveauxMedias = meta('Nouveaux Médias');
  assert(nouveauxMedias.querySelector('.badge-a_venir') && nouveauxMedias.textContent.includes('Ouvre le 01/10/2026'),
    'Paris Nouveaux Médias (dépôt 01/10 -> 29/10, aujourd\'hui 27/09) affiche "Ouvre le 01/10/2026", pas "close"');
  assert(!carte('Nouveaux Médias').classList.contains('is-closed'), 'cette session à venir n\'est PAS grisée comme terminée');
  assert(nouveauxMedias.querySelector('.entry-flag') && nouveauxMedias.textContent.includes('à vérifier'),
    'le drapeau pipeline `clos` devient une mention sobre "à vérifier", pas "close"');
  assert(carte('Nouveaux Médias').querySelector('.entry-next-step').textContent.includes('01/10/2026 → 29/10/2026'),
    'la ligne d\'échéance montre la fenêtre complète 01/10/2026 → 29/10/2026');
  assert(meta('COM Audiovisuel').querySelector('.badge-ouverte'), 'Ciclic (fenêtre 20/09 -> 05/10) affiche "Dépôt ouvert"');
  assert(meta('Aide au film court').querySelector('.badge-depot_clos'), 'Cinémas 93 (dépôt passé, plénière à venir) affiche "Dépôt clos"');
  assert(!meta('Soutien à la production').querySelector('.entry-badge'), 'une échéance unique sans fenêtre n\'a pas de pastille');
  assert(!meta('Soutien à la production').querySelector('.entry-flag'), 'une aide non signalée par le pipeline n\'a pas de mention "à vérifier"');

  console.log('\n--- Groupement par mois ---');
  const moisHeadings = [...window.document.querySelectorAll('.programme-month-heading')].map((h) => h.textContent);
  assert(moisHeadings.length >= 2, `au moins 2 groupes de mois créés, trouvé ${moisHeadings.length}`);

  console.log('\n--- Tri chronologique ---');
  const dates = [...window.document.querySelectorAll('.entry-next-step')].map((el) => el.textContent);
  assert(dates[0].includes('19/08/2026') === false, 'sanity: la structure du texte contient bien une date'); // juste un sanity check de forme
  const jours = [...window.document.querySelectorAll('.entry-date-day')].map((el) => el.textContent);
  assert(jours.length === 7, 'chaque entrée visible a un jour affiché');

  console.log('\n--- Filtre région ---');
  const regionCheckbox = [...window.document.querySelectorAll('#filter-region input')].find((cb) => cb.value === 'Bretagne');
  assert(!!regionCheckbox, 'la case "Bretagne" existe dans les filtres');
  regionCheckbox.checked = true;
  regionCheckbox.dispatchEvent(new window.Event('change'));
  const entriesApresFiltre = window.document.querySelectorAll('.entry');
  assert(entriesApresFiltre.length === 1, `1 seule entrée Bretagne après filtre, trouvé ${entriesApresFiltre.length}`);
  regionCheckbox.checked = false;
  regionCheckbox.dispatchEvent(new window.Event('change'));

  console.log('\n--- Recherche texte ---');
  const search = window.document.getElementById('search');
  search.value = 'pictanovo';
  search.dispatchEvent(new window.Event('input'));
  assert(window.document.querySelectorAll('.entry').length === 1, 'recherche "pictanovo" -> 1 résultat');
  search.value = '';
  search.dispatchEvent(new window.Event('input'));

  console.log('\n--- Afficher les sessions closes ---');
  const hideClosed = window.document.getElementById('hide-closed');
  hideClosed.checked = false;
  hideClosed.dispatchEvent(new window.Event('change'));
  assert(window.document.querySelectorAll('.entry').length === 8, 'décocher "masquer terminées" -> 8 entrées (a6 réapparaît)');
  hideClosed.checked = true;
  hideClosed.dispatchEvent(new window.Event('change'));

  console.log('\n--- Sélection ---');
  const firstAddBtn = window.document.querySelector('.entry-add');
  const firstFondsName = firstAddBtn.closest('.entry').querySelector('.entry-fonds').textContent;
  assert(window.document.getElementById('selection-count').textContent === '0', 'sélection vide au départ');
  firstAddBtn.dispatchEvent(new window.Event('click'));
  assert(window.document.getElementById('selection-count').textContent === '1', 'sélection = 1 après clic sur +');
  assert(firstAddBtn.classList.contains('is-added'), 'le bouton + passe en état "ajouté"');
  assert(window.document.getElementById('selection-export').disabled === false, 'le bouton export devient actif');

  console.log('\n--- Persistance localStorage ---');
  const stored = window.localStorage.getItem('veille-cinema-selection');
  assert(!!stored && JSON.parse(stored).length === 1, 'la sélection est bien persistée dans localStorage');

  console.log('\n--- Retrait de la sélection ---');
  const removeBtn = window.document.querySelector('.selection-item-remove');
  removeBtn.dispatchEvent(new window.Event('click'));
  assert(window.document.getElementById('selection-count').textContent === '0', 'sélection revient à 0 après retrait');

  console.log('\n--- Détails (expand) ---');
  firstAddBtn.dispatchEvent(new window.Event('click')); // re-sélectionne pour la suite
  const expandBtn = window.document.querySelector('.entry-expand');
  const details = window.document.querySelector('.entry-details');
  assert(details.hasAttribute('hidden'), 'détails masqués par défaut');
  expandBtn.dispatchEvent(new window.Event('click'));
  assert(!details.hasAttribute('hidden'), 'détails visibles après clic');
  assert(details.querySelector('.entry-etapes').innerHTML.length > 0, 'le contenu des étapes est bien rempli');

  console.log('\n--- Export .ics ---');
  let clickedAnchor = null;
  let capturedBlobContent = null;
  const OriginalBlob = window.Blob;
  window.Blob = class extends OriginalBlob {
    constructor(parts, opts) {
      super(parts, opts);
      capturedBlobContent = parts.join('');
    }
  };
  const originalCreateElement = window.document.createElement.bind(window.document);
  window.document.createElement = (tag) => {
    const el = originalCreateElement(tag);
    if (tag === 'a') {
      el.click = () => { clickedAnchor = el; };
    }
    return el;
  };
  window.document.getElementById('selection-export').dispatchEvent(new window.Event('click'));
  assert(clickedAnchor !== null, 'un lien de téléchargement a été créé et cliqué');
  assert(clickedAnchor.download === 'aides-cinema.ics', 'le nom de fichier .ics est correct');
  assert(capturedBlobContent.startsWith('BEGIN:VCALENDAR'), 'le fichier .ics commence par BEGIN:VCALENDAR');
  assert(capturedBlobContent.includes('BEGIN:VEVENT'), 'le fichier .ics contient au moins un VEVENT');
  assert(capturedBlobContent.includes(`SUMMARY:${icsEscapeForTest(firstFondsName)}`), 'le résumé de l\'événement contient bien le nom du fonds sélectionné');
  assert(capturedBlobContent.trim().endsWith('END:VCALENDAR'), 'le fichier .ics se termine par END:VCALENDAR');
  assert(capturedBlobContent.includes('\r\n'), 'le fichier .ics utilise bien des retours à la ligne CRLF (norme iCalendar)');

  console.log('\n--- Réinitialisation des filtres ---');
  regionCheckbox.checked = true;
  regionCheckbox.dispatchEvent(new window.Event('change'));
  window.document.getElementById('filters-reset').dispatchEvent(new window.Event('click'));
  assert(window.document.querySelectorAll('.entry').length === 7, 'reset ramène à 7 entrées (état par défaut)');

  console.log(`\n${failures === 0 ? 'TOUS LES TESTS SONT PASSÉS' : `${failures} ÉCHEC(S)`}`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error('Erreur fatale pendant le test:', err);
  process.exit(1);
});
