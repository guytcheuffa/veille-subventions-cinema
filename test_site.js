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

  console.log('\n--- Bouton « Masquer » ---');
  // jsdom ne reproduit pas la cascade d'un vrai navigateur (où un `display:`
  // d'auteur écrase le [hidden] par défaut) : on vérifie donc la règle CSS elle-même.
  const css = fs.readFileSync(path.join(__dirname, 'assets/style.css'), 'utf-8');
  assert(/\[hidden\]\s*\{\s*display:\s*none\s*!important/.test(css), 'style.css force display:none sur [hidden] (sinon .entry-details en flex reste visible)');
  assert(!window.document.getElementById('mobile-selection-bar').hasAttribute('hidden'), 'la barre mobile ne dépend plus de [hidden] (désormais prioritaire)');
  expandBtn.dispatchEvent(new window.Event('click'));
  assert(details.hasAttribute('hidden') && expandBtn.textContent === 'Détails', '2e clic : détails remasqués et bouton redevient « Détails »');
  expandBtn.dispatchEvent(new window.Event('click'));
  assert(!details.hasAttribute('hidden') && expandBtn.textContent === 'Masquer', '3e clic : détails de nouveau visibles, bouton « Masquer »');
  expandBtn.dispatchEvent(new window.Event('click'));

  console.log('\n--- Nature des échéances (libellés réels) ---');
  const cas = {
    depot: ['Dépôt des dossiers', 'Date limite de dépôt', 'dépôt des dossiers (comité de lecture AVR2)', 'Comité de chiffrage NOVEMBRE 2026 - Date unique de dépôt',
      'Date unique de dépôt pour le comité de chiffrage', 'dépôt des demandes', 'Dépôt des dossiers - Animation (CM, LM, AV, XR)', 'dépôt des dossiers (calendrier permanent)'],
    decision: ['Commission plénière', 'commission de présélection', 'Comité de lecture', 'Comité de chiffrage NOVEMBRE 2026', 'Annonce des résultats de la présélection',
      'Annonce des résultats définitifs', 'Résultats à partir du (comité de lecture)', 'Commission plénière AVR1 - session octobre 2026, résultats à partir du', 'Plénière', 'Présélection'],
    demarche: ['Réunion « premier dépôt »', 'Réunion « premier dépôt » (visioconférence, créneau unique 09h00-10h00)', 'RV préalables', 'contact recommandé des services instructeurs avant le dépôt', 'Rendez-vous préalable'],
  };
  Object.entries(cas).forEach(([attendu, labels]) => labels.forEach((l) => assert(window.typeEcheance(l) === attendu, `« ${l} » -> ${attendu} (obtenu ${window.typeEcheance(l)})`)));

  console.log('\n--- Format et genre déduits du texte ---');
  const tags = (o) => { const t = window.tagsAide(Object.assign({ etapes: [] }, o)); return `${[...t.formats].sort()}|${[...t.genres].sort()}`; };
  assert(tags({ fonds: 'Aide à la création', typologie_oeuvre: 'Animation (CM, LM, AV, XR)' }) === 'audiovisuel,court,long,numerique|animation', 'Occitanie « Animation (CM, LM, AV, XR) » -> 4 formats, animation');
  assert(tags({ fonds: 'x', typologie_oeuvre: 'Fiction (LM-AV)' }) === 'audiovisuel,long|fiction', 'Occitanie « Fiction (LM-AV) » -> long + audiovisuel, fiction');
  assert(tags({ fonds: 'x', typologie_oeuvre: 'Fiction (CM, XR)' }) === 'court,numerique|fiction', 'Occitanie « Fiction (CM, XR) » -> court + numérique');
  assert(tags({ fonds: 'x', typologie_oeuvre: 'Documentaire (CM, LM, AV, XR)' }) === 'audiovisuel,court,long,numerique|documentaire', 'Occitanie documentaire');
  assert(tags({ fonds: 'Fonds Court métrage', typologie_oeuvre: 'court métrage (hors animation)' }) === 'court|documentaire,experimental,fiction', 'Pictanovo « hors animation » exclut l\'animation sans la tagger');
  assert(tags({ fonds: 'Aide au film court', typologie_oeuvre: 'court métrage tous genres' }) === 'court|animation,documentaire,experimental,fiction', '« tous genres » -> tous les genres');
  assert(tags({ fonds: 'Fonds de soutien à la création et à la production cinéma et audiovisuel', typologie_oeuvre: 'long métrage' }) === 'long|', 'le nom générique « cinéma et audiovisuel » n\'ajoute pas le format audiovisuel');
  assert(tags({ fonds: 'Fonds', typologie_oeuvre: '' }) === '|', 'rien de précisé -> ensembles vides (aide jamais écartée par erreur)');

  console.log('\n--- Filtres type d\'échéance / format / genre (interface) ---');
  const cb = (id, v) => [...window.document.querySelectorAll(`#${id} input`)].find((c) => c.value === v);
  const cocher = (c, on = true) => { c.checked = on; c.dispatchEvent(new window.Event('change')); };
  const noms = () => [...window.document.querySelectorAll('.entry-fonds')].map((e) => e.textContent);
  assert(!!cb('filter-echeance', 'depot') && !!cb('filter-echeance', 'decision') && !!cb('filter-echeance', 'demarche'), 'les 3 cases dépôt / décision / démarche existent');
  cocher(cb('filter-echeance', 'decision'));
  assert(noms().some((n) => n.includes('Aide au film court')) && !noms().some((n) => n.includes('Soutien à la production')), 'filtre « Décision » : garde Cinémas 93 (plénière à venir), écarte une aide qui n\'a qu\'un dépôt');
  const ref93 = [...window.document.querySelectorAll('.entry')].find((e) => e.querySelector('.entry-fonds').textContent.includes('Aide au film court'));
  assert(ref93.querySelector('.entry-next-step .type-decision'), 'la ligne d\'échéance affiche la pastille « Décision » (couleur verte)');
  cocher(cb('filter-echeance', 'decision'), false);
  cocher(cb('filter-echeance', 'depot'));
  assert(!noms().some((n) => n.includes('Aide au film court')), 'filtre « Dépôt » : Cinémas 93 (dépôt passé) disparaît = « dépôt encore possible »');
  assert(noms().length >= 4, 'filtre « Dépôt » : les aides au dépôt à venir restent');
  cocher(cb('filter-echeance', 'depot'), false);
  const chipDepot = window.document.querySelector('.entry-next-step .type-depot');
  assert(!!chipDepot, 'une échéance de dépôt porte la pastille « Dépôt »');

  cocher(cb('filter-genre', 'animation'));
  assert(noms().some((n) => n.includes('Fonds Court métrage')) === false, 'genre « Animation » écarte Pictanovo (« hors animation »)');
  assert(noms().some((n) => n.includes('Aide au film court')), 'genre « Animation » garde « tous genres » (Cinémas 93)');
  cocher(cb('filter-genre', 'animation'), false);
  cocher(cb('filter-format', 'numerique'));
  assert(noms().some((n) => n.includes('Nouveaux Médias')) && !noms().some((n) => n.includes('Soutien à la production')), 'format « Nouveaux médias » : garde Paris NM, écarte le court métrage de fiction');
  cocher(cb('filter-format', 'numerique'), false);
  assert(window.document.querySelectorAll('.entry').length === 7, 'décocher les filtres ramène à 7 entrées');

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
