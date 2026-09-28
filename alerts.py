"""Suivi des échéances individuelles (indépendant du découpage en
commissions) pour générer des alertes fiables : nouvelle date détectée,
échéance retirée alors qu'elle était encore à venir.

Contrairement à state.py (qui suit des COMMISSIONS — fragile face au
redécoupage que fait parfois le LLM d'un run à l'autre : 12 sessions
séparées un run, 1 seule combinée le suivant), ce module suit des DATES
individuelles par source (url_source + date_fin), qui sont des faits exacts
sans aucune ambiguïté de regroupement. Une date reste la même date, peu
importe comment le LLM a organisé le texte autour.

Les deux mécanismes sont complémentaires et indépendants :
- state.py reste la source de vérité pour l'affichage (site_data.json) —
  c'est déjà fiable pour "quelles sont les prochaines échéances".
- ce module ne sert qu'à générer la couche d'alertes ("nouveau" / "ferme
  bientôt" / "vient de fermer") de façon robuste, sans hériter du bruit de
  state.py.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

FERME_BIENTOT_JOURS = 7

STATUT_NOUVELLE = "nouvelle"
STATUT_VIENT_DE_FERMER = "vient_de_fermer"

_CHAMPS_META = ("region", "fonds", "organisme", "label", "date_fin", "url_source")


def _cle(url_source: str, date_fin: str) -> str:
    return f"{url_source}|{date_fin}"


def load_watch(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_watch(path: str | Path, watch: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(watch, indent=2, ensure_ascii=False), encoding="utf-8")


def update_watch(
    watch: dict,
    commissions: list[dict],
    today: str,
    processed_url_sources: set[str],
) -> tuple[dict, list[dict]]:
    """Met à jour `watch` (modifié en place et retourné) avec les dates
    individuelles observées dans `commissions` ce run. Retourne
    (watch_mis_a_jour, alertes) — `alertes` ne contient jamais de date déjà
    passée (une date passée qui apparaît ou disparaît n'a aucune valeur
    informative pour un visiteur)."""
    vues_ce_run: set[str] = set()
    alertes: list[dict] = []

    for commission in commissions:
        url = commission.get("url_source", "")
        for etape in commission.get("etapes", []):
            date_fin = etape.get("date_fin")
            if not date_fin:
                continue
            cle = _cle(url, date_fin)
            vues_ce_run.add(cle)

            meta = {
                "region": commission.get("region"),
                "fonds": commission.get("fonds"),
                "organisme": commission.get("organisme"),
                "label": etape.get("label"),
                "date_fin": date_fin,
                "url_source": url,
            }

            if cle not in watch:
                watch[cle] = {**meta, "first_seen": today, "last_seen": today, "closed": False}
                if date_fin >= today:
                    alertes.append({"type": STATUT_NOUVELLE, **meta})
            else:
                entree = watch[cle]
                etait_fermee = entree.get("closed", False)
                watch[cle] = {**entree, **meta, "last_seen": today, "closed": False}
                if etait_fermee and date_fin >= today:
                    # Réapparue après avoir été marquée fermée : redevient
                    # une info utile pour un visiteur.
                    alertes.append({"type": STATUT_NOUVELLE, **meta})

    # Détection des fermetures : une date connue, sur une source traitée ce
    # run, absente du résultat -> fermée. On n'alerte que si elle était
    # encore à venir (une date déjà passée qui disparaît est normale, la
    # page ne référence simplement plus une échéance obsolète).
    for cle, entree in watch.items():
        url = entree.get("url_source", "")
        if url in processed_url_sources and cle not in vues_ce_run and not entree.get("closed"):
            entree["closed"] = True
            if entree["date_fin"] >= today:
                alertes.append({"type": STATUT_VIENT_DE_FERMER, **{k: entree[k] for k in _CHAMPS_META}})

    return watch, alertes


def echeances_proches(watch: dict, today: str, jours: int = FERME_BIENTOT_JOURS) -> list[dict]:
    """Toutes les échéances actives (non fermées) dont la date tombe dans
    les `jours` prochains jours, à partir de l'état de suivi actuel — sans
    dépendre de l'historique. Fourni pour un export centralisé côté
    pipeline ; le site peut aussi recalculer ceci lui-même directement à
    partir de site_data.json (ce qui reste exact même si le visiteur ouvre
    la page plusieurs jours après le dernier run)."""
    limite = (date.fromisoformat(today) + timedelta(days=jours)).isoformat()
    resultat = [
        dict(entree) for entree in watch.values()
        if not entree.get("closed") and today <= entree["date_fin"] <= limite
    ]
    return sorted(resultat, key=lambda e: e["date_fin"])
