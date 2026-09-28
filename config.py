"""Registre des sources à surveiller.

Chaque entrée = une URL à fetcher + son offline_fixture associé (utilisé par
test_extraction.py pour rejouer l'extraction sans requête réseau).

`needs_jina` indique si la source nécessite de passer par Jina Reader
(r.jina.ai) plutôt qu'un fetch direct — cas des sites protégés par un WAF
anti-bot (ex: auvergnerhonealpes.fr redirige vers /shield?u=... en fetch direct).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Source:
    region: str
    organisme: str
    url: str
    needs_jina: bool
    offline_fixture: str  # chemin relatif dans test_data/
    notes: str = ""
    # Au moins un de ces mots doit figurer dans la page reçue, sinon elle est
    # jugée inutilisable (le site a renvoyé une autre page, ex: son accueil).
    attendu: tuple[str, ...] = ()


SOURCES: list[Source] = [
    Source(
        region="Provence-Alpes-Côte d'Azur",
        organisme="Région Sud",
        url="https://www.maregionsud.fr/vos-aides/detail/soutien-a-la-production-de-court-metrage-de-fiction",
        needs_jina=False,
        offline_fixture="test_data/region_sud.txt",
        notes="Calendrier récurrent en texte libre (31/01, 15/04, 30/09 chaque "
        "année). RDV préalable CONDITIONNEL (uniquement en cas de cumul d'aides "
        "avec d'autres collectivités).",
    ),
    Source(
        region="Auvergne-Rhône-Alpes",
        organisme="Région Auvergne-Rhône-Alpes",
        url="https://www.auvergnerhonealpes.fr/aides/produire-un-court-metrage",
        needs_jina=True,
        attendu=("dépôt", "session", "commission"),
        offline_fixture="test_data/aura.txt",
        notes="Protégé par un WAF anti-bot : un fetch direct redirige vers "
        "/shield?u=... — Jina Reader obligatoire. Sessions multi-étapes avec "
        "fenêtre horaire précise ; sessions 2 et 3 souvent 'date communiquée "
        "ultérieurement' (le LLM doit omettre plutôt qu'halluciner).",
    ),
    Source(
        region="Normandie",
        organisme="Normandie Images",
        url="https://www.normandieimages.fr/creation-production/fonds-d-aides/14-creation-et-production/fonds-d-aide/26-production-de-court-metrage-cinema",
        needs_jina=False,
        offline_fixture="test_data/normandie_principale.txt",
        notes="Page principale : AUCUNE date en HTML, calendrier uniquement "
        "dans un PDF lié ('Calendrier2026_5.pdf') -> déclenche le fallback PDF "
        "(aucune_date_trouvee=true + lien_pdf_calendrier rempli). RDV préalable "
        "SYSTÉMATIQUE (lien Google Forms dédié).",
    ),
    Source(
        region="Normandie",
        organisme="Normandie Images",
        url="https://inscriptionsfondsaide.normandieimages.net/fonds-num/26",
        needs_jina=False,
        offline_fixture="test_data/normandie_sousdomaine.txt",
        notes="Sous-domaine dédié : source de vérité réelle pour les dates "
        "Normandie (à privilégier sur la page principale). 3 sessions x 4 "
        "étapes, avec fenêtres datées précises pour le RDV préalable et le "
        "dépôt des dossiers.",
    ),
    Source(
        region="National",
        organisme="CNC",
        url="https://www.cnc.fr/professionnels/aides-et-financements/court-metrage/production/aide-avant-realisation-a-la-production-de-films-de-court-metrage_191116",
        needs_jina=False,
        offline_fixture="test_data/cnc_avr.txt",
        notes="Aide NATIONALE (pas l'une des 13 régions du cahier des charges, "
        "à traiter séparément dans le futur si besoin). Cas le plus complexe "
        "du registre : 4 tableaux distincts (AVR1 comité de lecture x6 "
        "sessions, AVR1 commission plénière x6, AVR2 comité de lecture x6, "
        "AVR2 commission plénière x9) — bon test de robustesse sur une page "
        "à forte densité tabulaire.",
    ),
    Source(
        region="Paris",
        organisme="Ville de Paris / Mission Cinéma",
        url="https://www.paris.fr/pages/les-actions-de-paris-pour-le-cinema-2313",
        needs_jina=False,
        offline_fixture="test_data/paris.txt",
        notes="3 sessions par an, chacune en 3 étapes (dépôt à jour précis, "
        "comité de lecture et comité de sélection donnés au MOIS SEULEMENT, "
        "sans jour précis) — pattern de granularité mixte à surveiller : le "
        "LLM doit gérer une date_fin partielle (mois/année) sans inventer un "
        "jour.",
    ),
    Source(
        region="Bretagne",
        organisme="Région Bretagne",
        url="https://www.bretagne.bzh/aides/fiches/cinema-facca-production-court-metrage/",
        needs_jina=True,
        offline_fixture="test_data/bretagne.txt",
        notes="Protégé par un WAF anti-bot (même pattern qu'AURA et Grand "
        "Est) : le fetch direct renvoie 403 Forbidden — Jina Reader "
        "obligatoire (confirmé sur un run réel le 2026-09-27, alors que "
        "l'accès direct fonctionnait encore au moment des tests initiaux). "
        "AUCUNE date en HTML, calendrier uniquement dans un PDF lié "
        "('Calendrier-Court-metrage-2026-2027') -> même pattern que Normandie "
        "principale (aucune_date_trouvee=true + lien_pdf_calendrier rempli). "
        "RDV préalable SYSTÉMATIQUE (formulaire Google Forms dédié).",
    ),
    Source(
        region="Grand Est",
        organisme="Région Grand Est",
        url="https://www.grandest.fr/vos-aides-regionales/production-courts-metrages/",
        needs_jina=True,
        offline_fixture="test_data/grand_est.txt",
        notes="Protégé par un WAF anti-bot (même pattern qu'AURA) : le fetch "
        "direct est bloqué, Jina Reader obligatoire. Fixture actuelle basée "
        "sur un extrait de résultat de recherche (contenu partiel) faute "
        "d'accès direct au HTML complet au moment de la création — à "
        "recapturer via Jina Reader dès que possible pour une fixture plus "
        "fidèle. 2 dates de dépôt annuel (15 mars, 15 novembre).",
    ),
    Source(
        region="Pays de la Loire",
        organisme="Région Pays de la Loire",
        url="https://www.paysdelaloire.fr/les-aides/fonds-daide-la-creation-cinematographique-audiovisuelle-et-numerique",
        needs_jina=False,
        offline_fixture="test_data/pays_de_la_loire.txt",
        notes="Cas le plus simple du registre : 3 dates ponctuelles avec jour "
        "de semaine explicite, aucune session, aucun RDV préalable. Bon cas "
        "de référence/regression test.",
    ),
    Source(
        region="Occitanie",
        organisme="Région Occitanie / Pyrénées-Méditerranée",
        url="https://www.laregion.fr/Cinema-Audiovisuel-Multimedia-Aide-a-la-creation-audiovisuelle",
        needs_jina=False,
        offline_fixture="test_data/occitanie.txt",
        notes="Pattern inédit : dates de dépôt DIFFÉRENTES PAR GENRE D'ŒUVRE "
        "(Animation / Documentaire / Fiction CM / Fiction LM-AV), chacune "
        "avec son propre calendrier annuel. Le mandat porte sur le court "
        "métrage : la ligne pertinente est 'Fiction (CM, XR)'. Bon test pour "
        "vérifier que le LLM ne mélange pas les dates des différents genres.",
    ),
    Source(
        region="Centre-Val de Loire",
        organisme="Ciclic",
        url="https://ciclic.fr/aides-accompagnement/catalogue-des-aides/contrat-dobjectifs-et-de-moyens-com-audiovisuel",
        needs_jina=False,
        offline_fixture="test_data/ciclic.txt",
        notes="3 dépôts par an, chacun en fenêtre (ex: 'du 01 au 10 octobre "
        "2025 midi') + date de commission séparée. RDV/accueil de tournage "
        "recommandé en amont mais pas formulé comme une action préalable "
        "obligatoire au sens strict (pas de conséquence d'inéligibilité "
        "explicite) — bon test pour ne pas sur-interpréter 'il est demandé "
        "de solliciter' comme un RDV systématique de type Normandie.",
    ),
    Source(
        region="Île-de-France",
        organisme="Cinémas 93 (Département Seine-Saint-Denis)",
        url="https://www.cinemas93.org/accompagnement-de-la-creation/aide-au-film-court/",
        needs_jina=False,
        offline_fixture="test_data/cinema93.txt",
        notes="Page très longue et verbeuse (règlement complet inclus) — bon "
        "test de capacité à isoler le bloc 'Inscription et calendrier' "
        "pertinent au milieu de beaucoup de texte non pertinent (FAQ, "
        "archives, résultats passés). 1-2 sessions/an, pas de RDV préalable "
        "obligatoire (juste un webinaire optionnel).",
    ),
    Source(
        region="Bourgogne-Franche-Comté",
        organisme="Région Bourgogne-Franche-Comté",
        url="https://www.bourgognefranchecomte.fr/node/278",
        needs_jina=False,
        offline_fixture="test_data/bourgogne_franche_comte.txt",
        notes="2 fenêtres récurrentes SANS année explicite (1er-28 février, "
        "1er-31 juillet) — teste la règle 10 du prompt (choisir la prochaine "
        "occurrence). Dates de commission non communiquées à l'avance (juste "
        "'en avril'/'en septembre', sans jour). Pas de numerus clausus "
        "explicitement mentionné.",
    ),
    Source(
        region="Grand Est",
        organisme="Eurométropole de Strasbourg",
        url="https://culture.strasbourg.eu/pro/discipline-domaine/audiovisuel-cinema/fonds-soutien-production/",
        needs_jina=False,
        offline_fixture="test_data/strasbourg.txt",
        notes="3 dates ponctuelles récurrentes SANS année (15 nov, 15 mars, "
        "15 juin). ATTENTION : ce fonds couvre TOUT l'audiovisuel (séries "
        "TV, documentaires, longs métrages...), pas seulement le court "
        "métrage — bon test pour vérifier que le LLM restitue fidèlement "
        "'typologie_oeuvre' sans réduire abusivement au seul court métrage. "
        "Cumulable avec l'aide Région Grand Est (source séparée du "
        "registre) : à ne pas dédupliquer entre elles, ce sont deux "
        "financements distincts.",
    ),
    Source(
        region="Hauts-de-France",
        organisme="Pictanovo",
        url="https://www.pictanovo.com/fond/court-metrage/",
        needs_jina=False,
        offline_fixture="test_data/pictanovo.txt",
        notes="Format le plus propre et structuré du registre : sessions "
        "numérotées ('#3 Comité de lecture Court Métrage 2026') avec date de "
        "dépôt + date de comité clairement séparées. RDV préalable "
        "obligatoire (pré-dossier par mail requis avant tout rendez-vous). "
        "Bon cas de référence pour un run de non-régression rapide.",
    ),
    Source(
        region="Nouvelle-Aquitaine",
        organisme="ALCA Nouvelle-Aquitaine",
        url="https://alca-nouvelle-aquitaine.fr/fr/cinema-audiovisuel/fonds-de-soutien-au-cinema-et-l-audiovisuel/l-aide-la-production",
        needs_jina=False,
        offline_fixture="test_data/alca_nouvelle_aquitaine.txt",
        notes="Calendrier complet uniquement en PDF (2 PDF distincts : "
        "calendrier auditions + calendrier dépôts) -> même pattern que "
        "Normandie/Bretagne (aucune_date_trouvee=true + lien_pdf_calendrier "
        "rempli). PIÈGE : contrairement à Normandie/Bretagne, le texte "
        "contient quand même UNE date exploitable en clair ('prochaine "
        "session, du 11 janvier 2027') — bon test pour voir si le LLM sait "
        "extraire cette date isolée tout en signalant aussi le PDF pour le "
        "calendrier complet, plutôt que de tout classer en "
        "aucune_date_trouvee par réflexe.",
    ),
]
