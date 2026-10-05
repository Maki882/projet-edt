"""État de l'établissement en un coup d'œil, pour la page d'accueil.

Pour le semestre par défaut (celui du jour) : données saisies, calcul en
cours ou dernier calcul, version active et ses quatre indicateurs
(pourcentage de séances placées, nombre de conflits, taux d'utilisation des
salles, changements de campus), éléments à traiter, et la **prochaine
étape** conseillée en une phrase :

1. saisir les données (salles, groupes, puis besoins du semestre) ;
2. suivre le calcul en cours ;
3. vérifier puis lancer un calcul (l'écran « Générer » montre d'abord les
   vérifications préalables), ou lire le résultat d'un calcul qui a échoué ;
4. traiter les anomalies et les séances à programmer ;
5. consulter l'emploi du temps.

Les indicateurs viennent du vérificateur de la semaine type : tant qu'il
n'est pas livré, le tableau de bord le dit (`controle.motif`) au lieu
d'afficher des zéros.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.services import generation
from edt.services.emploi_du_temps import raccords
from edt.services.emploi_du_temps.presentation import pluriel
from edt.services.emploi_du_temps.versions import (
    ResumeSemestre,
    ResumeVersion,
    choisir_semestre,
    version_active,
)

journal = logging.getLogger(__name__)


class Etape:
    """Codes des prochaines étapes."""

    CHARGER = "charger"
    SAISIR = "saisir"
    SUIVRE = "suivre"
    GENERER = "generer"
    RELIRE = "relire"
    TRAITER = "traiter"
    CONSULTER = "consulter"


@dataclass(frozen=True)
class ProchaineEtape:
    """La prochaine étape conseillée : une phrase, son écran et d'autres chemins possibles.

    `autres_liens` : couples (adresse, libellé) des autres façons d'avancer
    (importer des fichiers au lieu de saisir, par exemple).
    """

    code: str
    phrase: str
    lien: str | None
    libelle_lien: str | None
    autres_liens: tuple[tuple[str, str], ...] = ()


ECRAN_JEUX = "/jeux"
ECRAN_IMPORT = "/import"
ECRAN_SALLES = "/salles"
ECRAN_GROUPES = "/groupes"
ECRAN_BESOINS = "/besoins"


@dataclass(frozen=True)
class TableauDeBord:
    """Ce que la page d'accueil affiche au-delà des compteurs de la base."""

    semestre: ResumeSemestre | None
    version: ResumeVersion | None
    nb_salles: int
    nb_groupes: int
    nb_enseignants: int
    nb_besoins: int
    calcul_en_cours: generation.SuiviCalcul | None
    dernier_calcul: generation.SuiviCalcul | None
    controle: raccords.Controle | None
    nb_a_traiter: int | None
    nb_a_programmer: int
    prochaine_etape: ProchaineEtape

    @property
    def indicateurs(self) -> raccords.Indicateurs | None:
        """Indicateurs de la version active, s'ils sont connus."""
        if self.controle is None or self.controle.verification is None:
            return None
        return self.controle.verification.indicateurs

    @property
    def nb_conflits(self) -> int | None:
        """Anomalies bloquantes de la semaine type active, si la vérification a tourné."""
        if self.controle is None or self.controle.verification is None:
            return None
        return self.controle.verification.nb_erreurs


def tableau_de_bord(session: Session, aujourdhui: dt.date | None = None) -> TableauDeBord:
    """Le tableau de bord du semestre par défaut (voir l'en-tête du module)."""

    def compter(modele, *conditions) -> int:
        return session.scalar(select(func.count()).select_from(modele).where(*conditions)) or 0

    semestre = choisir_semestre(session, None, aujourdhui)
    nb_salles = compter(m.Salle)
    nb_groupes = compter(m.Groupe)
    nb_enseignants = compter(m.Enseignant)
    nb_besoins = compter(m.Besoin, m.Besoin.semestre_id == semestre.id) if semestre else 0

    en_cours = generation.calcul_en_cours()
    dernier = generation.dernier_calcul(semestre.id) if semestre else generation.dernier_calcul()
    version = version_active(session, semestre.id) if semestre else None

    controle = None
    nb_a_traiter = None
    if version is not None:
        controle = raccords.controler(raccords.verifier_version, session, version.id)
        if controle.verification is not None:
            nb_a_traiter = (
                controle.verification.nb_erreurs + controle.verification.nb_avertissements
            )

    etape = _prochaine_etape(
        semestre=semestre,
        nb_salles=nb_salles,
        nb_groupes=nb_groupes,
        nb_enseignants=nb_enseignants,
        nb_besoins=nb_besoins,
        en_cours=en_cours,
        dernier=dernier,
        version=version,
        nb_a_traiter=nb_a_traiter,
    )
    return TableauDeBord(
        semestre=semestre,
        version=version,
        nb_salles=nb_salles,
        nb_groupes=nb_groupes,
        nb_enseignants=nb_enseignants,
        nb_besoins=nb_besoins,
        calcul_en_cours=en_cours,
        dernier_calcul=dernier,
        controle=controle,
        nb_a_traiter=nb_a_traiter,
        nb_a_programmer=version.nb_a_programmer if version else 0,
        prochaine_etape=etape,
    )


def _prochaine_etape(
    *,
    semestre: ResumeSemestre | None,
    nb_salles: int,
    nb_groupes: int,
    nb_enseignants: int,
    nb_besoins: int,
    en_cours: generation.SuiviCalcul | None,
    dernier: generation.SuiviCalcul | None,
    version: ResumeVersion | None,
    nb_a_traiter: int | None,
) -> ProchaineEtape:
    if not (nb_salles or nb_groupes or nb_enseignants):
        return ProchaineEtape(
            Etape.CHARGER,
            "La base ne contient encore aucun établissement : charger un établissement fictif "
            "pour découvrir l'application, ou importer les fichiers de l'école.",
            ECRAN_JEUX,
            "Charger un établissement fictif",
            ((ECRAN_IMPORT, "Importer des fichiers"), (ECRAN_SALLES, "Saisir à la main")),
        )
    if semestre is None or not nb_salles or not nb_groupes:
        manquants = [
            nom
            for nom, nombre in (("les salles", nb_salles), ("les groupes", nb_groupes))
            if not nombre
        ]
        return ProchaineEtape(
            Etape.SAISIR,
            "Compléter les données de l'établissement : " + " et ".join(manquants) + " manquent "
            "encore, puis saisir les besoins d'enseignement.",
            ECRAN_SALLES if not nb_salles else ECRAN_GROUPES,
            "Saisir les salles" if not nb_salles else "Saisir les groupes",
            ((ECRAN_IMPORT, "Importer des fichiers"),),
        )
    nom = semestre.libelle[:1].lower() + semestre.libelle[1:]
    if en_cours is not None:
        return ProchaineEtape(
            Etape.SUIVRE,
            "Un calcul d'emploi du temps est en cours : suivre son avancement.",
            f"/calculs/{en_cours.id}",
            "Suivre le calcul",
        )
    if not nb_besoins:
        return ProchaineEtape(
            Etape.SAISIR,
            f"Saisir les besoins d'enseignement du {nom} : c'est ce que le calcul place dans "
            "l'emploi du temps.",
            ECRAN_BESOINS,
            "Saisir les besoins",
            ((ECRAN_IMPORT, "Importer des besoins"),),
        )
    if version is None:
        if dernier is not None and not dernier.reussi:
            return ProchaineEtape(
                Etape.RELIRE,
                f"Le dernier calcul du {nom} n'a pas abouti : lire son résultat, corriger les "
                "données ou les contraintes, puis relancer.",
                f"/calculs/{dernier.id}",
                "Voir le dernier calcul",
            )
        return ProchaineEtape(
            Etape.GENERER,
            f"Vérifier les données et lancer le calcul de l'emploi du temps du {nom} : l'écran "
            "« Générer » montre d'abord les vérifications préalables.",
            f"/calculs?semestre_id={semestre.id}",
            "Générer l'emploi du temps",
        )
    reste = (nb_a_traiter or 0) + version.nb_a_programmer
    if reste:
        return ProchaineEtape(
            Etape.TRAITER,
            f"L'emploi du temps actif du {nom} a {pluriel(reste, 'point')} à traiter : "
            "anomalies ou séances à programmer.",
            f"/a-traiter?semestre_id={semestre.id}",
            "Voir ce qui reste à traiter",
        )
    phrase = f"L'emploi du temps du {nom} est prêt : le consulter par groupe, enseignant ou salle."
    if nb_a_traiter is None:
        phrase = (
            f"L'emploi du temps du {nom} est généré : le consulter par groupe, enseignant ou "
            "salle (les vérifications automatiques ne sont pas encore disponibles)."
        )
    return ProchaineEtape(
        Etape.CONSULTER,
        phrase,
        f"/emploi-du-temps?version_id={version.id}",
        "Consulter l'emploi du temps",
    )
