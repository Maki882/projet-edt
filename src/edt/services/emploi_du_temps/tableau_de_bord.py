"""Ce dont l'équipe pédagogique a besoin en arrivant, pour la page d'accueil.

Pour le semestre par défaut (celui du jour) : le calcul en cours, la
version active, le nombre de points à traiter (une action, pas une
statistique), le dernier calcul **seulement s'il s'est terminé sans emploi
du temps** (`CalculSansSolution` : un calcul réussi se lit dans la version
active), et la **prochaine étape** conseillée en une phrase :

1. saisir les données (salles, groupes, puis besoins du semestre) ;
2. suivre le calcul en cours ;
3. vérifier puis lancer un calcul (l'écran « Générer » montre d'abord les
   vérifications préalables), ou lire le résultat d'un calcul qui a échoué ;
4. traiter les anomalies et les séances à programmer ;
5. consulter l'emploi du temps.

Aucune statistique : les indicateurs de qualité restent sur le résultat
d'un calcul, les nombres de salles, groupes, enseignants et besoins ne
servent ici qu'à choisir l'étape. Si une vérification n'a pas pu être faite,
`nb_a_traiter` vaut `None` (l'accueil le dit au lieu d'afficher zéro).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.domaine.enumerations import EtatCalcul
from edt.services import generation
from edt.services.emploi_du_temps import anomalies
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


ETATS_A_RELANCER = (EtatCalcul.TEMPS_DEPASSE, EtatCalcul.ARRETE)
"""États sans solution qui appellent à relancer plutôt qu'à corriger (ton « attention »)."""


@dataclass(frozen=True)
class CalculSansSolution:
    """Le dernier calcul du semestre, terminé sans emploi du temps.

    `etiquette` : son issue en quelques mots (« Calcul refusé » quand les
    vérifications préalables l'ont refusé, sinon le libellé de son état) ;
    `ton` : « attention » pour un temps dépassé ou un arrêt, « erreur » pour
    un calcul impossible, refusé ou en erreur.
    """

    suivi: generation.SuiviCalcul
    etiquette: str
    ton: str

    @classmethod
    def du_calcul(cls, suivi: generation.SuiviCalcul | None) -> CalculSansSolution | None:
        """Le calcul s'il est terminé sans solution ; `None` s'il est absent, en cours ou réussi."""
        if suivi is None or suivi.en_cours or suivi.reussi:
            return None
        if (suivi.statistiques or {}).get("refuse"):
            return cls(suivi, "Calcul refusé", "erreur")
        ton = "attention" if suivi.etat in ETATS_A_RELANCER else "erreur"
        return cls(suivi, suivi.etat.libelle, ton)


@dataclass(frozen=True)
class TableauDeBord:
    """Ce que la page d'accueil affiche (voir l'en-tête du module).

    - `nb_a_traiter` : `ATraiter.total` de la version active (la même
      définition que la page « À traiter »), `None` si une vérification n'a
      pas pu être faite ;
    - `nb_a_programmer` : séances datées à programmer, affichées seulement
      quand `nb_a_traiter` est inconnu ;
    - `calcul_sans_solution` : le dernier calcul du semestre s'il s'est
      terminé sans emploi du temps, sinon `None`.
    """

    semestre: ResumeSemestre | None
    version: ResumeVersion | None
    calcul_en_cours: generation.SuiviCalcul | None
    calcul_sans_solution: CalculSansSolution | None
    nb_a_traiter: int | None
    nb_a_programmer: int
    prochaine_etape: ProchaineEtape


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

    nb_a_traiter = None
    nb_a_programmer = 0
    if version is not None:
        # Même calcul que la page « À traiter » et la pastille de l'onglet (mémorisé).
        element = anomalies.a_traiter_version(session, semestre, version.id)
        nb_a_programmer = len(element.a_programmer)
        if element.complet:
            nb_a_traiter = element.total

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
        calcul_en_cours=en_cours,
        calcul_sans_solution=CalculSansSolution.du_calcul(dernier),
        nb_a_traiter=nb_a_traiter,
        nb_a_programmer=nb_a_programmer,
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
    # `nb_a_traiter` compte déjà les séances à programmer (une fois chacune).
    reste = nb_a_traiter if nb_a_traiter is not None else version.nb_a_programmer
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
            "salle (vérifications automatiques indisponibles)."
        )
    return ProchaineEtape(
        Etape.CONSULTER,
        phrase,
        f"/emploi-du-temps?version_id={version.id}",
        "Consulter l'emploi du temps",
    )
