"""Mémoire des résultats coûteux des écrans, tant que la base ne change pas.

Vérifier une version, pré-vérifier un semestre ou en faire le récapitulatif
charge l'instantané du semestre : environ une seconde sur l'établissement
complet. Les écrans de consultation (accueil, « Générer », « À traiter »,
grilles, pastille de l'onglet) redemandent ces mêmes résultats à chaque
affichage alors que rien n'a changé. Ils passent donc par `memoriser`, qui
garde le dernier résultat de chaque calcul avec une **empreinte** de la base
et le recalcule seulement quand l'empreinte change :

- taille et date de modification du fichier SQLite et de son journal WAL :
  toute écriture validée (séance déplacée, besoin modifié, calcul terminé,
  import) change l'empreinte ;
- versions actives et agrégats SQL sur leurs séances et leurs occurrences
  (nombre, jours, heures, salles, verrous, dates, statuts), qui ne dépendent
  pas du système de fichiers.

Règles :

- seuls les écrans de **consultation** s'en servent ; une modification
  (`modifications.modifier_placement`, `deplacer_occurrence`) vérifie
  toujours sans mémoire, dans sa propre transaction ;
- ce n'est pas un état de l'application : c'est une copie de résultats
  recalculables à tout moment depuis la base, oubliée dès que la base change,
  partagée par tous les utilisateurs, limitée à `TAILLE_MAX` entrées ;
- une erreur levée pendant le calcul n'est pas mémorisée ;
- un calcul en cours n'est pas refait en même temps : un second demandeur
  de la même clé, sur la même empreinte, attend le résultat du premier (au
  plus `ATTENTE_MAX_S` secondes, puis calcule lui-même). Si le premier
  échoue, le second calcule à son tour. Sans cela, l'accueil et le
  préchauffage, lancés ensemble après un calcul, vérifient deux fois la même
  version et se gênent.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from edt.db import modeles as m

journal = logging.getLogger(__name__)

TAILLE_MAX = 64
"""Nombre maximal de résultats gardés (les plus anciennement utilisés partent d'abord)."""

ATTENTE_MAX_S = 120.0
"""Attente maximale du résultat d'un même calcul en cours dans un autre fil."""

T = TypeVar("T")

_verrou = threading.Lock()
_memoire: OrderedDict[tuple, tuple[tuple, object]] = OrderedDict()
_en_cours: dict[tuple, tuple[tuple, threading.Event]] = {}
"""Calculs en cours : clé → (empreinte, événement posé à la fin du calcul)."""


def memoriser(
    session: Session, nature: str, parametres: tuple[Hashable, ...], calcul: Callable[[], T]
) -> T:
    """Le résultat de `calcul()`, repris de la mémoire si la base n'a pas changé depuis.

    `nature` et `parametres` désignent le calcul (par exemple
    `("verifier_version", (version_id,))`).
    """
    cle = (_url(session), nature, parametres)
    empreinte = empreinte_base(session)
    deja_attendu = False
    while True:
        with _verrou:
            trouve = _memoire.get(cle)
            if trouve is not None and trouve[0] == empreinte:
                _memoire.move_to_end(cle)
                return trouve[1]  # type: ignore[return-value]
            autre = _en_cours.get(cle)
            if autre is None or autre[0] != empreinte or deja_attendu:
                evenement = threading.Event()
                _en_cours[cle] = (empreinte, evenement)
                break
        deja_attendu = True
        if not _attendre(autre[1]):
            journal.warning(
                "Calcul %s %s toujours en cours après %.0f s dans un autre fil : recalculé ici",
                nature,
                parametres,
                ATTENTE_MAX_S,
            )
    try:
        valeur = calcul()
        with _verrou:
            _memoire[cle] = (empreinte, valeur)
            _memoire.move_to_end(cle)
            while len(_memoire) > TAILLE_MAX:
                _memoire.popitem(last=False)
    finally:
        with _verrou:
            if _en_cours.get(cle, (None, None))[1] is evenement:
                del _en_cours[cle]
        evenement.set()
    journal.debug("Résultat mémorisé : %s %s", nature, parametres)
    return valeur


def _attendre(evenement: threading.Event) -> bool:
    """Attend la fin d'un calcul mené par un autre fil ; faux si l'attente a expiré."""
    return evenement.wait(ATTENTE_MAX_S)


def oublier() -> None:
    """Vide la mémoire (tests)."""
    with _verrou:
        _memoire.clear()
        _en_cours.clear()


def empreinte_base(session: Session) -> tuple:
    """Empreinte peu coûteuse de la base (voir l'en-tête du module)."""
    actives = tuple(
        session.scalars(
            select(m.VersionEdt.id).where(m.VersionEdt.active.is_(True)).order_by(m.VersionEdt.id)
        )
    )
    seances = session.execute(
        select(
            func.count(m.SeanceHebdo.id),
            func.total(m.SeanceHebdo.jour * 1440 + m.SeanceHebdo.debut_min),
            func.total(m.SeanceHebdo.fin_min),
            func.total(func.coalesce(m.SeanceHebdo.salle_id, 0) * (m.SeanceHebdo.id % 997 + 1)),
            func.total(m.SeanceHebdo.verrou),
        ).where(m.SeanceHebdo.version_id.in_(actives))
    ).one()
    occurrences = session.execute(
        select(
            func.count(m.Occurrence.id),
            func.total(func.julianday(m.Occurrence.date) * 1440 + m.Occurrence.debut_min),
            func.total(func.coalesce(m.Occurrence.salle_id, 0) * (m.Occurrence.id % 997 + 1)),
            func.group_concat(func.distinct(m.Occurrence.statut)),
            func.total(func.length(m.Occurrence.statut) * (m.Occurrence.id % 991 + 1)),
        ).where(m.Occurrence.version_id.in_(actives))
    ).one()
    return (actives, tuple(seances), tuple(occurrences), _empreinte_fichiers(session))


def _url(session: Session) -> str:
    return session.get_bind().url.render_as_string(hide_password=True)


def _empreinte_fichiers(session: Session) -> tuple:
    """Taille et date de modification du fichier SQLite et de son journal (vide sinon)."""
    url = make_url(session.get_bind().url)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return ()
    chemin = Path(url.database)
    etats = []
    for fichier in (chemin, chemin.with_name(chemin.name + "-wal")):
        try:
            etat = fichier.stat()
        except FileNotFoundError:
            etats.append(None)
            continue
        etats.append((etat.st_size, etat.st_mtime_ns))
    return tuple(etats)
