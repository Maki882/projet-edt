"""Préparation d'un calcul : les domaines de chaque séance, avant tout modèle.

Pour chaque séance de l'instantané, ce module calcule, en Python pur :

- ses **débuts admissibles** : couples (jour, heure) permis par les verrous,
  les jours et heures imposés, la grille (`HORAIRES`, `JOURS`), le déjeuner
  (`DEJEUNER`, seulement si ses horaires sont déterminés : voir
  `Regles.refus_horaire`) et les indisponibilités récurrentes de ses
  enseignants et de ses groupes (`DISPO_ENSEIGNANT`, `DISPO_GROUPE`) ;
- ses **salles admissibles** : celles qui satisfont `TYPE_SALLE`, `CAPACITE`,
  `EQUIPEMENT` et `ACCESSIBILITE`, moins celles qui sont indisponibles à tous
  ses débuts (`DISPO_SALLE`) ; quand une salle n'est indisponible qu'à
  certains débuts, la liste de ces débuts est gardée pour le modèle.

Seules les contraintes **dures** (niveau « nécessaire ») éliminent un début ou
une salle. Une contrainte souple laisse la possibilité ouverte et l'étiquette
d'une **entorse** (`Regles.Violation`, imputée à une contrainte du catalogue ou
à une règle personnalisée) : `SeancePreparee.entorses` (par début : `HORAIRES`,
`JOURS`, `DEJEUNER`, `DISPO_ENSEIGNANT`, `DISPO_GROUPE`, règles d'horaires),
`SeancePreparee.entorses_campus` (par début et campus : règles de jours visant
un campus, règles `CAMPUS`), `SeancePreparee.entorses_salles` (par salle :
règles `SALLES`), `SeancePreparee.debuts_genants` (par salle, les débuts où
elle est indisponible quand `DISPO_SALLE` est souple) et
`Regles.salles_compatibles(...).entorses` (exigences de salle souples). Le
modèle des horaires et l'affectation des salles les pénalisent.

Règles personnalisées de sorte « séance » (`edt.solveur.personnalisees`) :
les jours permis d'une séance viennent de `Instantane.reglages` (standard et
règles `JOURS_TRAVAILLES` retenues, campus compris) ; `PLAGE_HORAIRE` et
`CRENEAUX_INTERDITS` s'appliquent à chaque début, `CAMPUS` à chaque campus,
`SALLES` à chaque salle. En dur, le choix est retiré ; en souple, il porte
l'entorse de la règle. Les domaines d'une séance dépendent du besoin et des
règles qui la concernent (une règle datée ne vise que les séances dont les
semaines croisent sa période). Une règle d'un type sans poseur fait refuser le
calcul en la nommant.

Un domaine vide est une impossibilité certaine : elle est rapportée ici, en
nommant la séance et ce qui élimine ses possibilités, au lieu d'être
découverte plus tard comme une infaisabilité muette. Les contradictions
entre séances à horaire fixe (verrous, jour et heure imposés) sont relevées
au même endroit.

Un début est codé par un entier `jour * pas + minute`. Le `pas` dépasse une
journée augmentée de la plus longue séparation exigée (pause, trajet) : deux
séances de jours différents sont ainsi toujours assez éloignées sur cet axe,
et les règles « le même jour » deviennent de simples écarts.

Règles de construction des débuts (voir `docs/solveur.md`)
----------------------------------------------------------
- verrou (si `VERROU` est active) : le début est celui du verrou, rien d'autre ;
- le jour et l'heure imposés par le besoin (ou sa date, pour un besoin
  ponctuel) sont toujours respectés : ils font partie de la demande ;
- une heure imposée dispense de la grille, du déjeuner et, si le jour est
  imposé lui aussi, de la règle des jours ;
- sinon la séance commence sur un début de créneau, tient dans sa
  demi-journée et dans les heures d'ouverture, et évite le déjeuner (quand
  ses horaires sont déterminés).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from edt.domaine.contraintes import CATALOGUE, CodeContrainte, Niveau, Niveaux
from edt.domaine.enumerations import JOURS, CibleIndisponibilite, Gravite
from edt.domaine.heures import MINUTES_PAR_JOUR, duree_vers_texte, se_chevauchent
from edt.domaine.instantane import Besoin, Instantane, Placement, SeanceAPlacer
from edt.domaine.regles import NaturePortee, TypeRegle
from edt.erreurs import ErreurIntrouvable
from edt.solveur.personnalisees import Choix, enfreint, types_sans_poseur
from edt.solveur.regles import Regles, Violation, enumerer, nom_creneau, nom_seance
from edt.verification import Anomalie

journal = logging.getLogger(__name__)

C = CodeContrainte


@dataclass
class SeancePreparee:
    """Une séance et ses domaines.

    - `duree` : durée placée (celle du verrou pour une séance verrouillée) ;
    - `verrouillee` : un verrou fixe son créneau (contrainte `VERROU` active) ;
    - `semaines` : semaines actives, en masque de bits (bit n = semaine n) ;
    - `debuts` : débuts admissibles codés `jour * pas + minute`, triés ;
    - `salles` : identifiants des salles admissibles (capacité croissante) ;
    - `debuts_interdits` : par salle, les débuts où elle n'est pas permise
      (`DISPO_SALLE` dure, règle de jours dure visant son campus) ;
    - `entorses` : par début, les contraintes et règles souples qu'il enfreint ;
    - `entorses_campus` : par (début, campus), celles qu'enfreint ce campus-là ;
    - `entorses_salles` : par salle, les règles `SALLES` souples qu'elle enfreint ;
    - `debuts_genants` : par salle, les débuts où elle est indisponible quand
      `DISPO_SALLE` est souple (une entorse si la salle y est choisie).
    """

    index: int
    seance: SeanceAPlacer
    duree: int
    verrouillee: bool
    semaines: int
    debuts: tuple[int, ...] = ()
    salles: tuple[int, ...] = ()
    debuts_interdits: dict[int, frozenset[int]] = field(default_factory=dict)
    entorses: dict[int, tuple[Violation, ...]] = field(default_factory=dict)
    entorses_campus: dict[tuple[int, int], tuple[Violation, ...]] = field(default_factory=dict)
    entorses_salles: dict[int, tuple[Violation, ...]] = field(default_factory=dict)
    debuts_genants: dict[int, frozenset[int]] = field(default_factory=dict)

    @property
    def besoin(self) -> Besoin:
        """Besoin de la séance."""
        return self.seance.besoin

    @property
    def horaire_fixe(self) -> bool:
        """Vrai si la séance n'a qu'un début possible (verrou, ou jour et heure imposés)."""
        return len(self.debuts) == 1


@dataclass
class _Domaine:
    """Domaines communs aux séances sans verrou d'un même besoin soumises aux mêmes règles."""

    debuts: tuple[int, ...] = ()
    salles: tuple[int, ...] = ()
    debuts_interdits: dict[int, frozenset[int]] = field(default_factory=dict)
    entorses: dict[int, tuple[Violation, ...]] = field(default_factory=dict)
    entorses_campus: dict[tuple[int, int], tuple[Violation, ...]] = field(default_factory=dict)
    entorses_salles: dict[int, tuple[Violation, ...]] = field(default_factory=dict)
    debuts_genants: dict[int, frozenset[int]] = field(default_factory=dict)
    probleme: tuple[CodeContrainte, str] | None = None
    seances: list[str] = field(default_factory=list)


class Preparation:
    """Domaines de toutes les séances d'un instantané, et impossibilités certaines.

    - `seances` : une `SeancePreparee` par séance, dans l'ordre de l'instantané ;
    - `anomalies` : domaines vides, verrous intenables, séances à horaire
      imposé qui se contredisent (gravité `ERREUR`) ;
    - `conflits_verrous` : paires de séances verrouillées qui se contredisent
      (refus du calcul, sauf `tolerer_violations_heritees`) ;
    - `isolees` : avec `tolerer_violations_heritees`, les séances verrouillées
      dispensées des contraintes de paire face aux autres séances verrouillées ;
    - `entorses_fixes` : entorses aux contraintes souples qui ne dépendent pas
      du placement (dépassement du maximum hebdomadaire d'un enseignant),
      relevées par les pré-vérifications.

    `niveaux` prime sur `contraintes_desactivees` (voir `Regles`) ;
    `niveaux_regles` : niveau de chaque règle personnalisée retenue pour ce
    calcul (`edt.solveur.personnalisees.appliquer_niveaux`). `instantane` est
    l'instantané du calcul, avec ces seules règles.
    """

    def __init__(
        self,
        instantane: Instantane,
        contraintes_desactivees: Iterable[str] = (),
        tolerer_violations_heritees: bool = False,
        niveaux: Niveaux | None = None,
        niveaux_regles: dict[int, Niveau] | None = None,
    ) -> None:
        self.regles = Regles(instantane, contraintes_desactivees, niveaux, niveaux_regles)
        self.instantane = instantane = self.regles.instantane
        self.tolerer = tolerer_violations_heritees
        self.entorses_fixes: dict[CodeContrainte, int] = defaultdict(int)
        parametres = instantane.parametres
        marge = max(
            [self.regles.pause_la_plus_longue(), parametres.trajet_defaut_min]
            + [trajet.duree for trajet in instantane.trajets]
        )
        self.pas = MINUTES_PAR_JOUR + marge + 1
        self.anomalies: list[Anomalie] = []
        self.conflits_verrous: list[Anomalie] = []
        self._campus_de = {salle.id: salle.campus_id for salle in instantane.salles}
        if instantane.regles:
            journal.info(
                "Règles personnalisées du calcul : %s",
                ", ".join(f"{r.code} ({r.niveau.value})" for r in instantane.regles),
            )
        for regle in types_sans_poseur(instantane):
            self.anomalies.append(
                Anomalie(
                    C.REGLE,
                    Gravite.ERREUR,
                    f"La règle {regle.code} ({regle.phrase}) est d'un type que le générateur ne "
                    f"sait pas appliquer (« {regle.type.libelle} ») : ne pas l'appliquer à ce "
                    "calcul, ou la désactiver.",
                    regle_id=regle.id,
                    regle_code=regle.code,
                    regle_phrase=regle.phrase,
                )
            )
        self._domaines: dict[tuple, _Domaine] = {}
        self.seances = [
            self._preparer(index, seance) for index, seance in enumerate(instantane.seances)
        ]
        for (besoin_id, _), domaine in self._domaines.items():
            if domaine.probleme is not None:
                code, message = domaine.probleme
                besoin = instantane.besoin(besoin_id)
                pluriel = "s" if len(domaine.seances) > 1 else ""
                self.anomalies.append(
                    Anomalie(
                        code,
                        Gravite.ERREUR,
                        f"« {besoin.libelle} » (séance{pluriel} {enumerer(domaine.seances)}) : "
                        f"{message}",
                        seances=tuple(domaine.seances),
                        groupes=tuple(sorted(besoin.groupes)),
                        besoins=(besoin_id,),
                    )
                )
        self._relever_les_contradictions()
        self.isolees = self._seances_isolees()

    # -- codage des débuts ------------------------------------------------------

    def coder(self, jour: int, minute: int) -> int:
        """Code d'un début : `jour * pas + minute`."""
        return jour * self.pas + minute

    def decoder(self, code: int) -> tuple[int, int]:
        """Inverse de `coder` : `(jour, minute)`."""
        return divmod(code, self.pas)

    # -- une séance -------------------------------------------------------------

    def _preparer(self, index: int, seance: SeanceAPlacer) -> SeancePreparee:
        semaines = sum(1 << numero for numero in seance.semaines)
        if seance.verrou is not None and self.regles.active(C.VERROU):
            return self._preparer_verrouillee(index, seance, seance.verrou, semaines)
        cle = (seance.besoin.id, self.regles.signature_regles(seance.id))
        domaine = self._domaines.get(cle)
        if domaine is None:
            domaine = self._domaine_libre(seance)
            self._domaines[cle] = domaine
        domaine.seances.append(seance.id)
        return SeancePreparee(
            index=index,
            seance=seance,
            duree=seance.besoin.duree,
            verrouillee=False,
            semaines=semaines,
            debuts=domaine.debuts,
            salles=domaine.salles,
            debuts_interdits=domaine.debuts_interdits,
            entorses=domaine.entorses,
            entorses_campus=domaine.entorses_campus,
            entorses_salles=domaine.entorses_salles,
            debuts_genants=domaine.debuts_genants,
        )

    def _domaine_libre(self, seance: SeanceAPlacer) -> _Domaine:
        """Domaines d'une séance sans verrou (voir les règles en tête de module).

        Ils ne dépendent que du besoin et des règles qui concernent la
        séance : les indisponibilités récurrentes valent toutes les semaines,
        quelles que soient les semaines actives.
        """
        besoin, regles = seance.besoin, self.regles
        compatibles = regles.salles_compatibles(besoin)
        salles, entorses_salles, refus = self._salles_selon_les_regles(
            seance, [salle.id for salle in compatibles.salles]
        )
        # Les débuts se jugent sur les campus des salles possibles ; sans salle, sur tous.
        campus_possibles = sorted(
            {self._campus_de[salle] for salle in salles}
            or {campus.id for campus in self.instantane.campus}
        )
        debuts, entorses, entorses_campus, interdits_campus, elimines, examines = (
            self._debuts_libres(seance, campus_possibles)
        )
        if not debuts:
            return _Domaine(probleme=self._probleme_de_debuts(seance, elimines, examines))
        if not compatibles.salles:
            return _Domaine(debuts=debuts, probleme=self._probleme_de_salles(besoin))
        if not salles:
            assert refus is not None
            return _Domaine(
                debuts=debuts,
                probleme=(
                    C.REGLE,
                    f"aucune salle ne convient : {refus.raison} écarte toutes les salles qui "
                    "conviennent au besoin. Assouplir cette règle ou ne pas l'appliquer à ce "
                    "calcul.",
                ),
            )

        salles, interdits, genants = self._filtrer_salles_disponibles(
            besoin.duree, debuts, list(salles)
        )
        if not salles:
            return _Domaine(
                debuts=debuts,
                probleme=(
                    C.DISPO_SALLE,
                    f"aucune salle n'est libre : les {len(compatibles.salles)} salle(s) qui "
                    "conviennent sont indisponibles à tous les horaires possibles de la séance. "
                    "Revoir les indisponibilités de ces salles ou ajouter une salle qui convient.",
                ),
            )
        if interdits_campus:
            # Une règle dure de jours ou d'horaires visant un campus : ses salles perdent ces
            # débuts.
            interdits = dict(interdits)
            for salle in salles:
                bloques = interdits_campus.get(self._campus_de[salle])
                if bloques:
                    interdits[salle] = interdits.get(salle, frozenset()) | bloques
            if all(debut in interdits.get(salle, ()) for salle in salles for debut in debuts):
                return _Domaine(
                    debuts=debuts,
                    probleme=(
                        C.REGLE,
                        "aucun horaire possible sur un campus permis : une règle de jours ou "
                        "d'horaires de portée campus ferme chaque horaire possible sur le "
                        "campus de chaque salle qui convient. Assouplir cette règle ou ne pas "
                        "l'appliquer à ce calcul.",
                    ),
                )
        if len(salles) == 1 and interdits:
            # Une seule salle : ses indisponibilités réduisent directement les débuts.
            debuts = tuple(d for d in debuts if d not in interdits[salles[0]])
            entorses = {debut: codes for debut, codes in entorses.items() if debut in debuts}
            entorses_campus = {
                cle: codes for cle, codes in entorses_campus.items() if cle[0] in debuts
            }
            interdits = {}
            if not debuts:
                return _Domaine(
                    probleme=(
                        C.DISPO_SALLE,
                        "aucun horaire possible : la seule salle qui convient est indisponible "
                        "ou interdite à tous les horaires possibles de la séance. Revoir ses "
                        "indisponibilités ou ajouter une salle qui convient.",
                    )
                )
        return _Domaine(
            debuts=debuts,
            salles=salles,
            debuts_interdits=interdits,
            entorses=entorses,
            entorses_campus=entorses_campus,
            entorses_salles=entorses_salles,
            debuts_genants=genants,
        )

    def _salles_selon_les_regles(
        self, seance: SeanceAPlacer, salles: list[int]
    ) -> tuple[list[int], dict[int, tuple[Violation, ...]], Violation | None]:
        """Salles permises par les règles `SALLES` et `CAMPUS` dures de la séance.

        Renvoie `(salles gardées, entorses par salle aux règles SALLES souples,
        dernière violation dure)`. Une règle `CAMPUS` souple ne se règle pas
        ici mais par choix (début, campus), dans le modèle des horaires.
        """
        regles = self.regles
        concernantes = [
            regle
            for regle in regles.regles_de_seance(seance.id)
            if regle.type in (TypeRegle.SALLES, TypeRegle.CAMPUS)
        ]
        if not concernantes:
            return salles, {}, None
        gardees: list[int] = []
        entorses: dict[int, tuple[Violation, ...]] = {}
        derniere: Violation | None = None
        for salle_id in salles:
            choix = Choix(0, 0, 0, campus_id=self._campus_de[salle_id], salle_id=salle_id)
            violations = [
                (regle, regles.violation_de_regle(regle))
                for regle in concernantes
                if enfreint(regle, choix)
            ]
            dures = [v for _, v in violations if regles.dure_violation(v)]
            if dures:
                derniere = dures[0]
                continue
            gardees.append(salle_id)
            souples = regles.souples_violations(
                v for regle, v in violations if regle.type is TypeRegle.SALLES
            )
            if souples:
                entorses[salle_id] = souples
        return gardees, entorses, derniere

    def _debuts_libres(
        self, seance: SeanceAPlacer, campus_possibles: list[int]
    ) -> tuple[
        tuple[int, ...],
        dict[int, tuple[Violation, ...]],
        dict[tuple[int, int], tuple[Violation, ...]],
        dict[int, frozenset[int]],
        dict[tuple[CodeContrainte, str], int],
        int,
    ]:
        """Débuts admissibles d'une séance sans verrou.

        Renvoie `(débuts codés, entorses par début, entorses par début et
        campus, débuts interdits par campus, éliminations, nombre de débuts
        examinés)`. Seules les contraintes et règles dures éliminent un début ;
        les éliminations comptent les débuts écartés par chaque raison. Un
        début gardé qui enfreint des contraintes souples porte leurs
        violations.

        Jours : le standard et les règles de jours retenues pour la séance
        (`Regles.violations_de_jour`). Quand une règle de jours vise un campus
        possible, les jours se jugent campus par campus : un jour fermé sur
        tous les campus est éliminé ; fermé sur certains seulement, il
        interdit leurs salles ce jour-là (dur) ou pénalise le choix de ce
        campus (souple). Une règle `CAMPUS` souple pénalise de même chaque
        début sur un campus refusé ; une règle d'horaires de portée campus
        (`PLAGE_HORAIRE`, `CRENEAUX_INTERDITS`) interdit ou pénalise de même
        les débuts qu'elle exclut, sur son campus seulement.
        """
        regles, besoin = self.regles, seance.besoin
        imposee = besoin.debut_impose is not None or besoin.ponctuel
        elimines: dict[tuple[CodeContrainte, str], int] = defaultdict(int)
        entorses: dict[int, tuple[Violation, ...]] = {}
        entorses_campus: dict[tuple[int, int], list[Violation]] = defaultdict(list)
        interdits_campus: dict[int, set[int]] = defaultdict(set)
        par_campus = bool(set(regles.jours_de(seance.id).campus_regles) & set(campus_possibles))
        campus_refuses = self._campus_refuses(seance, campus_possibles)
        # Règles d'horaires de portée campus : jugées par (début, campus de la règle).
        horaires_campus = [
            regle
            for regle in regles.regles_de_seance(seance.id)
            if regle.portee is NaturePortee.CAMPUS
            and regle.type in (TypeRegle.PLAGE_HORAIRE, TypeRegle.CRENEAUX_INTERDITS)
            and regle.cible_id in campus_possibles
        ]

        jour_impose = seance.jour_impose
        jours = [jour_impose] if jour_impose is not None else list(range(7))
        dispensee_des_jours = imposee and jour_impose is not None

        plages_par_jour = defaultdict(list)
        for code, nom, plage in regles.recurrentes_des_personnes(besoin):
            plages_par_jour[plage.jour].append((code, nom, plage))

        debuts: list[int] = []
        examines = 0
        for jour in jours:
            # Jours permis : sans considérer le campus, ou campus par campus.
            if dispensee_des_jours:
                violations_jour: dict[int | None, list[Violation]] = {}
            elif par_campus:
                violations_jour = {
                    campus: regles.violations_de_jour(seance.id, jour, campus)
                    for campus in campus_possibles
                }
            else:
                violations_jour = {None: regles.violations_de_jour(seance.id, jour, None)}
            dures_jour = {
                campus: [v for v in violations if regles.dure_violation(v)]
                for campus, violations in violations_jour.items()
            }
            if violations_jour and all(dures_jour.values()):
                violation = next(iter(dures_jour.values()))[0]
                nb_debuts = len(regles.debuts_a_examiner(besoin, jour))
                if jour_impose is not None:
                    elimines[(violation.code, f"le {JOURS[jour]} imposé : {violation.raison}")] += (
                        max(nb_debuts, 1)
                    )
                    return (), {}, {}, {}, elimines, max(nb_debuts, 1)
                if violation.regle_id is not None:
                    # Une règle ferme ce jour : dit dans le message ; le standard reste muet.
                    elimines[(violation.code, violation.raison)] += nb_debuts
                    examines += nb_debuts
                continue
            for minute in regles.debuts_a_examiner(besoin, jour):
                examines += 1
                fin = minute + besoin.duree
                refus = regles.refus_horaire(besoin, besoin.duree, jour, minute, seance.id)
                if refus is None:
                    refus = next(
                        (
                            Violation(code, f"une indisponibilité de {nom}")
                            for code, nom, plage in plages_par_jour.get(jour, ())
                            if regles.dure(code)
                            and se_chevauchent(minute, fin, plage.debut, plage.fin)
                        ),
                        None,
                    )
                if refus is not None:
                    elimines[(refus.code, refus.raison)] += 1
                    continue
                violations = list(
                    regles.entorses_horaire(besoin, besoin.duree, jour, minute, seance.id)
                )
                violations.extend(
                    Violation(code, f"une indisponibilité de {nom}")
                    for code, nom, plage in plages_par_jour.get(jour, ())
                    if regles.souple(code) and se_chevauchent(minute, fin, plage.debut, plage.fin)
                )
                debut = self.coder(jour, minute)
                debuts.append(debut)
                if not par_campus:
                    violations.extend(violations_jour.get(None, ()))
                else:
                    for campus, par_jour in violations_jour.items():
                        if dures_jour[campus]:
                            interdits_campus[campus].add(debut)
                        else:
                            entorses_campus[(debut, campus)].extend(par_jour)
                for campus, refus_campus in campus_refuses.items():
                    entorses_campus[(debut, campus)].extend(refus_campus)
                for regle in horaires_campus:
                    if not enfreint(regle, Choix(jour, minute, fin, campus_id=regle.cible_id)):
                        continue
                    violation = regles.violation_de_regle(regle)
                    if regles.dure_violation(violation):
                        interdits_campus[regle.cible_id].add(debut)
                    else:
                        entorses_campus[(debut, regle.cible_id)].append(violation)
                souples = regles.souples_violations(violations)
                if souples:
                    entorses[debut] = souples
        retenues_campus = {
            cle: regles.souples_violations(liste)
            for cle, liste in entorses_campus.items()
            if cle[0] in debuts
        }
        return (
            tuple(sorted(debuts)),
            entorses,
            {cle: liste for cle, liste in retenues_campus.items() if liste},
            {campus: frozenset(codes) for campus, codes in interdits_campus.items()},
            elimines,
            examines,
        )

    def _campus_refuses(
        self, seance: SeanceAPlacer, campus_possibles: list[int]
    ) -> dict[int, tuple[Violation, ...]]:
        """Par campus possible, les règles `CAMPUS` souples qu'un choix sur ce campus enfreint."""
        regles = self.regles
        concernantes = [
            regle for regle in regles.regles_de_seance(seance.id) if regle.type is TypeRegle.CAMPUS
        ]
        if not concernantes:
            return {}
        refuses = {}
        for campus in campus_possibles:
            souples = regles.souples_violations(
                regles.violation_de_regle(regle)
                for regle in concernantes
                if enfreint(regle, Choix(0, 0, 0, campus_id=campus))
            )
            if souples:
                refuses[campus] = souples
        return refuses

    def _probleme_de_debuts(
        self,
        seance: SeanceAPlacer,
        elimines: dict[tuple[CodeContrainte, str], int],
        examines: int,
    ) -> tuple[CodeContrainte, str]:
        """Explication d'un domaine de débuts vide."""
        if not elimines:
            if seance.besoin.ponctuel and seance.besoin.debut_impose is None:
                return (
                    C.HORAIRES,
                    "aucun horaire possible : la grille n'a aucun créneau le jour de cette "
                    "séance ponctuelle. Préciser son heure de début.",
                )
            return (
                C.JOURS,
                "aucun horaire possible : la grille horaire n'offre aucun jour de cours permis "
                "pour cette séance. Ajouter des créneaux à la grille ou revoir les jours permis.",
            )
        (code, _), _ = max(elimines.items(), key=lambda item: item[1])
        details = enumerer(
            (f"{nombre} par {raison}" for (_, raison), nombre in elimines.items()), maximum=6
        )
        return (
            code,
            f"aucun horaire possible : {examines} début(s) examiné(s), tous écartés ({details}). "
            "Lever ou assouplir l'une de ces causes.",
        )

    def _probleme_de_salles(self, besoin: Besoin) -> tuple[CodeContrainte, str]:
        """Explication d'un domaine de salles vide : quelle exigence élimine les salles."""
        total = len(self.instantane.salles)
        exigences = self.regles.salles_compatibles(besoin).exigences
        if total == 0 or not exigences:
            return (
                C.TYPE_SALLE,
                "aucune salle n'est enregistrée pour ce semestre. Ajouter des salles avant de "
                "lancer le calcul.",
            )
        pire = min(exigences, key=lambda exigence: exigence.nb_salles)
        details = enumerer(
            (f"{exigence.nb_salles} {exigence.libelle}" for exigence in exigences), maximum=6
        )
        if pire.nb_salles > 0:
            conclusion = "aucune ne réunit toutes ces exigences"
        elif pire.code is C.ACCESSIBILITE:
            conclusion = "la séance requiert une salle accessible et aucune salle ne l'est"
        else:
            conclusion = f"l'exigence « {CATALOGUE[pire.code].libelle} » élimine toutes les salles"
        return (
            pire.code,
            f"aucune salle ne convient. Sur {total} salle(s) : {details} ; {conclusion}. "
            "Ajouter une salle qui convient ou revoir les exigences du besoin.",
        )

    def _filtrer_salles_disponibles(
        self, duree: int, debuts: tuple[int, ...], salles: list[int]
    ) -> tuple[tuple[int, ...], dict[int, frozenset[int]], dict[int, frozenset[int]]]:
        """Salles gardées, débuts interdits par salle et débuts gênants par salle.

        `DISPO_SALLE` dure : une salle indisponible à tous les débuts est
        retirée, les autres gardent la liste de leurs débuts interdits.
        `DISPO_SALLE` souple : toutes les salles restent, et les débuts où
        chacune est indisponible sont « gênants » (une entorse si la salle y
        est choisie). Sans effet si `DISPO_SALLE` est ignorée.
        """
        if not self.regles.active(C.DISPO_SALLE):
            return tuple(salles), {}, {}
        souple = self.regles.souple(C.DISPO_SALLE)
        gardees: list[int] = []
        interdits: dict[int, frozenset[int]] = {}
        genants: dict[int, frozenset[int]] = {}
        for salle_id in salles:
            plages = self.regles.recurrentes(CibleIndisponibilite.SALLE, salle_id)
            if not plages:
                gardees.append(salle_id)
                continue
            bloques = set()
            for code in debuts:
                jour, minute = self.decoder(code)
                if any(
                    plage.jour == jour
                    and se_chevauchent(minute, minute + duree, plage.debut, plage.fin)
                    for plage in plages
                ):
                    bloques.add(code)
            if souple:
                gardees.append(salle_id)
                if bloques:
                    genants[salle_id] = frozenset(bloques)
            elif len(bloques) < len(debuts):
                gardees.append(salle_id)
                if bloques:
                    interdits[salle_id] = frozenset(bloques)
        return tuple(gardees), interdits, genants

    # -- séances verrouillées ---------------------------------------------------

    def _preparer_verrouillee(
        self, index: int, seance: SeanceAPlacer, verrou: Placement, semaines: int
    ) -> SeancePreparee:
        """Séance fixée par son verrou : vérifie que le verrou est tenable seul.

        Horaires et jours ne sont pas opposés à un verrou (une séance imposée
        qui s'en écarte n'est qu'une information) ; les indisponibilités, les
        exigences de salle du catalogue et les règles `SALLES` et `CAMPUS`
        nécessaires le sont, et les règles souples valent une entorse. Un
        verrou incohérent (jour, plage), d'une autre durée que son besoin ou
        sur une salle inconnue est refusé en le nommant, dans tous les cas.
        """
        regles, besoin = self.regles, seance.besoin
        preparee = SeancePreparee(
            index=index,
            seance=seance,
            duree=verrou.fin - verrou.debut,
            verrouillee=True,
            semaines=semaines,
        )

        def refuser(code: CodeContrainte, message: str, **cibles) -> None:
            self.anomalies.append(
                Anomalie(
                    code,
                    Gravite.ERREUR,
                    message,
                    seances=(seance.id,),
                    besoins=(besoin.id,),
                    **cibles,
                )
            )

        def refuser_regle(violation: Violation, message: str, **cibles) -> None:
            regle = regles.regle(violation.regle_id)
            refuser(
                C.REGLE,
                message,
                regle_id=regle.id,
                regle_code=regle.code,
                regle_phrase=regle.phrase,
                **cibles,
            )

        def entorses_campus(
            salles: tuple[int, ...],
        ) -> dict[tuple[int, int], tuple[Violation, ...]]:
            """Règles CAMPUS souples : une entorse par campus refusé, au début du verrou."""
            campus = sorted({self._campus_de[salle_id] for salle_id in salles})
            return {
                (preparee.debuts[0], campus_id): violations
                for campus_id, violations in self._campus_refuses(seance, campus).items()
            }

        if not (0 <= verrou.jour <= 6 and 0 <= verrou.debut < verrou.fin <= MINUTES_PAR_JOUR):
            refuser(
                C.VERROU,
                f"Le verrou de la séance {nom_seance(seance)} est incohérent (jour "
                f"{verrou.jour}, de {verrou.debut} à {verrou.fin} minutes) : le corriger ou "
                "le retirer.",
            )
            return preparee
        creneau = nom_creneau(verrou.jour, verrou.debut, verrou.fin)
        designation = f"La séance verrouillée {nom_seance(seance)}, placée {creneau},"
        valide = True
        ecarts = []
        if preparee.duree != besoin.duree:
            # La durée vient du besoin, pas de la grille : l'écart vaut quel que soit le
            # niveau de HORAIRES (jamais de verrou appliqué en silence avec une autre durée).
            ecarts.append(
                f"dure {duree_vers_texte(preparee.duree)} au lieu des "
                f"{duree_vers_texte(besoin.duree)} demandées"
            )
        if regles.active(C.HORAIRES):
            if seance.jour_impose is not None and seance.jour_impose != verrou.jour:
                ecarts.append(f"devrait avoir lieu le {JOURS[seance.jour_impose]}")
            if besoin.debut_impose is not None and besoin.debut_impose != verrou.debut:
                ecarts.append("ne commence pas à l'heure imposée par son besoin")
        if ecarts:
            valide = False
            refuser(
                C.VERROU,
                f"{designation} contredit son besoin : elle {' et '.join(ecarts)}. "
                "Corriger le verrou ou le besoin.",
                jour=verrou.jour,
            )
        entorses: list[Violation] = []
        for code, nom, plage in regles.recurrentes_des_personnes(besoin):
            if plage.jour == verrou.jour and se_chevauchent(
                verrou.debut, verrou.fin, plage.debut, plage.fin
            ):
                if regles.souple(code):
                    # Le verrou tient ; l'entorse est comptée.
                    entorses.append(Violation(code, f"une indisponibilité de {nom}"))
                    continue
                valide = False
                refuser(
                    code,
                    f"{designation} tombe pendant une indisponibilité de {nom}. Déplacer le "
                    "verrou ou revoir cette indisponibilité.",
                    jour=verrou.jour,
                )
        if not valide:
            return preparee
        preparee.debuts = (self.coder(verrou.jour, verrou.debut),)
        if entorses:
            preparee.entorses = {preparee.debuts[0]: regles.souples_violations(entorses)}

        if verrou.salle_id is None:
            compatibles = [salle.id for salle in regles.salles_compatibles(besoin).salles]
            if not compatibles:
                code, message = self._probleme_de_salles(besoin)
                refuser(code, f"{designation} n'a pas de salle : {message}")
                return preparee
            # Les règles SALLES et CAMPUS de la séance, comme pour une séance libre.
            permises, entorses_salles, ecartee = self._salles_selon_les_regles(seance, compatibles)
            if not permises:
                assert ecartee is not None
                refuser_regle(
                    ecartee,
                    f"{designation} n'a pas de salle : {ecartee.raison} écarte toutes les salles "
                    "qui conviennent au besoin.",
                    jour=verrou.jour,
                )
                return preparee
            salles, _, genants = self._filtrer_salles_disponibles(
                preparee.duree, preparee.debuts, permises
            )
            if not salles:
                refuser(
                    C.DISPO_SALLE,
                    f"{designation} n'a pas de salle : toutes les salles qui conviennent sont "
                    "indisponibles à cet horaire. Déplacer le verrou ou revoir les "
                    "indisponibilités des salles.",
                    jour=verrou.jour,
                )
            preparee.salles = salles
            preparee.debuts_genants = genants
            preparee.entorses_salles = entorses_salles
            preparee.entorses_campus = entorses_campus(salles)
            return preparee

        try:
            salle = self.instantane.salle(verrou.salle_id)
        except ErreurIntrouvable:
            journal.warning(
                "Verrou de la séance %s : salle n° %s absente de l'instantané",
                seance.id,
                verrou.salle_id,
            )
            refuser(
                C.VERROU,
                f"{designation} désigne une salle qui n'existe pas dans ce semestre. Choisir "
                "une autre salle pour ce verrou, ou le retirer.",
            )
            return preparee
        defauts = [
            (code, libelle)
            for code, libelle, test in regles.exigences_de_salle(besoin)
            if regles.dure(code) and not test(salle)
        ]
        for code, libelle in defauts:
            refuser(
                code,
                f"{designation} est verrouillée en salle {salle.code}, qui ne fait pas partie "
                f"des salles {libelle}. Changer la salle du verrou ou ignorer la règle "
                f"« {CATALOGUE[code].libelle} » pour ce calcul.",
                salles=(salle.id,),
                jour=verrou.jour,
            )
        # Les règles SALLES et CAMPUS de la séance : une salle verrouillée qu'une règle
        # nécessaire écarte est refusée comme pour une exigence de salle du catalogue.
        permises, entorses_salles, ecartee = self._salles_selon_les_regles(seance, [salle.id])
        if not permises:
            assert ecartee is not None
            refuser_regle(
                ecartee,
                f"{designation} est verrouillée en salle {salle.code}, que {ecartee.raison} "
                "écarte. Changer la salle du verrou, ou ne pas appliquer cette règle à ce calcul.",
                salles=(salle.id,),
                jour=verrou.jour,
            )
            defauts.append((C.REGLE, ecartee.raison))
        salles, _, genants = self._filtrer_salles_disponibles(
            preparee.duree, preparee.debuts, [salle.id]
        )
        if not salles:
            refuser(
                C.DISPO_SALLE,
                f"{designation} est verrouillée en salle {salle.code}, indisponible à cet "
                "horaire. Changer la salle ou l'horaire du verrou, ou revoir cette "
                "indisponibilité.",
                salles=(salle.id,),
                jour=verrou.jour,
            )
        if not defauts:
            preparee.salles = salles
            preparee.debuts_genants = genants
            preparee.entorses_salles = entorses_salles
            preparee.entorses_campus = entorses_campus(salles)
        return preparee

    # -- contradictions entre séances à horaire fixe ----------------------------

    def creneau_fixe(self, seance: SeancePreparee) -> tuple[int, int, int, int | None] | None:
        """`(jour, début, fin, salle ou None)` d'une séance à horaire fixe, sinon `None`."""
        if not seance.horaire_fixe:
            return None
        jour, minute = self.decoder(seance.debuts[0])
        salle = seance.salles[0] if len(seance.salles) == 1 else None
        return jour, minute, minute + seance.duree, salle

    def _relever_les_contradictions(self) -> None:
        """Paires de séances à horaire fixe qui violent déjà entre elles une contrainte **dure**.

        Deux séances verrouillées : `conflits_verrous` (violation héritée, que
        l'utilisateur peut tolérer). Sinon l'une au moins tient son horaire de
        son besoin (jour et heure imposés) : l'impossibilité est certaine.
        Une règle souple enfreinte entre deux horaires fixes n'est pas une
        impossibilité : le modèle compte l'entorse (comme `_heurt` et le
        replacement dans la couche datée).
        """
        par_jour: dict[int, list[tuple[SeancePreparee, tuple[int, int, int, int | None]]]] = (
            defaultdict(list)
        )
        for seance in self.seances:
            creneau = self.creneau_fixe(seance)
            if creneau is not None:
                par_jour[creneau[0]].append((seance, creneau))
        for jour in sorted(par_jour):
            groupe = par_jour[jour]
            for position, (a, (_, debut_a, fin_a, salle_a)) in enumerate(groupe):
                for b, (_, debut_b, fin_b, salle_b) in groupe[position + 1 :]:
                    if not a.semaines & b.semaines:
                        continue
                    violations = [
                        violation
                        for violation in self.regles.violations_de_paire(
                            a.besoin, debut_a, fin_a, salle_a, b.besoin, debut_b, fin_b, salle_b
                        )
                        if self.regles.dure_violation(violation)
                    ]
                    if not violations:
                        continue
                    codes = [violation.code for violation in violations]
                    violees = enumerer(self.regles.texte_violation(v) for v in violations)
                    cibles = {
                        "seances": (a.seance.id, b.seance.id),
                        "jour": jour,
                        "besoins": tuple(sorted({a.besoin.id, b.besoin.id})),
                    }
                    if a.verrouillee and b.verrouillee:
                        self.conflits_verrous.append(
                            Anomalie(
                                codes[0],
                                Gravite.ERREUR,
                                f"Les séances verrouillées {nom_seance(a.seance)}, "
                                f"{nom_creneau(jour, debut_a, fin_a)}, et {nom_seance(b.seance)}, "
                                f"{nom_creneau(jour, debut_b, fin_b)}, se contredisent "
                                f"({violees}).",
                                **cibles,
                            )
                        )
                    elif a.besoin.id == b.besoin.id:
                        self.anomalies.append(
                            Anomalie(
                                codes[0],
                                Gravite.ERREUR,
                                f"« {a.besoin.libelle} » impose le même jour et la même heure "
                                f"({nom_creneau(jour, debut_a, fin_a)}) à ses séances "
                                f"{a.seance.id} et {b.seance.id}, qui ne peuvent pas avoir lieu "
                                f"en même temps ({violees}). Réduire le nombre de séances par "
                                "semaine ou retirer le jour ou l'heure imposés.",
                                **cibles,
                            )
                        )
                    else:
                        self.anomalies.append(
                            Anomalie(
                                codes[0],
                                Gravite.ERREUR,
                                f"Les séances {nom_seance(a.seance)}, "
                                f"{nom_creneau(jour, debut_a, fin_a)}, et {nom_seance(b.seance)}, "
                                f"{nom_creneau(jour, debut_b, fin_b)}, n'ont chacune qu'un horaire "
                                f"possible, et ces horaires se contredisent ({violees}).",
                                **cibles,
                            )
                        )

    def _seances_isolees(self) -> frozenset[int]:
        """Séances verrouillées à dispenser des contraintes de paire entre verrous.

        Avec `tolerer_violations_heritees`, aucune contrainte ne s'applique à
        une paire de séances verrouillées. Deux verrous qui ne se contredisent
        pas respectent de toute façon ces contraintes : il suffit d'isoler les
        séances impliquées dans une contradiction et celles dont la salle
        reste à choisir (pour qu'une autre séance verrouillée ne restreigne
        pas ce choix).
        """
        if not self.tolerer:
            return frozenset()
        par_id = {seance.seance.id: seance.index for seance in self.seances}
        isolees = {
            par_id[seance_id] for conflit in self.conflits_verrous for seance_id in conflit.seances
        }
        isolees.update(
            seance.index
            for seance in self.seances
            if seance.verrouillee and seance.seance.verrou.salle_id is None
        )
        return frozenset(isolees)

    # -- relations entre séances ------------------------------------------------

    def paires_de_personnes(self) -> list[tuple[int, int, bool, bool]]:
        """Paires `(i, j, enseignant commun, groupes en conflit)` de séances qui peuvent coexister.

        `i < j`, les semaines actives se croisent et les deux séances
        partagent une personne. Les paires de séances verrouillées dispensées
        par `tolerer_violations_heritees` sont omises.
        """
        par_enseignant: dict[int, list[int]] = defaultdict(list)
        par_groupe: dict[int, list[int]] = defaultdict(list)
        for seance in self.seances:
            for enseignant in self.regles.enseignants_reels(seance.besoin):
                par_enseignant[enseignant].append(seance.index)
            for groupe in seance.besoin.groupes:
                par_groupe[groupe].append(seance.index)
        paires: list[tuple[int, int, bool, bool]] = []
        for a in self.seances:
            voisins: set[int] = set()
            for enseignant in self.regles.enseignants_reels(a.besoin):
                voisins.update(par_enseignant[enseignant])
            for groupe in self.regles.groupes_lies(a.besoin):
                voisins.update(par_groupe.get(groupe, ()))
            for j in sorted(voisins):
                b = self.seances[j]
                if j <= a.index or not a.semaines & b.semaines or self.dispensees(a.index, j):
                    continue
                enseignant, groupe = self.regles.partage(a.besoin, b.besoin)
                paires.append((a.index, j, enseignant, groupe))
        return paires

    def ensembles_par_semaines(
        self, indices: Iterable[int], isoler: bool = True
    ) -> list[list[int]]:
        """Découpe des séances en ensembles maximaux actifs une même semaine.

        C'est ici que les semaines actives sont traitées exactement : deux
        séances sont dans un même ensemble si et seulement si une semaine au
        moins les réunit. Une contrainte posée sur chaque ensemble ne lie donc
        jamais deux séances aux semaines disjointes, et n'oublie aucune paire
        de séances qui coexistent.

        Avec `isoler`, une séance verrouillée « isolée » (violations héritées
        tolérées) n'est réunie qu'aux séances non verrouillées.
        """
        par_masque: dict[int, list[int]] = defaultdict(list)
        for index in indices:
            par_masque[self.seances[index].semaines].append(index)
        if len(par_masque) <= 1:
            ensembles = [sorted(membres) for membres in par_masque.values()]
        else:
            tous = 0
            for masque in par_masque:
                tous |= masque
            signatures: set[frozenset[int]] = set()
            bit = 0
            while tous >> bit:
                if tous >> bit & 1:
                    signatures.add(frozenset(m for m in par_masque if m >> bit & 1))
                bit += 1
            maximales = [s for s in signatures if not any(s < autre for autre in signatures)]
            ensembles = sorted(sorted(i for m in s for i in par_masque[m]) for s in maximales)
        if not isoler or not self.isolees:
            return ensembles
        resultat = []
        for ensemble in ensembles:
            resultat.append([i for i in ensemble if i not in self.isolees])
            libres = [i for i in ensemble if not self.seances[i].verrouillee]
            resultat.extend(libres + [i] for i in ensemble if i in self.isolees)
        return resultat

    def dispensees(self, i: int, j: int) -> bool:
        """Vrai si la paire est dispensée des contraintes de paire (verrous tolérés)."""
        return (
            self.tolerer
            and self.seances[i].verrouillee
            and self.seances[j].verrouillee
            and (i in self.isolees or j in self.isolees)
        )
