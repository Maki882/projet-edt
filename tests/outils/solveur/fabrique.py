"""Fabrique d'instantanés écrits à la main, sans base de données.

    f = Fabrique()
    classe = f.groupe("A1")
    prof = f.enseignant("Mme Martin")
    f.salle("N101")
    f.besoin([classe], [prof], nombre=2)
    instantane = f.instantane()

Chaque méthode crée un élément et renvoie son identifiant. Les valeurs par
défaut décrivent un établissement minimal : un campus, la grille standard
(quatre créneaux de deux heures du lundi au vendredi, deux le samedi matin),
un semestre de deux semaines. `instantane()` déplie les besoins avec la
vraie fonction de dépliage du domaine.

`verifier_solution` et `verifier_dates` sont les points de passage vers
l'oracle : le vérificateur officiel (`edt.verification`) ne doit relever
aucune erreur sur ce que produit le solveur.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable, Sequence

from edt.domaine.calendrier import semaines_entre
from edt.domaine.contraintes import CATALOGUE, Niveau, codes_actifs
from edt.domaine.depliage import deplier
from edt.domaine.enumerations import (
    Accessibilite,
    CibleIndisponibilite,
    DemiJournee,
    NatureGroupe,
    NatureJourFerme,
    StatutEnseignant,
)
from edt.domaine.instantane import (
    Besoin,
    Campus,
    Creneau,
    Enseignant,
    Groupe,
    Indisponibilite,
    Instantane,
    JourFerme,
    Occurrence,
    Placement,
    Salle,
    Trajet,
)
from edt.domaine.parametres import Parametres
from edt.domaine.regles import (
    EtatRegle,
    NaturePortee,
    Portee,
    Regle,
    TypeRegle,
    resoudre_regle,
    valider_parametres,
)
from edt.solveur import OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.personnalisees import POSEURS
from edt.verification import verifier, verifier_occurrences

H = 60
LUNDI, MARDI, MERCREDI, JEUDI, VENDREDI, SAMEDI = range(6)
CRENEAUX_JOUR = (
    (8 * H + 30, 10 * H + 30, DemiJournee.MATIN),
    (10 * H + 45, 12 * H + 45, DemiJournee.MATIN),
    (13 * H + 45, 15 * H + 45, DemiJournee.APRES_MIDI),
    (16 * H, 18 * H, DemiJournee.APRES_MIDI),
)
C1, C2, C3, C4 = (creneau[0] for creneau in CRENEAUX_JOUR)
"""Débuts des quatre créneaux standard : 8 h 30, 10 h 45, 13 h 45 et 16 h."""

RENTREE = dt.date(2026, 9, 7)
"""Un lundi : premier jour du semestre des instantanés fabriqués."""

TYPES_SEANCE = {"TD": 1, "CM": 2, "TP": 3}
"""Types de séance des besoins fabriqués : code → identifiant."""


def grille(jours: Iterable[int] = range(5), samedi: bool = True) -> tuple[Creneau, ...]:
    """Grille standard sur `jours`, plus deux créneaux le samedi matin si `samedi`."""
    creneaux = [Creneau(jour, d, f, demi) for jour in jours for d, f, demi in CRENEAUX_JOUR]
    if samedi:
        creneaux.extend(Creneau(SAMEDI, d, f, demi) for d, f, demi in CRENEAUX_JOUR[:2])
    return tuple(creneaux)


class Fabrique:
    """Construit un `Instantane` pièce par pièce (voir l'en-tête du module)."""

    def __init__(
        self,
        nb_semaines: int = 2,
        creneaux: Sequence[Creneau] | None = None,
        **parametres: int | bool,
    ) -> None:
        self.date_debut = RENTREE
        self.date_fin = RENTREE + dt.timedelta(days=7 * nb_semaines - 3)
        self.creneaux = tuple(creneaux) if creneaux is not None else grille()
        self.parametres = Parametres(**parametres)
        self._campus: list[Campus] = []
        self._trajets: list[Trajet] = []
        self._salles: list[Salle] = []
        self._enseignants: list[Enseignant] = []
        self._groupes: list[Groupe] = []
        self._conflits: set[tuple[int, int]] = set()
        self._indisponibilites: list[Indisponibilite] = []
        self._jours_fermes: list[JourFerme] = []
        self._besoins: list[Besoin] = []
        self._verrous: dict[str, Placement] = {}
        self._indices: dict[str, Placement] = {}
        self._regles: list[Regle] = []

    # -- référentiels -----------------------------------------------------------

    def campus(self, code: str | None = None) -> int:
        """Ajoute un campus."""
        identifiant = len(self._campus) + 1
        code = code or f"C{identifiant}"
        self._campus.append(Campus(identifiant, code, f"Campus {code}"))
        return identifiant

    def trajet(self, campus_a: int, campus_b: int, duree: int) -> None:
        """Fixe le temps de trajet entre deux campus."""
        self._trajets.append(Trajet(campus_a, campus_b, duree))

    def salle(
        self,
        code: str | None = None,
        capacite: int = 40,
        campus: int | None = None,
        type_salle: int = 1,
        equipements: Iterable[int] = (),
        accessibilite: Accessibilite = Accessibilite.OUI,
    ) -> int:
        """Ajoute une salle (sur le premier campus par défaut, créé au besoin)."""
        if campus is None:
            campus = self._campus[0].id if self._campus else self.campus()
        identifiant = len(self._salles) + 1
        self._salles.append(
            Salle(
                identifiant,
                code or f"S{identifiant}",
                campus,
                capacite,
                type_salle,
                frozenset(equipements),
                accessibilite,
            )
        )
        return identifiant

    def enseignant(
        self,
        nom: str | None = None,
        max_jour: int | None = None,
        max_semaine: int | None = None,
        fictif: bool = False,
    ) -> int:
        """Ajoute un enseignant."""
        identifiant = len(self._enseignants) + 1
        self._enseignants.append(
            Enseignant(
                identifiant,
                f"ens-{identifiant}",
                nom or f"Enseignant {identifiant}",
                StatutEnseignant.PERMANENT,
                max_jour,
                max_semaine,
                fictif,
            )
        )
        return identifiant

    def groupe(
        self,
        code: str | None = None,
        effectif: int = 30,
        parent: int | None = None,
        nature: NatureGroupe = NatureGroupe.CLASSE,
        accessibilite: bool = False,
    ) -> int:
        """Ajoute un groupe ; il est mis en conflit avec tous les groupes qui le contiennent."""
        identifiant = len(self._groupes) + 1
        self._groupes.append(
            Groupe(
                identifiant,
                code or f"G{identifiant}",
                code or f"Groupe {identifiant}",
                nature,
                parent,
                effectif,
                accessibilite,
            )
        )
        ancetre = parent
        while ancetre is not None:
            self.conflit(ancetre, identifiant)
            ancetre = self._groupes[ancetre - 1].parent_id
        return identifiant

    def conflit(self, groupe_a: int, groupe_b: int) -> None:
        """Déclare que deux groupes ne peuvent pas avoir cours en même temps."""
        self._conflits.add((min(groupe_a, groupe_b), max(groupe_a, groupe_b)))

    def indisponibilite(
        self,
        cible: CibleIndisponibilite,
        ressource: int,
        jour: int | None = None,
        debut: int = 0,
        fin: int = 24 * H,
        date_debut: dt.date | None = None,
        date_fin: dt.date | None = None,
        dure: bool = True,
    ) -> None:
        """Ajoute une indisponibilité récurrente (`jour`) ou datée (`date_debut`, `date_fin`)."""
        self._indisponibilites.append(
            Indisponibilite(
                len(self._indisponibilites) + 1,
                cible,
                ressource,
                jour,
                date_debut,
                date_fin if date_fin is not None else date_debut,
                debut,
                fin,
                "test",
                dure,
            )
        )

    def jour_ferme(
        self,
        date_debut: dt.date,
        date_fin: dt.date | None = None,
        nature: NatureJourFerme = NatureJourFerme.FERIE,
        groupes: Iterable[int] | None = None,
    ) -> None:
        """Ferme une période du calendrier (pour tous, ou pour `groupes`)."""
        self._jours_fermes.append(
            JourFerme(
                date_debut,
                date_fin or date_debut,
                "Fermeture de test",
                nature,
                None if groupes is None else frozenset(groupes),
            )
        )

    # -- demande ----------------------------------------------------------------

    def besoin(
        self,
        groupes: Iterable[int],
        enseignants: Iterable[int] = (),
        duree: int = 2 * H,
        nombre: int = 1,
        libelle: str | None = None,
        samedi_autorise: bool = False,
        type_seance: str = "TD",
        **champs,
    ) -> int:
        """Ajoute un besoin ; `champs` passe tels quels les autres champs de `Besoin`.

        `type_seance` : « TD » (1), « CM » (2) ou « TP » (3), comme
        `TYPES_SEANCE` ; `matiere_id` et `matiere_code` se donnent dans `champs`
        (défaut : un par besoin).

        L'effectif est la somme des effectifs des groupes ; l'accessibilité
        est requise si un groupe du besoin, ou un groupe en conflit avec l'un
        d'eux, porte le drapeau.
        """
        identifiant = len(self._besoins) + 1
        groupes = frozenset(groupes)
        matiere_id = champs.pop("matiere_id", identifiant)
        self._besoins.append(
            Besoin(
                id=identifiant,
                libelle=libelle or f"Cours {identifiant}",
                matiere_id=matiere_id,
                matiere_code=champs.pop("matiere_code", f"M{matiere_id}"),
                type_seance_id=TYPES_SEANCE[type_seance],
                type_seance_code=type_seance,
                samedi_autorise=samedi_autorise,
                duree=duree,
                groupes=groupes,
                enseignants=frozenset(enseignants),
                effectif=champs.pop(
                    "effectif", sum(self._groupes[g - 1].effectif for g in groupes)
                ),
                accessibilite_requise=False,  # résolu dans `instantane()`
                nombre=nombre,
                **champs,
            )
        )
        return identifiant

    def verrou(
        self,
        seance_id: str,
        jour: int,
        debut: int,
        salle: int | None = None,
        fin: int | None = None,
    ) -> None:
        """Verrouille une séance ; la fin se déduit de la durée du besoin, sauf `fin` donnée.

        Rien n'est validé : un verrou incohérent (jour 7, salle inconnue, autre
        durée que le besoin) se fabrique ainsi, pour les tests des cas limites.
        """
        self._verrous[seance_id] = Placement(
            seance_id, jour, debut, -1 if fin is None else fin, salle
        )

    def indice(self, seance_id: str, jour: int, debut: int, salle: int | None = None) -> None:
        """Donne le placement précédent d'une séance."""
        self._indices[seance_id] = Placement(seance_id, jour, debut, -1, salle)

    # -- règles personnalisées --------------------------------------------------

    def portee(self, nature: NaturePortee | str, cible: int | None = None) -> Portee:
        """Portée d'une règle, avec le code et le libellé de la cible (pour la phrase)."""
        nature = NaturePortee(nature)
        if nature is NaturePortee.ETABLISSEMENT:
            return Portee(nature)
        assert cible is not None
        if nature is NaturePortee.GROUPE:
            groupe = self._groupes[cible - 1]
            return Portee(nature, cible, groupe.code, groupe.libelle, groupe.nature)
        if nature is NaturePortee.ENSEIGNANT:
            enseignant = self._enseignants[cible - 1]
            return Portee(nature, cible, enseignant.identifiant, enseignant.nom)
        if nature is NaturePortee.CAMPUS:
            campus = self._campus[cible - 1]
            return Portee(nature, cible, campus.code, campus.nom)
        if nature is NaturePortee.TYPE_SEANCE:
            code = next(code for code, numero in TYPES_SEANCE.items() if numero == cible)
            return Portee(nature, cible, code, code)
        if nature is NaturePortee.MATIERE:
            besoin = next(b for b in self._besoins if b.matiere_id == cible)
            return Portee(nature, cible, besoin.matiere_code, besoin.matiere_code)
        besoin = self._besoins[cible - 1]
        return Portee(nature, cible, str(cible), besoin.libelle)

    def regle(
        self,
        type_regle: TypeRegle | str,
        nature: NaturePortee | str,
        cible: int | None = None,
        niveau: Niveau | str | None = None,
        etat: EtatRegle = EtatRegle.ACTIVE,
        **parametres,
    ) -> int:
        """Ajoute une règle personnalisée active et renvoie son identifiant.

        Les paramètres sont donnés sous leur forme lisible (jours en noms,
        heures « HH:MM », durées en minutes, codes des campus ou des salles)
        et validés par le catalogue ; `niveau` : celui du type par défaut.
        Les champs de période (`semestre_id`, `date_debut`, `date_fin`) se
        passent aussi en paramètres nommés.
        """
        type_regle = TypeRegle(type_regle)
        periode = {
            cle: parametres.pop(cle)
            for cle in ("semestre_id", "date_debut", "date_fin", "commentaire")
            if cle in parametres
        }
        portee = self.portee(nature, cible)
        identifiant = len(self._regles) + 1
        self._regles.append(
            Regle(
                type_regle,
                portee,
                valider_parametres(type_regle, parametres, self.parametres, portee=portee.nature),
                Niveau(niveau) if niveau else definition_niveau(type_regle),
                etat,
                id=identifiant,
                code=f"R{identifiant:03d}",
                **periode,
            )
        )
        return identifiant

    # -- résultat ---------------------------------------------------------------

    def instantane(self) -> Instantane:
        """Assemble l'instantané : conflits, accessibilité résolue, dépliage, verrous, indices."""
        lies: dict[int, set[int]] = {groupe.id: {groupe.id} for groupe in self._groupes}
        for a, b in self._conflits:
            lies[a].add(b)
            lies[b].add(a)
        exigeants = {groupe.id for groupe in self._groupes if groupe.accessibilite_requise}
        besoins = tuple(
            dataclasses.replace(
                besoin,
                accessibilite_requise=any(
                    not lies[g].isdisjoint(exigeants) for g in besoin.groupes
                ),
            )
            for besoin in self._besoins
        )
        semaines = semaines_entre(self.date_debut, self.date_fin)
        depliage = deplier(besoins, semaines, self._jours_fermes)
        seances = []
        for seance in depliage.seances:
            changements = {}
            for champ, source in (("verrou", self._verrous), ("indice", self._indices)):
                if seance.id in source:
                    place = source[seance.id]
                    fin = place.fin if place.fin >= 0 else place.debut + seance.duree
                    changements[champ] = dataclasses.replace(place, fin=fin)
            seances.append(dataclasses.replace(seance, **changements) if changements else seance)
        instantane = Instantane(
            semestre_id=1,
            libelle="Semestre de test",
            date_debut=self.date_debut,
            date_fin=self.date_fin,
            semaines=semaines,
            creneaux=self.creneaux,
            campus=tuple(self._campus),
            trajets=tuple(self._trajets),
            salles=tuple(self._salles),
            enseignants=tuple(self._enseignants),
            groupes=tuple(self._groupes),
            conflits_groupes=frozenset(self._conflits),
            indisponibilites=tuple(self._indisponibilites),
            jours_fermes=tuple(self._jours_fermes),
            parametres=self.parametres,
            besoins=besoins,
            seances=tuple(seances),
            problemes_depliage=depliage.problemes,
        )
        if not self._regles:
            return instantane
        identifiants = {
            "campus": {campus.code: campus.id for campus in self._campus},
            "salle": {salle.code: salle.id for salle in self._salles},
            "type_seance": dict(TYPES_SEANCE),
        }
        resolues = tuple(
            resolue
            for resolue in (
                resoudre_regle(regle, instantane, identifiants)
                for regle in self._regles
                if regle.active
            )
            if resolue.semaines
        )
        return dataclasses.replace(instantane, regles=resolues)


# --------------------------------------------------------------------------- #
# Aides de test
# --------------------------------------------------------------------------- #


def definition_niveau(type_regle: TypeRegle) -> Niveau:
    """Niveau par défaut d'un type de règle."""
    from edt.domaine.regles import CATALOGUE_TYPES

    return CATALOGUE_TYPES[type_regle].niveau_defaut


def niveaux_regles_posables(instantane: Instantane) -> dict[int, Niveau]:
    """Niveaux des règles de l'instantané que le moteur sait poser (les autres sont ignorées).

    Le moteur pose tous les types du catalogue (`types_non_couverts("solveur")`
    est vide) : le filtre garde les tests du côté sûr si un type s'ajoute.
    """
    return {regle.id: regle.niveau for regle in instantane.regles if regle.type in POSEURS}


NIVEAUX_DURS = {code: Niveau.NECESSAIRE for code in CATALOGUE}
"""Toutes les contraintes nécessaires : les tests parlent de règles dures, sauf demande contraire."""

OPTIONS = OptionsSolveur(
    temps_limite_s=20,
    graine=1,
    nb_workers=1,
    arret_sur_stagnation=False,
    niveaux=NIVEAUX_DURS,
)
"""Options des tests : mode reproductible (une graine, un fil de calcul, et pas d'arrêt sur
stagnation, qui lit l'horloge, et toutes les contraintes nécessaires : un test qui veut une
contrainte souple ou l'arrêt sur stagnation le demande)."""


def options(**changements) -> OptionsSolveur:
    """Options de test avec quelques réglages changés."""
    return dataclasses.replace(OPTIONS, **changements)


def par_seance(resultat: ResultatSolveur) -> dict[str, Placement]:
    """Placements d'un résultat, par identifiant de séance."""
    return {placement.seance_id: placement for placement in resultat.placements}


def verifier_solution(
    instantane: Instantane,
    resultat: ResultatSolveur,
    contraintes_desactivees: Iterable[str] = (),
) -> dict[str, Placement]:
    """ORACLE : exige une solution complète, sans aucune erreur du vérificateur officiel.

    Renvoie les placements par identifiant de séance. Les contraintes
    désactivées pour le calcul ne sont pas vérifiées, ni les contraintes
    souples du calcul (leurs entorses sont permises et comptées à part).
    """
    assert resultat.statut is StatutSolveur.SOLUTION, resultat.message
    assert {p.seance_id for p in resultat.placements} == {s.id for s in instantane.seances}
    assert all(placement.salle_id is not None for placement in resultat.placements)
    codes = codes_actifs(contraintes_desactivees)
    if resultat.niveaux:
        codes &= frozenset(c for c, n in resultat.niveaux.items() if n is Niveau.NECESSAIRE)
    # Les règles personnalisées sont vérifiées aux niveaux retenus par le moteur : une règle
    # nécessaire enfreinte est une erreur, une règle souple un avertissement.
    anomalies = verifier(
        instantane, resultat.placements, codes, niveaux_regles=resultat.niveaux_regles
    )
    erreurs = [anomalie for anomalie in anomalies if anomalie.bloquante]
    assert not erreurs, "\n".join(f"[{a.code}] {a.message}" for a in erreurs)
    return par_seance(resultat)


def verifier_dates(
    instantane: Instantane,
    occurrences: Sequence[Occurrence],
    contraintes_desactivees: Iterable[str] = (),
    *,
    niveaux=None,
    niveaux_regles=None,
) -> None:
    """ORACLE de la couche datée : aucune erreur du vérificateur sur ces occurrences.

    Avec `niveaux`, le vérificateur applique les mêmes niveaux que le replacement :
    une entorse à une règle souple est un avertissement, pas une erreur.
    `niveaux_regles` : de même pour les règles personnalisées.
    """
    if niveaux is not None:
        anomalies = verifier_occurrences(
            instantane, occurrences, niveaux=niveaux, niveaux_regles=niveaux_regles
        )
    else:
        anomalies = verifier_occurrences(
            instantane,
            occurrences,
            codes_actifs(contraintes_desactivees),
            niveaux_regles=niveaux_regles,
        )
    erreurs = [anomalie for anomalie in anomalies if anomalie.bloquante]
    assert not erreurs, "\n".join(f"[{a.code}] {a.message}" for a in erreurs)
