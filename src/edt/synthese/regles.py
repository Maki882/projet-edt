"""Règles personnalisées des jeux fictifs : choix, exigences pour le témoin, écriture en base.

Conception : `docs/regles-personnalisees.md` (§ 4.7). Chaque jeu reçoit
quelques règles réalistes, choisies selon les groupes, matières et sites
présents (le jeu grand les a toutes, les autres une partie), plus les
règles de base du catalogue, laissées « proposées » (sans effet).

Le témoin respecte toutes les règles **actives** de sorte « séance » et de
réglage (`JOURS_TRAVAILLES`, `PLAGE_HORAIRE`,
`CRENEAUX_INTERDITS`, `CAMPUS`, `SALLES`, `PAUSE_DEJEUNER`,
`PAUSE_MINIMALE`), quel que soit leur niveau, et `MAX_PAR_JOUR` :
`ExigencesRegles` les traduit en exigences simples pour le moteur
(`edt.synthese.temoin`). Les autres règles actives sont choisies pour être
respectées **par construction** (`RESPECT_CONSTRUCTION`) :

- « Les A3 ne changent pas de campus dans la journée » : la règle `CAMPUS`
  place toutes les séances de la promotion à Issy ;
- « M. X vient au plus deux jours par semaine » : posée sur des vacataires
  dont les indisponibilités récurrentes laissent au plus deux jours.

Jours : le témoin suit la précédence du contrat, comme le vérificateur et
le solveur (`Instantane.reglages`) ; un jour ouvert par une règle seule (le
samedi d'A3-APP1) lui est permis, mais il le garde en dernier recours, le
samedi passant après les jours de semaine dans son ordre de préférence.

Le vérificateur et le solveur vérifient et posent les quatorze types ; le
test `tests/unitaires/synthese/test_verificateur.py` s'assure que le
témoin ne provoque aucune anomalie `REGLE`.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import JOURS, SAMEDI
from edt.domaine.parametres import DEJEUNER_FIXE, DEJEUNER_LIBRE, Parametres
from edt.domaine.regles import (
    JOURS_DIFFERENTS,
    MODE_IMPOSER,
    MODE_INTERDIRE,
    REGLES_DE_BASE,
    UNITE_JOURNEE,
    UNITE_MINUTES,
    EtatRegle,
    NaturePortee,
    OrigineRegle,
    TypeRegle,
    duree_depuis_texte,
    heure_depuis_texte,
    jour_depuis_texte,
    valider_parametres,
    valider_portee,
)
from edt.erreurs import ErreurDonneesInvalides
from edt.synthese.temoin import EnseignantTemoin

journal = logging.getLogger(__name__)

JOURNEE = 24 * 60
RESPECT_POSEE = "appliquée dans l'emploi du temps de référence"
RESPECT_CONSTRUCTION = "respectée d'office par l'emploi du temps de référence"
RESPECT_SANS_EFFET = "sans effet (proposée ou inactive)"

TYPES_POSES = frozenset(
    {
        TypeRegle.JOURS_TRAVAILLES,
        TypeRegle.PLAGE_HORAIRE,
        TypeRegle.CRENEAUX_INTERDITS,
        TypeRegle.CAMPUS,
        TypeRegle.SALLES,
        TypeRegle.PAUSE_DEJEUNER,
        TypeRegle.PAUSE_MINIMALE,
        TypeRegle.MAX_PAR_JOUR,
    }
)
"""Types que le témoin pose lui-même ; les autres règles actives sont respectées par construction."""

SEMAINE = ["lundi", "mardi", "mercredi", "jeudi", "vendredi"]


@dataclass
class ReglePrevue:
    """Une règle du jeu fictif, avant son écriture en base.

    `cible` : groupe, type de séance, matière, campus (objets ORM) ou
    enseignant du vivier (`EnseignantTemoin`, écrit en base après le témoin) ;
    `None` pour l'établissement. `parametres` : brouillon, validé à l'écriture.
    """

    type: TypeRegle
    portee: NaturePortee
    cible: Any
    parametres: dict[str, Any]
    niveau: Niveau
    etat: EtatRegle = EtatRegle.ACTIVE
    origine: OrigineRegle = OrigineRegle.SAISIE
    semestre: str | None = None
    commentaire: str | None = None
    respect: str = RESPECT_POSEE

    @property
    def active(self) -> bool:
        return self.etat is EtatRegle.ACTIVE

    def vaut_pour(self, semestre: str) -> bool:
        """Vrai si la règle est active et couvre le semestre `semestre`."""
        return self.active and self.semestre in (None, semestre)


@dataclass(frozen=True)
class Reperes:
    """Ce que le choix des règles doit trouver dans le jeu (par code ou libellé)."""

    groupes: Mapping[str, m.Groupe]
    matieres: Mapping[tuple[int, str, str], m.Matiere]
    types_seance: Mapping[str, m.TypeSeance]
    campus: Mapping[str, m.Campus]
    salles: Sequence[m.Salle]
    site_promotion: Mapping[int, str]
    semestres: Sequence[str]


def prevoir_regles(r: Reperes) -> list[ReglePrevue]:
    """Règles de groupes, de matières et de types du jeu (hors enseignants), selon ce qui existe."""
    regles: list[ReglePrevue] = []

    def groupe(*codes: str) -> m.Groupe | None:
        return next((r.groupes[c] for c in codes if c in r.groupes), None)

    def matiere(*cles: tuple[int, str, str]) -> m.Matiere | None:
        return next((r.matieres[c] for c in cles if c in r.matieres), None)

    if (a1 := groupe("A1")) is not None:
        regles.append(
            ReglePrevue(
                TypeRegle.JOURS_TRAVAILLES,
                NaturePortee.GROUPE,
                a1,
                {"jours": SEMAINE},
                Niveau.NECESSAIRE,
                commentaire="Pas de cours le samedi en 1re année, même pour les TP.",
            )
        )
    if (apprentis := groupe("A3-APP1")) is not None:
        regles.append(
            ReglePrevue(
                TypeRegle.JOURS_TRAVAILLES,
                NaturePortee.GROUPE,
                apprentis,
                {"jours": [*SEMAINE, "samedi"]},
                Niveau.IMPORTANT,
                commentaire="Les apprentis peuvent rattraper le samedi matin pendant leurs "
                "semaines d'école.",
            )
        )
    if "S1" in r.semestres and (classe := groupe("A2-G3", "A3-G2", "A1-G1")) is not None:
        regles.append(
            ReglePrevue(
                TypeRegle.PAUSE_DEJEUNER,
                NaturePortee.GROUPE,
                classe,
                {"mode": DEJEUNER_LIBRE, "debut": "11:45", "fin": "14:15", "duree": 75},
                Niveau.IMPORTANT,
                semestre="S1",
                commentaire="Demande de la responsable de promotion (activité associative).",
            )
        )
    if (apprentis := groupe("A4-APP1", "A3-APP2")) is not None:
        regles.append(
            ReglePrevue(
                TypeRegle.PAUSE_MINIMALE,
                NaturePortee.GROUPE,
                apprentis,
                {"duree": 30},
                Niveau.IMPORTANT,
                commentaire="Les apprentis changent souvent de salle : une demi-heure entre deux "
                "séances.",
            )
        )
    if "CM" in r.types_seance:
        regles.append(
            ReglePrevue(
                TypeRegle.CRENEAUX_INTERDITS,
                NaturePortee.TYPE_SEANCE,
                r.types_seance["CM"],
                {"jours": ["vendredi"], "debut": "13:45", "fin": "18:00"},
                Niveau.IMPORTANT,
                commentaire="Les amphithéâtres servent aux conférences le vendredi après-midi.",
            )
        )
    campus_a3 = None
    if (a3 := groupe("A3")) is not None and "ISSY" in r.campus:
        campus_a3 = ReglePrevue(
            TypeRegle.CAMPUS,
            NaturePortee.GROUPE,
            a3,
            {"mode": MODE_IMPOSER, "campus": ["ISSY"]},
            Niveau.NECESSAIRE,
            commentaire="La 3e année est installée à Issy.",
        )
        regles.append(campus_a3)
    algo = matiere((1, "S1", "Algorithmique et programmation 1"))
    if algo is not None and "NDC" in r.campus:
        regles.append(
            ReglePrevue(
                TypeRegle.CAMPUS,
                NaturePortee.MATIERE,
                algo,
                {"mode": MODE_IMPOSER, "campus": ["NDC"]},
                Niveau.NECESSAIRE,
                commentaire="Les TP d'informatique ont besoin des salles informatiques de NDC.",
            )
        )
    electronique = matiere(
        (3, "S1", "Électronique embarquée"), (1, "S1", "Électronique numérique 1")
    )
    if electronique is not None and electronique.promotion is not None:
        annee = int(electronique.promotion.code[1:])
        site = r.site_promotion.get(annee)
        labos = sorted(
            (s for s in r.salles if s.type_salle.code == "TP" and s.campus.code == site),
            key=lambda s: s.code,
        )[:4]
        if labos:
            regles.append(
                ReglePrevue(
                    TypeRegle.SALLES,
                    NaturePortee.MATIERE,
                    electronique,
                    {"mode": MODE_IMPOSER, "salles": [s.code for s in labos]},
                    Niveau.IMPORTANT,
                    commentaire="Seuls ces laboratoires ont les cartes de développement du module.",
                )
            )
    if campus_a3 is not None:
        regles.append(
            ReglePrevue(
                TypeRegle.CAMPUS_PAR_JOUR,
                NaturePortee.GROUPE,
                campus_a3.cible,
                {},
                Niveau.IMPORTANT,
                commentaire="Pas d'aller-retour entre Issy et Paris dans la journée.",
                respect=RESPECT_CONSTRUCTION,
            )
        )
    if (charge := groupe("A5", "A3-APP2")) is not None:
        regles.append(
            ReglePrevue(
                TypeRegle.MAX_PAR_JOUR,
                NaturePortee.GROUPE,
                charge,
                {"unite": UNITE_MINUTES, "maximum": 360},
                Niveau.IMPORTANT,
                commentaire="Laisser du temps pour le projet de fin d'études et l'entreprise.",
            )
        )
    analyse = matiere((1, "S1", "Analyse 1"), (3, "S1", "Traitement du signal"))
    if analyse is not None:
        regles.append(
            ReglePrevue(
                TypeRegle.REPARTITION_MATIERE,
                NaturePortee.MATIERE,
                analyse,
                {"mode": JOURS_DIFFERENTS},
                Niveau.FACULTATIF,
                etat=EtatRegle.INACTIVE,
                commentaire="Essai de l'an dernier, suspendu.",
                respect=RESPECT_SANS_EFFET,
            )
        )
    return regles


def regles_de_base() -> list[ReglePrevue]:
    """Les règles de base du catalogue, proposées (sans effet tant qu'elles ne sont pas confirmées)."""
    return [
        ReglePrevue(
            base.type,
            NaturePortee.ETABLISSEMENT,
            None,
            dict(base.parametres),
            base.niveau,
            etat=EtatRegle.PROPOSEE,
            origine=OrigineRegle.BASE,
            commentaire=base.commentaire,
            respect=RESPECT_SANS_EFFET,
        )
        for base in REGLES_DE_BASE
    ]


def regle_horaire_enseignant(enseignant: EnseignantTemoin, fermeture: int) -> ReglePrevue:
    """« M. X commence à 9 h au plus tôt » (le témoin lui interdit le début de matinée)."""
    return ReglePrevue(
        TypeRegle.PLAGE_HORAIRE,
        NaturePortee.ENSEIGNANT,
        enseignant,
        {"debut": "09:00", "fin": f"{fermeture // 60:02d}:{fermeture % 60:02d}", "jours": []},
        Niveau.NECESSAIRE,
        commentaire="Contrainte de service déclarée par l'enseignant.",
    )


def regle_presence_vacataire(enseignant: EnseignantTemoin, jours_presents: int) -> ReglePrevue:
    """« M. X vient au plus N jours par semaine » (vrai par construction : indisponibilités)."""
    return ReglePrevue(
        TypeRegle.JOURNEES_LIBRES,
        NaturePortee.ENSEIGNANT,
        enseignant,
        {"unite": UNITE_JOURNEE, "nombre": 5 - jours_presents, "parmi": []},
        Niveau.IMPORTANT,
        commentaire="Vacataire : activité professionnelle principale le reste de la semaine.",
        respect=RESPECT_CONSTRUCTION,
    )


def normaliser(regles: Iterable[ReglePrevue], parametres: Parametres) -> None:
    """Remplace les paramètres de chaque règle par leurs valeurs canoniques complètes.

    Valide la portée et les paramètres (défauts compris) : une règle fictive
    invalide est une erreur du générateur, signalée tout de suite.
    """
    for regle in regles:
        valider_portee(regle.type, regle.portee)
        regle.parametres = valider_parametres(
            regle.type, regle.parametres, parametres, portee=regle.portee
        )


def plages_interdites_enseignant(regle: ReglePrevue) -> tuple[tuple[int, int, int], ...]:
    """Plages qu'une règle `PLAGE_HORAIRE` ou `CRENEAUX_INTERDITS` d'un enseignant lui interdit."""
    return _plages(regle)


# --------------------------------------------------------------------------- #
# Exigences pour le témoin
# --------------------------------------------------------------------------- #


class ExigencesRegles:
    """Les règles actives d'un semestre, traduites pour le témoin.

    `lignees` : groupe -> lui puis ses ascendants ; `feuilles` : groupe ->
    groupes feuilles de son sous-arbre (lui-même s'il est une feuille) ;
    `lies` : groupe -> lui et les groupes en conflit avec lui.
    """

    def __init__(
        self,
        regles: Iterable[ReglePrevue],
        semestre: str,
        parametres: Parametres,
        lignees: Mapping[int, tuple[int, ...]],
        feuilles: Mapping[int, tuple[int, ...]],
        lies: Mapping[int, frozenset[int]],
    ) -> None:
        self._parametres = parametres
        self._lignees = lignees
        self._feuilles = feuilles
        self._lies = lies
        actives = [r for r in regles if r.vaut_pour(semestre) and r.type in TYPES_POSES]
        self._jours_groupe: dict[int, frozenset[int]] = {}
        self._jours_enseignement: dict[tuple[NaturePortee, int], frozenset[int]] = {}
        self._exigences: list[ReglePrevue] = []
        self._pauses: dict[int, int] = {}
        self._dejeuners_groupe: dict[int, tuple[str, int, int, int]] = {}
        self._maxima_groupe: dict[int, int] = {}
        for regle in actives:
            cible = getattr(regle.cible, "id", None)
            if regle.type is TypeRegle.JOURS_TRAVAILLES:
                jours = frozenset(jour_depuis_texte(j) for j in regle.parametres["jours"])
                if regle.portee is NaturePortee.GROUPE:
                    self._jours_groupe[cible] = jours
                elif regle.portee in (NaturePortee.MATIERE, NaturePortee.TYPE_SEANCE):
                    self._jours_enseignement[(regle.portee, cible)] = jours
                else:
                    raise ErreurDonneesInvalides(
                        f"Portée non prévue par le témoin des jeux fictifs : {regle.portee}."
                    )
            elif regle.type is TypeRegle.PAUSE_MINIMALE:
                self._pauses[cible] = int(regle.parametres["duree"])
            elif regle.type is TypeRegle.PAUSE_DEJEUNER:
                p = regle.parametres
                self._dejeuners_groupe[cible] = (
                    p["mode"],
                    heure_depuis_texte(p["debut"]),
                    heure_depuis_texte(p["fin"]),
                    duree_depuis_texte(p["duree"]),
                )
            elif regle.type is TypeRegle.MAX_PAR_JOUR:
                if regle.parametres.get("unite", UNITE_MINUTES) != UNITE_MINUTES:
                    raise ErreurDonneesInvalides(
                        "Le témoin des jeux fictifs ne pose MAX_PAR_JOUR qu'en minutes."
                    )
                self._maxima_groupe[cible] = int(regle.parametres["maximum"])
            elif regle.portee is not NaturePortee.ENSEIGNANT:
                self._exigences.append(regle)

    # -- réglages par groupe feuille --------------------------------------------

    def _premiere(self, feuille: int, table: Mapping[int, Any]) -> Any:
        for groupe in self._lignees.get(feuille, (feuille,)):
            if groupe in table:
                return table[groupe]
        return None

    def feuilles_de(self, groupes: Iterable[int]) -> tuple[int, ...]:
        """Groupes feuilles dont l'emploi du temps contient une séance de ces groupes."""
        return tuple(sorted({f for g in groupes for f in self._feuilles.get(g, (g,))}))

    def pause(self, groupes: Iterable[int]) -> int | None:
        """Plus grande pause minimale réglée par une règle pour les personnes de la séance."""
        pauses = [self._premiere(f, self._pauses) for f in self.feuilles_de(groupes)]
        pauses = [p for p in pauses if p is not None]
        return max(pauses) if pauses else None

    def dejeuners(self) -> dict[int, tuple[int, int, int]]:
        """Groupe feuille -> fenêtre (début, fin, durée) de sa pause déjeuner libre.

        Une feuille sans règle prend le paramètre global s'il est en mode libre.
        """
        globale = self._parametres.fenetre_dejeuner
        resultat: dict[int, tuple[int, int, int]] = {}
        for feuille in sorted({f for fs in self._feuilles.values() for f in fs}):
            reglage = self._premiere(feuille, self._dejeuners_groupe)
            if reglage is None:
                if globale is not None:
                    resultat[feuille] = globale
            elif reglage[0] == DEJEUNER_LIBRE:
                resultat[feuille] = reglage[1:]
        return resultat

    def maxima(self) -> dict[int, int]:
        """Groupe feuille -> minutes de cours par jour au plus (`MAX_PAR_JOUR`)."""
        resultat: dict[int, int] = {}
        for cible, maximum in self._maxima_groupe.items():
            for feuille in self._feuilles.get(cible, (cible,)):
                resultat[feuille] = min(maximum, resultat.get(feuille, maximum))
        return resultat

    # -- exigences par séance -----------------------------------------------------

    def jours(
        self, groupes: Iterable[int], type_id: int, matiere_id: int, samedi_autorise: bool
    ) -> tuple[int, ...]:
        """Jours permis selon la précédence du contrat (`edt.domaine.regles`).

        Sans règle retenue, le standard : jours travaillés, plus le samedi si le
        type de séance l'autorise et qu'aucune règle ne vise ce type. Sinon,
        l'intersection des règles retenues, qui peut ouvrir un jour (le samedi
        d'A3-APP1).
        """
        groupes = tuple(groupes)
        retenues = [
            jours
            for feuille in self.feuilles_de(groupes)
            if (jours := self._premiere(feuille, self._jours_groupe)) is not None
        ]
        enseignement = self._jours_enseignement.get(
            (NaturePortee.MATIERE, matiere_id)
        ) or self._jours_enseignement.get((NaturePortee.TYPE_SEANCE, type_id))
        if enseignement is not None:
            retenues.append(enseignement)
        type_vise = (NaturePortee.TYPE_SEANCE, type_id) in self._jours_enseignement
        standard = set(self._parametres.jours_travailles)
        if samedi_autorise and not type_vise:
            standard.add(SAMEDI)
        permis = frozenset.intersection(*retenues) if retenues else frozenset(standard)
        return tuple(sorted(permis))

    def _concernee(self, regle: ReglePrevue, groupes: tuple[int, ...], type_id, matiere_id):
        cible = getattr(regle.cible, "id", None)
        if regle.portee is NaturePortee.ETABLISSEMENT:
            return True
        if regle.portee is NaturePortee.GROUPE:
            return not self._lies.get(cible, frozenset({cible})).isdisjoint(groupes)
        if regle.portee is NaturePortee.TYPE_SEANCE:
            return cible == type_id
        if regle.portee is NaturePortee.MATIERE:
            return cible == matiere_id
        raise ErreurDonneesInvalides(
            f"Portée non prévue par le témoin des jeux fictifs : {regle.portee}."
        )

    def interdits(
        self, groupes: Iterable[int], type_id: int, matiere_id: int
    ) -> tuple[tuple[int, int, int], ...]:
        """Plages interdites par `PLAGE_HORAIRE`, `CRENEAUX_INTERDITS` et les déjeuners fixes."""
        groupes = tuple(groupes)
        plages: list[tuple[int, int, int]] = []
        for regle in self._exigences:
            if regle.type in (TypeRegle.PLAGE_HORAIRE, TypeRegle.CRENEAUX_INTERDITS) and (
                self._concernee(regle, groupes, type_id, matiere_id)
            ):
                plages.extend(_plages(regle))
        for feuille in self.feuilles_de(groupes):
            reglage = self._premiere(feuille, self._dejeuners_groupe)
            if reglage is not None and reglage[0] == DEJEUNER_FIXE:
                plages.extend((jour, reglage[1], reglage[2]) for jour in range(7))
        return tuple(sorted(set(plages)))

    def campus(
        self, groupes: Iterable[int], type_id: int, matiere_id: int, campus_ids: Mapping[str, int]
    ) -> tuple[frozenset[int] | None, frozenset[int]]:
        """Campus imposés (`None` = tous) et campus interdits par les règles `CAMPUS`."""
        groupes = tuple(groupes)
        imposes: frozenset[int] | None = None
        interdits: set[int] = set()
        for regle in self._exigences:
            if regle.type is not TypeRegle.CAMPUS or not self._concernee(
                regle, groupes, type_id, matiere_id
            ):
                continue
            ids = frozenset(campus_ids[c] for c in regle.parametres["campus"])
            if regle.parametres["mode"] == MODE_INTERDIRE:
                interdits |= ids
            else:
                imposes = ids if imposes is None else imposes & ids
        return imposes, frozenset(interdits)

    def salles(
        self, groupes: Iterable[int], type_id: int, matiere_id: int, salle_ids: Mapping[str, int]
    ) -> tuple[frozenset[int] | None, frozenset[int]]:
        """Salles imposées (`None` = toutes) et salles interdites par les règles `SALLES`."""
        groupes = tuple(groupes)
        imposees: frozenset[int] | None = None
        interdites: set[int] = set()
        for regle in self._exigences:
            if regle.type is not TypeRegle.SALLES or not self._concernee(
                regle, groupes, type_id, matiere_id
            ):
                continue
            ids = frozenset(salle_ids[c] for c in regle.parametres["salles"])
            if regle.parametres["mode"] == MODE_INTERDIRE:
                interdites |= ids
            else:
                imposees = ids if imposees is None else imposees & ids
        return imposees, frozenset(interdites)


def _plages(regle: ReglePrevue) -> tuple[tuple[int, int, int], ...]:
    """Plages `(jour, début, fin)` qu'une règle d'horaires interdit."""
    p = regle.parametres
    jours = [jour_depuis_texte(j) for j in p.get("jours") or []] or list(range(len(JOURS)))
    debut, fin = heure_depuis_texte(p["debut"]), heure_depuis_texte(p["fin"])
    if regle.type is TypeRegle.CRENEAUX_INTERDITS:
        return tuple((jour, debut, fin) for jour in jours)
    plages = [(jour, 0, debut) for jour in jours if debut > 0]
    plages += [(jour, fin, JOURNEE) for jour in jours if fin < JOURNEE]
    return tuple(plages)


# --------------------------------------------------------------------------- #
# Écriture en base
# --------------------------------------------------------------------------- #


def ecrire_regles(
    session: Session,
    annee: m.AnneeScolaire,
    regles: Sequence[ReglePrevue],
    semestres: Mapping[str, m.Semestre],
    enseignants: Mapping[int, m.Enseignant],
    parametres: Parametres,
) -> list[tuple[m.Regle, ReglePrevue]]:
    """Écrit les règles (codes R001... dans l'ordre), validées par le schéma de leur type.

    Une règle d'enseignant dont l'enseignant n'a finalement aucun cours (il
    n'est pas écrit en base) est omise et journalisée. Les dates de création
    sont fixées (début de l'année scolaire) : le jeu reste déterministe.
    """
    horodatage = dt.datetime.combine(annee.date_debut, dt.time(8, 0))
    colonnes = {
        NaturePortee.GROUPE: "groupe",
        NaturePortee.TYPE_SEANCE: "type_seance",
        NaturePortee.MATIERE: "matiere",
        NaturePortee.CAMPUS: "campus",
        NaturePortee.ENSEIGNANT: "enseignant",
    }
    ecrites: list[tuple[m.Regle, ReglePrevue]] = []
    for regle in regles:
        cible = regle.cible
        if regle.portee is NaturePortee.ENSEIGNANT:
            cible = enseignants.get(regle.cible.cle)
            if cible is None:
                journal.info(
                    "Règle %s omise : l'enseignant visé n'a finalement aucun cours",
                    regle.type.value,
                )
                continue
        ligne = m.Regle(
            annee_scolaire_id=annee.id,
            code=f"R{len(ecrites) + 1:03d}",
            type=regle.type,
            portee=regle.portee,
            parametres=valider_parametres(
                regle.type, regle.parametres, parametres, portee=regle.portee
            ),
            niveau=regle.niveau,
            etat=regle.etat,
            origine=regle.origine,
            semestre_id=semestres[regle.semestre].id if regle.semestre else None,
            commentaire=regle.commentaire,
            cree_le=horodatage,
            modifie_le=horodatage,
        )
        if regle.portee in colonnes:
            setattr(ligne, colonnes[regle.portee], cible)
        session.add(ligne)
        ecrites.append((ligne, regle))
    session.flush()
    journal.info(
        "Règles personnalisées écrites : %d (%d actives)",
        len(ecrites),
        sum(1 for _, r in ecrites if r.active),
    )
    return ecrites
