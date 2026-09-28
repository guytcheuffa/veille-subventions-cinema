"""Tests de la détection de pages inutilisables (fetch.verifier_contenu) —
100% hors-ligne, zéro coût.

Cas réels du 28/09 : Bretagne et Grand Est renvoient une page de vérification
anti-robot Cloudflare, AURA renvoie sa page d'accueil à la place de la fiche
de l'aide. Ces pages étaient comptées comme « traitées avec succès ».

Usage :
    python test_fetch.py
"""

from __future__ import annotations

import glob
import os

from config import SOURCES
from fetch import PageInutilisable, verifier_contenu

BRETAGNE = """Title: Just a moment...

URL Source: https://www.bretagne.bzh/aides/fiches/cinema-facca-production-court-metrage/

Warning: This page maybe requiring CAPTCHA, please make sure you are authorized to access this page.

Markdown Content:
## www.bretagne.bzh

## Performing security verification

This website uses a security service to protect against malicious bots."""

GRAND_EST = """Title: Just a moment...

URL Source: https://www.grandest.fr/vos-aides-regionales/production-courts-metrages/

Warning: Target URL returned error 403: Forbidden

Markdown Content:
Enable JavaScript and cookies to continue"""

ACCUEIL_AURA = ("Title: Accueil | Région Auvergne-Rhône-Alpes\n\nMarkdown Content:\nCe site utilise des cookies. "
                + "Le Puy-de-Dôme était à l'honneur mercredi 23 septembre 2026 autour de plusieurs temps forts. " * 30)


def leve(texte, attendu=()) -> str:
    try:
        verifier_contenu(texte, attendu)
    except PageInutilisable as exc:
        return str(exc)
    return ""


def run_pages_de_blocage() -> None:
    assert "anti-robot" in leve(BRETAGNE)
    assert "anti-robot" in leve(GRAND_EST)
    print("Bretagne et Grand Est (vérification anti-robot Cloudflare) : détectées : OK\n")


def run_contenu_quasi_vide() -> None:
    assert "quasi vide" in leve("Title: x\n\nMarkdown Content:\nrien")
    print("Contenu quasi vide : détecté : OK\n")


def run_mauvaise_page_aura() -> None:
    assert leve(ACCUEIL_AURA, ("dépôt", "session", "commission")) and "pas la page attendue" in leve(ACCUEIL_AURA, ("dépôt", "session"))
    assert leve(ACCUEIL_AURA) == "", "sans mots attendus, une page longue est acceptée (pas de faux rejet)"
    print("AURA : accueil renvoyé à la place de la fiche -> détecté grâce aux mots attendus : OK\n")


def run_aucune_vraie_page_rejetee() -> None:
    """Les 16 vraies pages capturées ne doivent JAMAIS être rejetées."""
    attendu_par_fixture = {os.path.basename(s.offline_fixture): s.attendu for s in SOURCES}
    n = 0
    for chemin in sorted(glob.glob("test_data/*.txt")):
        nom = os.path.basename(chemin)
        texte = open(chemin, encoding="utf-8").read()
        erreur = leve(texte, attendu_par_fixture.get(nom, ()))
        assert erreur == "", f"FAUX REJET de {nom} : {erreur}"
        n += 1
    print(f"{n} vraies pages capturées : aucune rejetée à tort : OK\n")


def run_main_ne_compte_pas_une_page_bloquee() -> None:
    """Une page bloquée est un échec explicite : la source n'est PAS traitée
    (ses données précédentes restent intactes), et le LLM n'est pas appelé
    (aucun coût sur une page inutilisable)."""
    import io
    import contextlib
    import os as _os
    _os.environ.update(DRY_RUN="false", PROVIDER="anthropic", ANTHROPIC_API_KEY="x")
    import main
    from config import Source

    appels = {"extract": 0}
    main.SOURCES = [Source(region="Bretagne", organisme="Région", url="https://ex.fr/b", needs_jina=True, offline_fixture="x")]
    main.fetch_page = lambda url, use_jina=False: BRETAGNE
    main.extract = lambda *a, **k: appels.__setitem__("extract", appels["extract"] + 1)
    tampon = io.StringIO()
    with contextlib.redirect_stdout(tampon):
        main.run()
    sortie = tampon.getvalue()
    assert appels["extract"] == 0, "le LLM ne doit pas être appelé sur une page bloquée"
    assert "0/1 sources traitées" in sortie and "1 source(s) en échec" in sortie and "PageInutilisable" in sortie, sortie
    for f in ("data/state.json", "data/site_data.json", "data/dates_watch.json", "data/alerts.json"):
        if _os.path.exists(f):
            _os.remove(f)
    print("Pipeline : page bloquée = échec explicite, source non traitée, aucun appel LLM : OK\n")


if __name__ == "__main__":
    run_pages_de_blocage()
    run_contenu_quasi_vide()
    run_mauvaise_page_aura()
    run_aucune_vraie_page_rejetee()
    run_main_ne_compte_pas_une_page_bloquee()
    print("TOUS LES TESTS FETCH SONT PASSÉS.")
