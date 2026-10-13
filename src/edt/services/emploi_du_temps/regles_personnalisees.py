"""Règles personnalisées vues des écrans de l'emploi du temps : Générer, suivi, grille, anomalies.

Le contrat est livré (`edt.domaine.regles`, `edt.services.regles`,
`Instantane.regles`, `emplois`, `reglages`, `jours_travailles`). Ce module en
tire ce que les écrans affichent :

- **page Générer** : le tableau « Règles personnalisées » (règles actives de
  l'année qui concernent le semestre), trois niveaux par règle et une case
  « ignorer pour ce calcul », préremplis avec le niveau du dernier calcul,
  sinon celui de la règle (`bloc_regles`, `lire_niveaux_regles`) ;
- **suivi et historique** : les règles appliquées qui s'écartent de leur
  niveau ou ont été ignorées, les entorses par règle ;
- **grille** : la pause déjeuner de la personne affichée
  (`dejeuner_de_la_cible`) et, pour une retouche, les jours permis d'une
  séance (`jours_permis`), lus dans `Instantane.reglages`.

Raccords avec les lots en cours, lus défensivement (rien ne casse tant
qu'ils manquent ; l'écran dit ce qui n'est pas encore pris en compte) :

- solveur : `OptionsSolveur.niveaux_regles` (id de règle → niveau ; une règle
  absente est ignorée), la copie des règles appliquées dans
  `calcul.parametres["regles"]` (ou `SuiviCalcul.regles`) : liste de
  `{id, code, type, portee, parametres, niveau, phrase}`, et le décompte
  `statistiques["entorses_regles"]` (code → nombre) ;
- vérificateur : `Anomalie.regle_id`, `regle_code`, `regle_phrase` (lus par
  `presentation.regle_citee`).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.instantane import Instantane, id_seance
from edt.domaine.parametres import Parametres
from edt.domaine.regles import (
    EtatRegle,
    NaturePersonne,
    NaturePortee,
    Personne,
    ReglageDejeuner,
    Source,
    TypeRegle,
)
from edt.erreurs import ErreurDonneesInvalides, ErreurEDT
from edt.services import regles as service_regles
from edt.services.emploi_du_temps import memoire, presentation
from edt.services.instantane import charger_instantane
from edt.solveur.contrat import OptionsSolveur

journal = logging.getLogger(__name__)

PREFIXE_NIVEAU_REGLE = "niveau_regle_"
"""Champ du niveau d'une règle personnalisée pour un calcul : `niveau_regle_12=important`."""

PREFIXE_IGNORER_REGLE = "ignorer_regle_"
"""Case « ignorer pour ce calcul » d'une règle : `ignorer_regle_12=1`."""

SEUIL_REPLI = 20
"""Au-delà de ce nombre de règles, le tableau de la page Générer est regroupé par type, replié."""

ADRESSE_REGLES = presentation.ADRESSE_REGLES
ADRESSE_AJOUT_REGLE = presentation.ADRESSE_AJOUT_REGLE


# --------------------------------------------------------------------------- #
# Instantané mémorisé
# --------------------------------------------------------------------------- #


def instantane_du_semestre(session: Session, semestre_id: int) -> Instantane:
    """L'instantané du semestre (règles résolues et réglages), mémorisé tant que la base ne change pas.

    Lève les erreurs de `charger_instantane` (règle active devenue invalide,
    réglages contradictoires : elles nomment la règle).
    """
    return memoire.memoriser(
        session, "instantane", (semestre_id,), lambda: charger_instantane(session, semestre_id)
    )


def _regles_actives_du_type(session: Session, annee_id: int, type_regle: TypeRegle) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(m.Regle)
            .where(
                m.Regle.annee_scolaire_id == annee_id,
                m.Regle.etat == EtatRegle.ACTIVE,
                m.Regle.type == type_regle,
            )
        )
        or 0
    )


# --------------------------------------------------------------------------- #
# Grille : déjeuner de la personne affichée, jours permis d'une séance
# --------------------------------------------------------------------------- #


def dejeuner_standard(parametres: Parametres) -> ReglageDejeuner:
    """La pause déjeuner de l'établissement (paramètres globaux)."""
    return ReglageDejeuner(
        mode=parametres.dejeuner_mode,
        debut=parametres.dejeuner_debut_min,
        fin=parametres.dejeuner_fin_min,
        duree=parametres.dejeuner_duree_min,
        source=Source(contrainte=CodeContrainte.DEJEUNER),
    )


def dejeuner_de_la_cible(
    session: Session,
    semestre_id: int,
    annee_id: int,
    parametres: Parametres,
    nature: str | None,
    cible_id: int | None,
) -> tuple[ReglageDejeuner, str | None]:
    """Pause déjeuner de la personne affichée, et une alerte si les règles n'ont pas pu être lues.

    - groupe : son propre réglage, le long de sa lignée (une règle sur lui ou
      un ascendant ; pour un groupe qui n'est pas une feuille, le réglage
      d'une de ses feuilles qui n'a pas de règle plus précise que lui) ;
    - enseignant : le sien ;
    - salle, ou personne sans emploi du temps : celui de l'établissement.

    Sans règle `PAUSE_DEJEUNER` active dans l'année, l'établissement, sans
    charger l'instantané. Si les règles ne se lisent pas, l'établissement et
    une alerte qui dit pourquoi (jamais d'erreur sur la grille).
    """
    standard = dejeuner_standard(parametres)
    if nature not in ("groupe", "enseignant") or cible_id is None:
        return standard, None
    if not _regles_actives_du_type(session, annee_id, TypeRegle.PAUSE_DEJEUNER):
        return standard, None
    try:
        instantane = instantane_du_semestre(session, semestre_id)
        reglages = instantane.reglages.dejeuner
    except ErreurEDT as erreur:
        journal.warning("Déjeuner par personne illisible (semestre %d) : %s", semestre_id, erreur)
        return standard, (
            f"Les règles personnalisées n'ont pas pu être lues ({erreur.message}) : la bande du "
            "déjeuner est celle de l'établissement."
        )
    if nature == "enseignant":
        return reglages.get(Personne(NaturePersonne.ENSEIGNANT, cible_id), standard), None
    for emploi in instantane.emplois:
        personne = emploi.personne
        if personne.nature is not NaturePersonne.GROUPE or cible_id not in emploi.lignee:
            continue
        lignee_du_groupe = emploi.lignee[emploi.lignee.index(cible_id) :]
        reglage = reglages.get(personne)
        if reglage is None:
            continue
        regle = reglage.source.regle
        if regle is None or (
            regle.portee is NaturePortee.GROUPE and regle.cible_id in lignee_du_groupe
        ):
            return reglage, None
    # Toutes les feuilles ont une règle plus précise que le groupe : chacune la sienne,
    # le groupe affiche celle de l'établissement (cas rare, documenté).
    return standard, None


def jours_permis(
    session: Session,
    semestre_id: int,
    besoin_id: int,
    rang: int,
    campus_id: int | None,
) -> frozenset[int] | None:
    """Jours permis d'une séance de la semaine type (`Instantane.reglages`), ou `None`.

    `None` quand la séance n'est pas dans l'instantané (besoin modifié depuis
    la version) ou que les règles ne se lisent pas (journalisé) : l'appelant
    se rabat alors sur les jours travaillés de l'établissement.
    """
    try:
        instantane = instantane_du_semestre(session, semestre_id)
        regles_jours = instantane.reglages.jours.get(id_seance(besoin_id, rang))
    except ErreurEDT as erreur:
        journal.warning("Jours permis illisibles (semestre %d) : %s", semestre_id, erreur)
        return None
    if regles_jours is None:
        journal.info(
            "Séance %s absente de l'instantané du semestre %d : jours de l'établissement",
            id_seance(besoin_id, rang),
            semestre_id,
        )
        return None
    return regles_jours.jours_permis(campus_id)


# --------------------------------------------------------------------------- #
# Page Générer
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegleDuCalcul:
    """Une ligne du tableau « Règles personnalisées » de la page Générer.

    `niveau` : niveau proposé pour ce calcul (dernier calcul, saisie réaffichée
    ou celui de la règle) ; `niveau_regle` : celui enregistré avec la règle ;
    `ignoree` : case « ignorer pour ce calcul » cochée.
    """

    id: int
    code: str
    phrase: str
    type_libelle: str
    portee: str
    periode: str
    niveau: Niveau
    niveau_regle: Niveau
    ignoree: bool = False

    @property
    def champ_niveau(self) -> str:
        """Nom du champ des trois boutons radio."""
        return f"{PREFIXE_NIVEAU_REGLE}{self.id}"

    @property
    def champ_ignorer(self) -> str:
        """Nom de la case « ignorer pour ce calcul »."""
        return f"{PREFIXE_IGNORER_REGLE}{self.id}"

    @property
    def lien(self) -> str:
        """Adresse de la règle sur l'écran Données › Règles personnalisées."""
        return presentation.adresse_regle(self.code)


@dataclass(frozen=True)
class BlocRegles:
    """Le bloc « Règles personnalisées » de la page Générer.

    `erreur` : les règles n'ont pas pu être lues (le message dit laquelle).
    """

    regles: tuple[RegleDuCalcul, ...] = ()
    nb_proposees: int = 0
    erreur: str | None = None

    @property
    def replie(self) -> bool:
        """Vrai au-delà de `SEUIL_REPLI` règles : regroupées par type, repliées."""
        return len(self.regles) > SEUIL_REPLI

    @property
    def par_type(self) -> tuple[tuple[str, tuple[RegleDuCalcul, ...]], ...]:
        """Les règles regroupées par libellé de type, dans l'ordre d'apparition."""
        groupes: dict[str, list[RegleDuCalcul]] = {}
        for regle in self.regles:
            groupes.setdefault(regle.type_libelle, []).append(regle)
        return tuple((libelle, tuple(liste)) for libelle, liste in groupes.items())


class ErreurSaisieRegles(ErreurDonneesInvalides):
    """Choix illisible sur une règle personnalisée : rien n'est lancé.

    `choix` : les choix à réafficher (id → niveau, `None` pour une règle ignorée).
    """

    def __init__(self, message: str, choix: dict[int, Niveau | None]) -> None:
        super().__init__(f"Le calcul n'a pas été lancé : {message}")
        self.choix = choix


def _portee_texte(detail: service_regles.RegleDetail) -> str:
    """« La classe A3-APP1 », « M. Petit », « L'établissement » (désignation de la portée)."""
    designation = detail.portee.designation
    return designation[:1].upper() + designation[1:]


def _concerne_le_semestre(detail: service_regles.RegleDetail, semestre) -> bool:
    regle = detail.regle
    if regle.semestre_id is not None and regle.semestre_id != semestre.id:
        return False
    if regle.date_debut is not None and regle.date_debut > semestre.date_fin:
        return False
    return regle.date_fin is None or regle.date_fin >= semestre.date_debut


def regles_actives_du_semestre(
    session: Session, semestre, annee_id: int
) -> list[service_regles.RegleDetail]:
    """Règles actives de l'année dont la période recoupe le semestre, par code."""
    return [
        detail
        for detail in service_regles.lister(session, annee_id, etat=EtatRegle.ACTIVE)
        if _concerne_le_semestre(detail, semestre)
    ]


def bloc_regles(
    session: Session,
    semestre,
    annee_id: int,
    *,
    choix: Mapping[int, Niveau | None] | None = None,
    precedents: Mapping[int, Niveau] | None = None,
) -> BlocRegles:
    """Le tableau des règles personnalisées du semestre, prérempli.

    `choix` : saisie à réafficher (id → niveau, `None` : ignorée) ; sinon
    `precedents` : niveaux du dernier calcul (une règle qu'il n'a pas citée
    reprend son propre niveau : la case « ignorer » ne vaut que pour un
    calcul) ; sinon le niveau de la règle.
    """
    try:
        details = regles_actives_du_semestre(session, semestre, annee_id)
        proposees = len(service_regles.lister(session, annee_id, etat=EtatRegle.PROPOSEE))
    except ErreurEDT as erreur:
        journal.warning("Règles personnalisées illisibles (année %d) : %s", annee_id, erreur)
        return BlocRegles(erreur=erreur.message)
    lignes = []
    for detail in details:
        niveau, ignoree = detail.niveau, False
        if choix is not None and detail.id in choix:
            ignoree = choix[detail.id] is None
            niveau = choix[detail.id] or detail.niveau
        elif precedents is not None and detail.id in precedents:
            niveau = precedents[detail.id]
        lignes.append(
            RegleDuCalcul(
                id=detail.id,
                code=detail.code,
                phrase=detail.phrase,
                type_libelle=detail.type.libelle,
                portee=_portee_texte(detail),
                periode=detail.regle.periode_texte(),
                niveau=niveau,
                niveau_regle=detail.niveau,
                ignoree=ignoree,
            )
        )
    return BlocRegles(regles=tuple(lignes), nb_proposees=proposees)


def champs_regles(champs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Les champs des règles personnalisées d'un formulaire (niveaux et cases « ignorer »)."""
    return {
        nom: str(valeur).strip()
        for nom, valeur in champs
        if nom.startswith((PREFIXE_NIVEAU_REGLE, PREFIXE_IGNORER_REGLE))
    }


def lire_choix_regles(
    champs: Mapping[str, str], regles: Sequence[RegleDuCalcul]
) -> dict[int, Niveau | None]:
    """Choix reçus pour chaque règle du tableau : son niveau, ou `None` si elle est ignorée.

    Une règle sans champ garde son niveau. Lève `ErreurSaisieRegles` (avec
    les choix lisibles, à réafficher) pour un niveau inconnu ou un champ qui
    vise une règle qui n'est pas (ou plus) active pour le semestre.
    """
    connues = {regle.id for regle in regles}
    erreurs: list[str] = []
    for nom in champs:
        brut = nom.removeprefix(PREFIXE_NIVEAU_REGLE).removeprefix(PREFIXE_IGNORER_REGLE)
        if not brut.isdigit() or int(brut) not in connues:
            erreurs.append(
                f"la règle « {brut} » n'est pas (ou plus) active pour ce semestre : recharger "
                "la page."
            )
    choix: dict[int, Niveau | None] = {}
    for regle in regles:
        if champs.get(regle.champ_ignorer):
            choix[regle.id] = None
            continue
        valeur = champs.get(regle.champ_niveau)
        if valeur is None:
            choix[regle.id] = regle.niveau_regle
            continue
        try:
            choix[regle.id] = Niveau(valeur)
        except ValueError:
            erreurs.append(f"niveau inconnu pour la règle {regle.code} (« {valeur} »).")
            choix[regle.id] = regle.niveau
    if erreurs:
        journal.info("Niveaux des règles personnalisées refusés : %s", " ".join(erreurs))
        raise ErreurSaisieRegles(" ".join(dict.fromkeys(erreurs)), choix)
    return choix


def niveaux_pour_le_calcul(choix: Mapping[int, Niveau | None]) -> dict[int, Niveau]:
    """Les niveaux transmis au moteur : les règles ignorées en sont absentes (convention)."""
    return {identifiant: niveau for identifiant, niveau in choix.items() if niveau is not None}


def choix_modifies(choix: Mapping[int, Niveau | None], regles: Sequence[RegleDuCalcul]) -> bool:
    """Vrai si une règle est ignorée ou prise à un autre niveau que le sien."""
    return any(
        choix.get(regle.id, regle.niveau_regle) is not regle.niveau_regle for regle in regles
    )


# --------------------------------------------------------------------------- #
# Options du calcul
# --------------------------------------------------------------------------- #


def moteur_lit_les_niveaux_regles() -> bool:
    """Vrai si `OptionsSolveur` porte `niveaux_regles` (lot solveur livré)."""
    return "niveaux_regles" in {champ.name for champ in dataclasses.fields(OptionsSolveur)}


def avec_niveaux_regles(
    options: OptionsSolveur, niveaux_regles: Mapping[int, Niveau] | None
) -> OptionsSolveur:
    """Les options avec les niveaux des règles personnalisées, si le moteur sait les lire.

    `None` : rien à transmettre (le moteur applique chaque règle à son niveau).
    Sans `OptionsSolveur.niveaux_regles`, les options sont rendues telles
    quelles et l'absence est journalisée ; l'écran le dit
    (`moteur_lit_les_niveaux_regles`).
    """
    if niveaux_regles is None:
        return options
    if not moteur_lit_les_niveaux_regles():
        journal.warning(
            "Niveaux des règles personnalisées non transmis : le moteur ne les lit pas encore "
            "(OptionsSolveur.niveaux_regles absent)"
        )
        return options
    return dataclasses.replace(options, niveaux_regles=dict(niveaux_regles))


# --------------------------------------------------------------------------- #
# Ce que le calcul a enregistré
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegleAppliquee:
    """Une règle telle que le calcul l'a appliquée (copie de `calcul.parametres["regles"]`)."""

    id: int
    code: str
    phrase: str
    niveau: Niveau


def regles_enregistrees(
    suivi, parametres: Mapping | None = None
) -> tuple[RegleAppliquee, ...] | None:
    """Les règles appliquées par un calcul, ou `None` s'il n'en a rien enregistré.

    Lues dans `SuiviCalcul.regles` sinon `parametres["regles"]` ; une entrée
    illisible est journalisée et laissée de côté.
    """
    brut = getattr(suivi, "regles", None)
    if brut is None and parametres is not None:
        brut = parametres.get("regles")
    if brut is None:
        return None
    if not isinstance(brut, list | tuple):
        journal.warning("Calcul %s : règles enregistrées illisibles (%r)", suivi.id, brut)
        return None
    lues = []
    for entree in brut:
        try:
            lues.append(
                RegleAppliquee(
                    id=int(entree["id"]),
                    code=str(entree.get("code") or f"n° {entree['id']}"),
                    phrase=str(entree.get("phrase") or ""),
                    niveau=Niveau(entree["niveau"]),
                )
            )
        except (KeyError, TypeError, ValueError, AttributeError) as erreur:
            journal.warning(
                "Calcul %s : règle enregistrée illisible (%r) : %s", suivi.id, entree, erreur
            )
    return tuple(lues)


def niveaux_regles_du_calcul(suivi, parametres: Mapping | None = None) -> dict[int, Niveau] | None:
    """Niveaux à reprendre pour relancer un calcul (absente = ignorée), `None` s'il n'y en a pas."""
    appliquees = regles_enregistrees(suivi, parametres)
    if appliquees is None:
        return None
    return {regle.id: regle.niveau for regle in appliquees}


def libelles_regles_du_calcul(
    session: Session,
    suivi,
    parametres: Mapping | None,
    semestre,
    annee_id: int,
) -> tuple[str, ...]:
    """Les règles qui s'écartent de leur niveau ou ont été ignorées par ce calcul, en phrases.

    « R002 (La classe A1-G1 a une pause déjeuner libre…) : facultatif » ;
    « R001 (…) : ignorée pour ce calcul » pour une règle active du semestre,
    déjà créée au lancement, que le calcul n'a pas appliquée. Rien si le
    calcul n'a pas enregistré ses règles.
    """
    appliquees = regles_enregistrees(suivi, parametres)
    if appliquees is None:
        return ()
    try:
        actuelles = {d.id: d for d in regles_actives_du_semestre(session, semestre, annee_id)}
    except ErreurEDT as erreur:
        journal.warning("Règles actuelles illisibles pour le calcul %s : %s", suivi.id, erreur)
        actuelles = {}
    phrases = []
    for regle in appliquees:
        actuelle = actuelles.get(regle.id)
        if actuelle is not None and actuelle.niveau is not regle.niveau:
            phrases.append(
                f"{_citation(regle.code, regle.phrase)} : {regle.niveau.libelle.lower()}"
            )
    appliquees_ids = {regle.id for regle in appliquees}
    creees = dict(
        session.execute(
            select(m.Regle.id, m.Regle.cree_le).where(m.Regle.id.in_(list(actuelles)))
        ).all()
    )
    for identifiant, detail in actuelles.items():
        cree_le = creees.get(identifiant)
        if identifiant in appliquees_ids or cree_le is None or cree_le > suivi.demarre_le:
            continue
        phrases.append(f"{_citation(detail.code, detail.phrase)} : ignorée pour ce calcul")
    return tuple(phrases)


def _citation(code: str, phrase: str) -> str:
    return f"{code} ({phrase.rstrip('.')})" if phrase else code


def libelles_entorses_regles(
    session: Session,
    suivi,
    statistiques: Mapping,
    parametres: Mapping | None,
    annee_id: int,
) -> tuple[str, ...]:
    """Entorses par règle : « 2 entorses à R007 (Le groupe A1 ne travaille pas le samedi) ».

    D'après `statistiques["entorses_regles"]` (code → nombre) ; la phrase vient
    de la copie enregistrée avec le calcul, sinon de la règle actuelle. Un
    décompte illisible est dit, pas tu.
    """
    brut = statistiques.get("entorses_regles")
    if not brut:
        return ()
    try:
        entorses = {str(code): int(nombre) for code, nombre in dict(brut).items()}
    except (TypeError, ValueError) as erreur:
        journal.warning(
            "Calcul %s : entorses par règle illisibles (%r) : %s", suivi.id, brut, erreur
        )
        return (
            "Le décompte des entorses par règle est illisible : le journal du calcul le détaille.",
        )
    phrases_par_code = {r.code: r.phrase for r in regles_enregistrees(suivi, parametres) or ()}
    manquants = [code for code in entorses if code not in phrases_par_code]
    if manquants:
        try:
            for detail in service_regles.lister(session, annee_id):
                phrases_par_code.setdefault(detail.code, detail.phrase)
        except ErreurEDT as erreur:
            journal.warning("Phrases des règles illisibles : %s", erreur)
    lignes = []
    for code in sorted(entorses, key=_cle_code):
        nombre = entorses[code]
        if nombre <= 0:
            continue
        mot = "entorse" if nombre == 1 else "entorses"
        lignes.append(f"{nombre} {mot} à {_citation(code, phrases_par_code.get(code, ''))}")
    return tuple(lignes)


def _cle_code(code: str) -> tuple[int, str]:
    chiffres = code[1:] if code[:1] == "R" else ""
    return (int(chiffres), code) if chiffres.isdigit() else (10**9, code)


def resume_regles_du_calcul(calcul: m.Calcul) -> tuple[str, ...]:
    """Pour l'historique : les règles appliquées hors de leur niveau, en bref (« R002 : facultatif »).

    D'après `calcul.parametres["regles"]` comparé aux niveaux actuels des
    règles ; rien si le calcul ne les a pas enregistrées.
    """
    appliquees = regles_enregistrees(calcul, dict(calcul.parametres or {}))
    if not appliquees:
        return ()
    session = Session.object_session(calcul)
    if session is None:
        return ()
    actuels = dict(
        session.execute(
            select(m.Regle.id, m.Regle.niveau).where(m.Regle.id.in_([r.id for r in appliquees]))
        ).all()
    )
    return tuple(
        f"{regle.code} : {regle.niveau.libelle.lower()}"
        for regle in appliquees
        if regle.id in actuels and actuels[regle.id] is not regle.niveau
    )
