"""Pipeline bout-en-bout : fetch réel des sources du registre (config.py),
extraction LLM, FALLBACK PDF automatique pour les sources sans date en HTML
(Normandie principale, Bretagne, ALCA, Ciclic...), mise à jour de l'état
(dédup par commission, pour l'affichage) ET du suivi par date individuelle
(alerts.py, pour des alertes fiables), puis écriture de :
  - data/site_data.json — base plate lue par le site (affichage)
  - data/alerts.json — alertes du run le plus récent (nouveau/ferme)

Le fallback PDF : quand extract() renvoie aucune_date_trouvee=true avec un
lien_pdf_calendrier, le PDF est téléchargé (fetch_pdf_text) et son texte est
envoyé à extract() une 2e fois — avec url=source.url (la page d'origine, PAS
le lien du PDF) pour garder url_source cohérent avec les autres runs de
cette même source, condition nécessaire pour que la dédup (make_key) la
reconnaisse correctement d'un run à l'autre.

Nécessite une connexion internet et une clé API (ANTHROPIC_API_KEY ou
DASHSCOPE_API_KEY selon PROVIDER, cf. extract.py).

Pour rejouer l'extraction sans requête réseau vers les sites sources
(données déjà capturées dans test_data/), utiliser test_extraction.py à la
place — mais noter que test_extraction.py n'écrit PAS l'état (c'est un outil
de vérification de l'extraction, pas le pipeline de production) et ne teste
PAS le fallback PDF (les fixtures offline s'arrêtent à aucune_date_trouvee).

Usage :
    python main.py
"""

from __future__ import annotations

import datetime
import json

from alerts import load_watch, save_watch, update_watch
from config import SOURCES
from extract import DRY_RUN, extract
from fetch import PageInutilisable, fetch_page, fetch_pdf_text, verifier_contenu
from state import build_site_data, load_state, save_state, update_state

STATE_PATH = "data/state.json"
SITE_DATA_PATH = "data/site_data.json"
WATCH_PATH = "data/dates_watch.json"
ALERTS_PATH = "data/alerts.json"



def _a_des_dates(commission: dict) -> bool:
    return any(isinstance(e, dict) and e.get("date_fin") for e in (commission.get("etapes") or []))


def _raison_courte(exc: Exception) -> str:
    """Raison courte pour le récapitulatif final. Pour une erreur de
    validation, on donne les CHAMPS fautifs (pas seulement « 1 validation
    error »), ce qui permet de diagnostiquer sans remonter le log."""
    if hasattr(exc, "errors") and callable(exc.errors):
        try:
            champs = [f"{'.'.join(str(x) for x in e['loc'])} ({e['type']})" for e in exc.errors()[:3]]
            if champs:
                return f"{type(exc).__name__}: " + " ; ".join(champs)[:200]
        except Exception:  # noqa: BLE001 - le récap ne doit jamais planter
            pass
    ligne = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
    return f"{type(exc).__name__}: {ligne}"[:160]


def run() -> None:
    today = datetime.date.today().isoformat()
    toutes_commissions: list[dict] = []
    sources_traitees: set[str] = set()
    echecs: list[tuple[str, str]] = []  # (source, raison) pour le récapitulatif final

    for source in SOURCES:
        print(f"\n{'=' * 70}\n{source.region} — {source.organisme}\n{source.url}\n{'=' * 70}")

        try:
            content = fetch_page(source.url, use_jina=source.needs_jina)
            verifier_contenu(content, source.attendu)
        except Exception as exc:  # noqa: BLE001 - on veut logguer et continuer
            print(f"  [ERREUR fetch] {exc}")
            echecs.append((f"{source.region} — {source.organisme}", f"fetch : {_raison_courte(exc)}"))
            continue

        try:
            result = extract(content, url=source.url, region=source.region)
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERREUR extraction] {exc}")
            echecs.append((f"{source.region} — {source.organisme}", f"extraction : {_raison_courte(exc)}"))
            continue

        # La source est considérée "traitée" dès que le fetch ET l'extraction
        # ont réussi — c'est ce qui autorise la détection de clôture (une
        # commission connue sur cette source mais absente du résultat).
        sources_traitees.add(source.url)

        if result.aucune_date_trouvee:
            if result.lien_pdf_calendrier:
                print(f"  Aucune date trouvée en HTML. Fallback PDF : {result.lien_pdf_calendrier}")
                try:
                    pdf_text = fetch_pdf_text(result.lien_pdf_calendrier)
                except Exception as exc:  # noqa: BLE001 - PDF scanné, 404, etc.
                    print(f"  [ERREUR fallback PDF — téléchargement/extraction du texte] {exc}")
                else:
                    # url=source.url (la page d'origine), PAS le lien du PDF :
                    # ça garde url_source cohérent avec les autres runs de
                    # cette même source pour la dédup (make_key en dépend).
                    try:
                        pdf_result = extract(pdf_text, url=source.url, region=source.region)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  [ERREUR fallback PDF — extraction LLM] {exc}")
                    else:
                        if pdf_result.commissions:
                            commissions_dump = [c.model_dump() for c in pdf_result.commissions]
                            toutes_commissions.extend(commissions_dump)
                            print(f"  Fallback PDF réussi : {len(commissions_dump)} commission(s) extraite(s).")
                            print(json.dumps(commissions_dump, indent=2, ensure_ascii=False))
                        else:
                            print("  Fallback PDF : toujours aucune date exploitable dans le PDF.")
            else:
                print("  Aucune date trouvée en HTML, et aucun PDF détecté sur la page.")
        else:
            commissions_dump = [c.model_dump() for c in result.commissions]
            toutes_commissions.extend(commissions_dump)
            print(json.dumps(commissions_dump, indent=2, ensure_ascii=False))

    # Une commission sans aucune date n'a rien à afficher ni à suivre : elle ne
    # doit ni entrer dans l'état, ni fabriquer de fausses alertes (cas réels :
    # Normandie page principale, Occitanie avec des étapes vides).
    avant = len(toutes_commissions)
    toutes_commissions = [c for c in toutes_commissions if _a_des_dates(c)]
    if avant != len(toutes_commissions):
        print(f"\n{avant - len(toutes_commissions)} commission(s) sans aucune date ignorée(s).")

    print(f"\n{'=' * 70}\nMise à jour de l'état ({len(toutes_commissions)} commissions extraites, "
          f"{len(sources_traitees)}/{len(SOURCES)} sources traitées avec succès)\n{'=' * 70}")

    if echecs:
        # Ces sources n'ont pas été traitées : leurs données précédentes sont
        # conservées telles quelles et rien n'est marqué comme fermé à cause
        # de l'échec (seules les sources traitées avec succès peuvent l'être).
        print(f"\n{len(echecs)} source(s) en échec — données précédentes conservées :")
        for nom, raison in echecs:
            print(f"  - {nom} : {raison}")

    if DRY_RUN:
        # GARDE-FOU CRITIQUE : en mode DRY_RUN, extract() ne renvoie que des
        # résultats factices (0 commission par source). Mettre à jour l'état
        # dans ces conditions marquerait CHAQUE commission/date déjà connue
        # comme 'clos'/'fermée' à tort (absente du run factice) — corruption
        # silencieuse de data/state.json ET data/dates_watch.json. On
        # n'écrit donc RIEN (état, site_data.json, watch, alerts.json) en
        # dry run.
        print("\nDRY_RUN actif : état, site_data.json, dates_watch.json et "
              "alerts.json NON modifiés (les résultats de ce run sont factices).")
        return

    state = load_state(STATE_PATH)
    state, changements = update_state(state, toutes_commissions, today=today, processed_url_sources=sources_traitees)
    save_state(STATE_PATH, state)

    site_data = build_site_data(state, generated_at=today)
    with open(SITE_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(site_data, f, indent=2, ensure_ascii=False)

    # Suivi par date individuelle (alerts.py) — mécanisme SÉPARÉ du suivi
    # par commission ci-dessus, spécifiquement pour des alertes fiables
    # "nouveau" / "vient de fermer" qui ne souffrent pas du bruit causé par
    # le redécoupage variable des commissions d'un run à l'autre (voir
    # docstring d'alerts.py).
    watch = load_watch(WATCH_PATH)
    watch, alertes = update_watch(watch, toutes_commissions, today=today, processed_url_sources=sources_traitees)
    save_watch(WATCH_PATH, watch)
    with open(ALERTS_PATH, "w", encoding="utf-8") as f:
        json.dump({"generated_at": today, "alerts": alertes}, f, indent=2, ensure_ascii=False)

    if changements:
        print(f"\n{len(changements)} changement(s) à notifier :")
        for c in changements:
            com = c["commission"]
            label = com.get("session_label") or com.get("etape_filiere")
            print(f"  [{c['statut'].upper()}] {com['region']} — {com['fonds']} — {label}")
    else:
        print("\nAucun changement à notifier.")

    if alertes:
        print(f"\n{len(alertes)} alerte(s) fiable(s) par date (alerts.py) :")
        for a in alertes:
            print(f"  [{a['type'].upper()}] {a['region']} — {a['fonds']} — {a['label']} ({a['date_fin']})")
    else:
        print("\nAucune alerte par date à signaler.")

    print(f"\nÉtat sauvegardé -> {STATE_PATH}")
    print(f"Base site sauvegardée -> {SITE_DATA_PATH} ({len(site_data['aides'])} aides au total)")
    print(f"Alertes sauvegardées -> {ALERTS_PATH}")


if __name__ == "__main__":
    run()
