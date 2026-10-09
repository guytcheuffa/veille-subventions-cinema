> 🇬🇧 English version: [README.md](README.md)

# Veille des aides régionales au cinéma — POC

Squelette du pipeline d'extraction pour la mission de veille des dates de dépôt
des fonds d'aide régionaux au cinéma. Ce POC couvre **16 sources** (voir
`config.py`) : Région Sud, AURA, Normandie (x2), Paris, Bretagne, Grand Est
(Région + Strasbourg), Pays de la Loire, Occitanie, Centre-Val de Loire
(Ciclic), Île-de-France (Cinémas 93), Bourgogne-Franche-Comté, Hauts-de-France
(Pictanovo), Nouvelle-Aquitaine (ALCA), plus le CNC (aide nationale, hors
périmètre strict) — une quinzaine de patterns structurels différents.

## Ce qui est fait

- `schema.py` — schéma Pydantic de sortie structurée (`Commission`,
  `PeriodeCle`, `ActionPrealable`, `ExtractionResult`), pensé pour couvrir :
  date simple récurrente, sessions multi-étapes, fenêtres de dates (`du X au
  Y`), et absence totale de date en HTML (renvoi vers un PDF).
- `prompts.py` — prompt système qui interdit explicitement au LLM d'halluciner
  une date, distingue date ponctuelle vs fenêtre, et repère les actions
  préalables obligatoires (RDV, etc.) conditionnelles ou systématiques.
- `extract.py` — appel à l'API (Anthropic ou Qwen, cf. section dédiée
  ci-dessous) avec **tool call forcé** sur le schéma Pydantic, plus un filet
  de sécurité (`_reparer_structure`) qui corrige automatiquement plusieurs
  écarts structurels observés en pratique sur Qwen Flash (champs égarés,
  commissions scindées en fragments, valeurs manquantes).
- `fetch.py` — récupération du contenu : fetch direct (`httpx`) ou via
  **Jina Reader** (`r.jina.ai`) pour les sites protégés par un WAF anti-bot,
  + fallback d'extraction de texte PDF natif (`pypdf`).
- `config.py` — registre des **16 sources** testées (13 régionales/locales +
  le CNC, aide nationale hors périmètre strict — la Normandie et le Grand Est
  comptent chacun 2 entrées), avec pour chacune : URL, besoin ou non de Jina
  Reader, fixture offline associée, et notes sur le piège structurel qu'elle
  illustre.
- **`state.py`** — gestion d'état et déduplication : compare chaque
  extraction au dernier état connu (`data/state.json`) et classe chaque
  commission en `nouveau` / `modifie` / `inchange` / `clos`. Seuls les 3
  premiers statuts (jamais `inchange`) doivent déclencher une notification —
  c'est le point explicitement demandé par le cahier des charges ("ne pas
  notifier les commissions inchangées ou closes"). Produit aussi
  `data/site_data.json`, la base JSON propre et plate destinée au futur site
  (filtres région / étape / type d'échéance — dépôt, décision, démarche, en couleurs —
  / format / genre déduits des textes officiels, sélection, export calendrier).
- `test_extraction.py` — rejoue l'extraction sur les 10 textes déjà capturés
  dans `test_data/` (aucune requête vers les sites sources, seulement vers
  l'API) — **c'est le point d'entrée à lancer en premier** pour valider le
  LLM.
- **`test_state.py`** — test de la logique de dédup, **100% hors-ligne,
  aucun appel API, zéro coût** — à lancer à tout moment pour vérifier ou
  faire évoluer `state.py` sans dépendre du LLM.
- `main.py` — pipeline complet (fetch réel + extraction + fallback PDF
  automatique + mise à jour de l'état + écriture de `data/site_data.json`)
  sur les 16 sources du registre.
- **`budget.py`** — garde-fou budgétaire indépendant du provider : vérifie
  AVANT chaque appel réel que le coût estimé ne dépasserait pas un plafond
  mensuel (`BUDGET_LIMIT_USD`, 2$ par défaut) et bloque l'appel si c'est le
  cas — utile en particulier pour Qwen, qui facture en post-payé (contrairement
  à Anthropic où le solde prépayé est déjà un plafond dur).
- **`alerts.py`** — suivi par DATE INDIVIDUELLE (`url_source` + `date_fin`),
  indépendant du suivi par commission de `state.py`. Sert uniquement à
  générer des alertes fiables ("nouvelle" échéance détectée, échéance
  "vient de fermer") sans hériter du bruit causé par le redécoupage parfois
  incohérent des commissions par le LLM d'un run à l'autre (ex: le CNC qui
  regroupe tantôt 12 sessions séparées, tantôt 1 seule combinée — state.py
  seul y voit des dizaines de fausses clôtures, alerts.py n'y voit rien
  puisque les dates individuelles, elles, sont identiques). Écrit
  `data/dates_watch.json` (état persistant) et `data/alerts.json` (alertes
  du run le plus récent — n'est plus lu par le site, voir note ci-dessous). Testé à 100%
  hors-ligne dans `test_alerts.py`.
- **`verify.py`** — filet de sécurité contre l'HALLUCINATION DE DATES : après
  extraction, vérifie que chaque `date_fin` apparaît réellement (sous une
  forme plausible : `jj/mm/aaaa`, `jj mois aaaa`, etc.) dans le texte source
  envoyé au LLM. Contrairement à tous les autres filets déjà en place, ce
  cas-ci ne provoque ni crash ni erreur de validation — le JSON produit est
  parfaitement valide, juste factuellement faux. Découvert en conditions
  réelles sur Normandie (Session 3 - 2027) : le texte disait "Présélection
  le 22/06/27, Plénière le 06/07/27", Qwen avait renvoyé 16/06/2027 et
  30/06/2027 — deux dates plausibles mais entièrement inventées, invisibles
  à l'œil sur le site sans comparer au texte source. Une date non confirmée
  est retirée (jamais affichée comme fiable) ; une action préalable dont
  seule la fenêtre de dates n'est pas confirmée garde son obligation, juste
  sans date précise. Testé à 100% hors-ligne dans `test_verify.py`, y
  compris sur le cas réel exact qui a révélé le problème.
  **2e chance** (`extract.py`) : le filtre retire une date inventée mais
  ne retrouve pas la vraie (Normandie Session 3 perdait sa plénière du
  06/07/27). Quand au moins une date est retirée, l'extraction est
  relancée UNE fois avec un message listant les dates qui n'existent pas
  dans le texte. Le résultat de la 2e passe n'est gardé que s'il est
  strictement meilleur (moins de dates retirées, puis plus d'étapes
  conservées) ; si elle échoue, plante ou est bloquée par le budget, la
  1re passe est conservée. Aucun surcoût quand aucune date n'est retirée.
  Testé dans `test_retry.py` (appels LLM simulés). Une 2e passe n'est
  retenue que si elle est valide ET rend au moins autant d'étapes
  confirmées que la 1re (sinon la source entière pouvait être perdue).
- **Années des dates** (`verify.py`, `normaliser_annees`) : une erreur sur
  la seule année passe le filtre précédent (le jour et le mois figurent bien
  dans le texte). Cas réel PACA : « 30 septembre de chaque année » rendu en
  2027 alors que le 30/09/2026 était dans 2 jours, donc une échéance
  imminente disparaissait du site. Règles, par date : (1) une seule année
  écrite dans le texte pour ce jour/mois -> elle l'emporte ; (2) plusieurs
  années -> ambigu, on ne touche pas ; (3) aucune année MAIS le texte marque
  une récurrence à proximité (« chaque année », « par an », « annuel »,
  « calendrier permanent ») -> prochaine occurrence à partir d'aujourd'hui ;
  (4) sinon on ne touche pas (ALCA : « les dépôts des 7 et 21 septembre sont
  clôturés » n'est pas un calendrier qui se répète). Les fenêtres sont
  décalées en bloc. Sur les 16 pages testées, la règle ne se déclenche que
  sur PACA, Strasbourg, Grand Est et Bourgogne-Franche-Comté. Chaque
  correction est affichée dans le log (`[verify] année corrigée`).
  Testé dans `test_verify.py` et de bout en bout dans `test_robustesse.py`.
- **Réponses vides, doublons, dates dans les libellés** (cas réels du run du
  28/09) : (1) réponse `{"commissions": null}` (Ciclic) -> un nouvel essai, puis
  « aucune date trouvée » plutôt qu'un échec ; une source déclarée sans date
  n'est jamais retentée (pas de surcoût) ; (2) la même page renvoyée 3 fois
  sous des libellés de session inventés (Bourgogne) -> commissions
  strictement identiques (même fonds, même étape, mêmes dates) fusionnées,
  celles aux dates différentes jamais ; (3) libellé « semaine du 12 octobre
  2026 » avec une date_fin inventée (Cinémas 93) -> la date écrite dans le
  libellé est reprise si le texte la confirme, au lieu de perdre l'étape ;
  (4) `build_site_data` n'envoie plus au site une fiche « non retrouvée »
  dont toutes les dates sont déjà affichées par une fiche active de la même
  source (reste d'un redécoupage) — elle reste dans `state.json`.
- **Fiches vides et anciennes versions** (données réelles du 28/09) : une
  commission sans aucune date n'entre jamais dans l'état (`main.py`) ; si
  TOUTES les commissions d'une source n'ont plus de date (Normandie page
  principale, Occitanie), un nouvel essai puis « aucune date trouvée » (lien
  PDF conservé pour le fallback), avec au plus 2 appels. Côté site,
  `build_site_data` n'envoie pas les fiches sans date, ni une fiche « non
  retrouvée » **supplantée** par une fiche active du même dispositif (même
  source, fonds similaire, dates incluses ou en fort recouvrement) : l'ancienne
  version aux dates inventées ne reste plus affichée. Une fiche non retrouvée
  qui porte seule ses dates reste visible (« à vérifier »).
  `python rebuild_site.py` régénère `site_data.json` depuis `state.json` sans
  appeler l'API (coût 0).
- **Pages inutilisables** (`fetch.verifier_contenu`, testé dans `test_fetch.py`) :
  Bretagne et Grand Est (Région) renvoient une vérification anti-robot
  Cloudflare (« Just a moment… », « 403 »), AURA renvoie sa page d'accueil à
  la place de la fiche de l'aide (mots attendus dans `config.Source.attendu`).
  Ces pages étaient comptées comme « traitées avec succès » et envoyées au
  LLM. Elles sont maintenant des ÉCHECS explicites (`PageInutilisable`) : le
  LLM n'est pas appelé (0 coût), la source n'est pas marquée traitée (donc
  aucune session connue n'est marquée fermée à tort) et le récapitulatif de
  fin la liste. Aucune des 16 pages réelles capturées n'est rejetée à tort.
  Limite connue : ces trois sites ne sont pas lisibles automatiquement sans
  navigateur réel ; leurs dates doivent être suivies autrement (voir
  « hors scope »).
- **Robustesse aux sorties abîmées de Qwen** (`extract.py`, testé dans
  `test_robustesse.py`) : champs texte obligatoires renvoyés à `null`
  (remplacés par « non précisé »), JSON avec caractères de contrôle ou
  syntaxe Python (décodage tolérant, sans rien exécuter), arguments
  d'outil illisibles (un nouvel essai, coût enregistré). **Sauvetage champ
  par champ** : quand la validation échoue sur un champ précis, seule la plus
  petite partie fautive est retirée (une étape, une fenêtre, une condition,
  une valeur, ou un texte remplacé par « non précisé ») et le reste de la
  source est conservé, au lieu de rejeter toute la source ; chaque
  correction est loguée (`[sauvetage] chemin.du.champ : …`). Une structure
  réellement inexploitable échoue toujours explicitement. `main.py` liste en
  fin de run les sources en échec, avec la raison et, pour une erreur de
  validation, les champs fautifs.
- **`index.html` / `assets/`** — le site web statique (voir section dédiée
  ci-dessous) : programme chronologique des échéances,
  filtres, sélection, export `.ics`. Aucun serveur, aucune dépendance de
  build — se déploie tel quel sur GitHub Pages.

## Ce qui est volontairement hors scope de ce POC

Ces points sont identifiés mais pas encore codés :

- Workflow GitHub Actions + GitHub Secrets (le pipeline tourne aujourd'hui à
  la main via `python main.py`).
- Extension aux régions métropolitaines manquantes.
- OCR pour les PDF scannés (le fallback PDF ne gère que le texte natif).
- Sources protégées par un anti-robot (Bretagne, Grand Est Région, AURA) : non
  lisibles automatiquement ; une saisie manuelle du texte de la page reste à
  prévoir si on veut les couvrir.

## Le site web

Le site (`index.html` + `assets/style.css` + `assets/app.js`) est un
programme chronologique des échéances, sur le modèle d'un catalogue de
festival : filtrable par région et par étape de la filière, avec une
recherche libre, une sélection persistante (`localStorage`) et un export
`.ics` généré entièrement côté client (aucun serveur, aucune dépendance
externe au runtime).

**Statut d'une session.** Le site déduit le statut des DATES, jamais du
drapeau `clos` du pipeline (qui signifie seulement "non retrouvée à la
dernière extraction" — cela arrive quand le LLM découpe la page autrement,
pas quand une session ferme). Pastilles : *Ouvre le JJ/MM* (fenêtre de
dépôt future), *Dépôt ouvert* (dans la fenêtre), *Dépôt clos* (dépôt
passé mais comité/plénière à venir), *Terminée* (toutes les dates
passées, carte grisée et masquée par défaut). Une session que le pipeline
n'a pas retrouvée mais dont les dates sont à venir reste affichée avec
la mention discrète *à vérifier* plutôt que d'être présentée comme fermée.

Le site lit `data/site_data.json` — le fichier produit par `main.py` — via
un simple `fetch()` relatif, donc **aucune étape de build** n'est
nécessaire : committer `index.html`, `assets/` et `data/site_data.json`
suffit à avoir un site à jour.

### Tester en local

Les navigateurs bloquent `fetch()` sur un fichier ouvert directement depuis
le disque (`file://`) — il faut un petit serveur local :

```bash
python -m http.server 8000
```

puis ouvrir http://localhost:8000/. Si `data/site_data.json` n'existe pas
encore chez vous, vous pouvez temporairement copier
`data/site_data.sample.json` vers `data/site_data.json` (jeu de données
factice fourni pour le développement).

### Tester automatiquement (jsdom)

`test_site.js` fait tourner le site dans un DOM simulé (jsdom) et vérifie le
rendu, les filtres, la sélection, la persistance et **le contenu réel du
fichier `.ics` généré** — pas seulement qu'un téléchargement se déclenche :

```bash
npm install
npm run test:site
```

### Déployer sur GitHub Pages

Dans les paramètres du repo GitHub : **Settings → Pages → Source : Deploy
from a branch**, branche `main`, dossier `/ (root)`. Le site sera servi à
`https://<utilisateur>.github.io/<repo>/`. Comme `main.py` écrit directement
`data/site_data.json` à la racine du repo (une fois committé), aucune étape
de publication supplémentaire n'est nécessaire — relancer le pipeline et
pousser met le site à jour.

## Installation

```bash
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate sous Windows
pip install -r requirements.txt
cp .env.example .env      # puis renseigne ta clé dans .env
```

Le projet ne charge pas automatiquement `.env` (pas de `python-dotenv` pour
rester minimal) — exporte la variable avant de lancer les scripts :

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # macOS/Linux
$env:ANTHROPIC_API_KEY="sk-ant-..."        # PowerShell
```

## Choisir le provider LLM (Anthropic ou Qwen)

Le cahier des charges accepte "type GPT-4o-mini ou Claude Haiku" — `extract.py`
supporte les deux, sélectionnables via la variable `PROVIDER` :

```powershell
# Anthropic (par défaut) — nécessite ANTHROPIC_API_KEY
$env:PROVIDER="anthropic"
$env:ANTHROPIC_API_KEY="sk-ant-..."

# Qwen (QwenCloud/DashScope) — nécessite DASHSCOPE_API_KEY
$env:PROVIDER="qwen"
$env:DASHSCOPE_API_KEY="sk-ws-..."
```

Les deux passent par un tool call forcé sur le même schéma Pydantic
(`schema.py`), donc le résultat est structurellement identique quel que soit
le provider choisi — pratique pour comparer la qualité d'extraction des deux
sur les mêmes 4 fixtures.

## Lancer en local

**0. Dry run (0 centime dépensé)** — vérifie toute la plomberie (lecture des
fixtures, construction du prompt, validation Pydantic) sans appeler l'API.
Affiche aussi une estimation grossière du coût par appel :

```bash
export DRY_RUN=true
python test_extraction.py
unset DRY_RUN   # ou $env:DRY_RUN="false" sous PowerShell, avant l'étape suivante
```

**1. Tester l'extraction hors-ligne (recommandé en premier)** — rejoue le LLM
sur les 4 textes déjà capturés, sans dépendre de la disponibilité des sites :

```bash
python test_extraction.py
```

Compare la sortie JSON à ce qui est décrit dans les notes de `config.py` pour
chaque source — en particulier :
- Région Sud : le RDV préalable doit ressortir comme **conditionnel**.
- AURA : les sessions "date communiquée ultérieurement" ne doivent **pas**
  avoir de date inventée.
- Normandie (page principale) : `aucune_date_trouvee` doit être `true`, avec
  `lien_pdf_calendrier` rempli.
- Normandie (sous-domaine) : 3 sessions × 4 étapes, avec de vraies **fenêtres**
  (`date_debut` + `date_fin`) sur le RDV et le dépôt.

**2. Tester la dédup/état (recommandé, aucun coût)** — logique pure, aucun
appel API, vérifie les 4 statuts (nouveau/modifié/inchangé/clos) sur des
données synthétiques :

```bash
python test_state.py
```

**3. Tester le pipeline complet (fetch réel + extraction + dédup)** :

```bash
python main.py
```

Écrit/actualise `data/state.json` (l'état persistant, à committer dans le
repo — c'est la mémoire d'un run à l'autre) et `data/site_data.json` (la
base JSON propre, plate, destinée au futur site).

## Structure

```
veille-cinema/
├── index.html                        # site web (racine, pour GitHub Pages)
├── assets/
│   ├── style.css                       # styles du site
│   └── app.js                           # logique du site (filtres, sélection, export .ics)
├── test_site.js                        # test du site avec jsdom (npm run test:site)
├── package.json                        # dépendance de test (jsdom) uniquement
├── config.py              # registre des 16 sources testées
├── schema.py               # modèles Pydantic
├── prompts.py               # prompt système + template utilisateur
├── extract.py                # appel API Anthropic/Qwen (tool call forcé)
├── fetch.py                   # fetch direct / Jina Reader / PDF
├── budget.py                   # garde-fou budgétaire (BUDGET_LIMIT_USD)
├── state.py                     # dédup/état par commission + génération de site_data.json
├── alerts.py                     # suivi par date individuelle + alerts.json (log terminal, non affiché sur le site)
├── verify.py                      # anti-hallucination : vérifie chaque date contre le texte source
├── main.py                       # pipeline complet (réseau)
├── rebuild_site.py               # régénère site_data.json depuis state.json (0 coût)
├── test_extraction.py            # test LLM offline (fixtures, coûte des tokens)
├── test_state.py                  # test dédup offline (0 coût, 0 API)
├── test_alerts.py                  # test suivi par date offline (0 coût, 0 API)
├── test_verify.py                   # test anti-hallucination offline (0 coût, 0 API)
├── test_retry.py                    # test de la 2e chance sur dates retirées (0 coût, 0 API)
├── test_robustesse.py               # test des sorties abîmées de Qwen (0 coût, 0 API)
├── test_fetch.py                    # test de la détection de pages bloquées (0 coût, 0 API)
├── test_data/                      # 16 pages capturées manuellement
│   ├── region_sud.txt
│   ├── aura.txt
│   ├── normandie_principale.txt
│   ├── normandie_sousdomaine.txt
│   ├── cnc_avr.txt
│   ├── paris.txt
│   ├── bretagne.txt
│   ├── grand_est.txt
│   ├── pays_de_la_loire.txt
│   ├── occitanie.txt
│   ├── ciclic.txt
│   ├── cinema93.txt
│   ├── bourgogne_franche_comte.txt
│   ├── strasbourg.txt
│   ├── pictanovo.txt
│   └── alca_nouvelle_aquitaine.txt
├── data/                            # généré par main.py (à committer)
│   ├── state.json                    # état persistant par commission (state.py)
│   ├── dates_watch.json               # état persistant par date individuelle (alerts.py)
│   ├── site_data.json                # base lue par le site (affichage)
│   ├── alerts.json                    # alertes du dernier run (non lues par le site)
│   ├── site_data.sample.json           # jeu de données factice pour dev/test
│   └── budget.json                    # dépense mensuelle cumulée (budget.py)
├── requirements.txt
├── .env.example
└── .gitignore
```
