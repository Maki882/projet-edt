"""Compteur de l'onglet « À traiter », recalculé seulement quand les données changent.

Le nombre d'éléments à traiter (erreurs et avertissements de la semaine type
et des séances datées des versions actives, plus les séances à programmer)
demande de vérifier ces versions : environ une seconde sur l'établissement
complet. Affiché dans le menu de chaque page, il doit pourtant ne rien coûter.

Il est donc **mémorisé** avec une **empreinte** de la base, et recalculé au
plus une fois par changement :

- versions actives et contenu de leurs séances hebdomadaires et de leurs
  occurrences (nombre, jours, heures, salles, verrous, dates, statuts),
  résumés par quelques agrégats SQL : déplacer, verrouiller, annuler une
  séance change l'empreinte ;
- taille et date de modification du fichier SQLite et de son journal WAL :
  toute écriture validée ailleurs (besoin, salle, indisponibilité, paramètre,
  calcul) change aussi l'empreinte.

Cette mémoire n'est pas un état de l'application : c'est une copie d'un
résultat calculable à tout moment depuis la base, oubliée dès que la base
change, partagée par tous les utilisateurs (une entrée par base). Le calcul
lui-même passe par `anomalies.a_traiter` : le compteur dit exactement ce que
montre l'onglet.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.services.emploi_du_temps import anomalies

journal = logging.getLogger(__name__)


@dataclass(frozen=True)
class Compteur:
    """Nombre d'éléments à traiter ; `complet` est faux si une vérification n'a pas tourné."""

    total: int
    complet: bool


_verrou = threading.Lock()
_memoire: dict[str, tuple[tuple, Compteur]] = {}


def compteur_a_traiter(session: Session) -> Compteur:
    """Le compteur, mémorisé tant que l'empreinte de la base ne change pas."""
    url = session.get_bind().url
    cle = url.render_as_string(hide_password=True)
    empreinte = empreinte_base(session)
    with _verrou:
        memoire = _memoire.get(cle)
        if memoire is not None and memoire[0] == empreinte:
            return memoire[1]
        compteur = _calculer(session)
        _memoire[cle] = (empreinte, compteur)
        journal.info(
            "Compteur « À traiter » recalculé : %d élément(s)%s",
            compteur.total,
            "" if compteur.complet else " (vérifications incomplètes)",
        )
        return compteur


def empreinte_base(session: Session) -> tuple:
    """Empreinte peu coûteuse des données dont dépend le compteur (voir l'en-tête du module)."""
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


def _calculer(session: Session) -> Compteur:
    liste = anomalies.a_traiter(session)
    return Compteur(
        total=sum(element.total for element in liste),
        complet=all(element.complet for element in liste),
    )


def oublier() -> None:
    """Vide la mémoire du compteur (tests)."""
    with _verrou:
        _memoire.clear()
