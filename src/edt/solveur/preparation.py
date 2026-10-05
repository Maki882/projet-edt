"""Préparation d'un calcul : les domaines de chaque séance, avant tout modèle.

Pour chaque séance de l'instantané, on calcule ici, en Python pur :

- ses **débuts admissibles** : couples (jour, heure) permis par les verrous,
  les jours et heures imposés, la grille (`HORAIRES`, `JOURS`), le déjeuner
  (`DEJEUNER`) et les indisponibilités récurrentes de ses enseignants et de
  ses groupes (`DISPO_ENSEIGNANT`, `DISPO_GROUPE`) ;
- ses **salles admissibles** : celles qui satisfont `TYPE_SALLE`, `CAPACITE`,
  `EQUIPEMENT` et `ACCESSIBILITE`, moins celles qui sont indisponibles à tous
  ses débuts (`DISPO_SALLE`) ; quand une salle n'est indisponible qu'à
  certains débuts, la liste de ces débuts est gardée pour le modèle.

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
  demi-journée et dans les heures d'ouverture, et évite le déjeuner.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from edt.domaine.contraintes import CATALOGUE, CodeContrainte
from edt.domaine.enumerations import JOURS, CibleIndisponibilite, Gravite
from edt.domaine.heures import MINUTES_PAR_JOUR, duree_vers_texte, se_chevauchent
from edt.domaine.instantane import Besoin, Instantane, Placement, SeanceAPlacer
from edt.erreurs import ErreurIntrouvable
from edt.solveur.regles import Regles, enumerer, nom_creneau, nom_seance
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
    - `debuts_interdits` : par salle, les débuts où elle est indisponible.
    """

    index: int
    seance: SeanceAPlacer
    duree: int
    verrouillee: bool
    semaines: int
    debuts: tuple[int, ...] = ()
    salles: tuple[int, ...] = ()
    debuts_interdits: dict[int, frozenset[int]] = field(default_factory=dict)

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
    """Domaines communs à toutes les séances sans verrou d'un même besoin."""

    debuts: tuple[int, ...] = ()
    salles: tuple[int, ...] = ()
    debuts_interdits: dict[int, frozenset[int]] = field(default_factory=dict)
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
      dispensées des contraintes de paire face aux autres séances verrouillées.
    """

    def __init__(
        self,
        instantane: Instantane,
        contraintes_desactivees: Iterable[str] = (),
        tolerer_violations_heritees: bool = False,
    ) -> None:
        self.instantane = instantane
        self.regles = Regles(instantane, contraintes_desactivees)
        self.tolerer = tolerer_violations_heritees
        parametres = instantane.parametres
        marge = max(
            [parametres.pause_min, parametres.trajet_defaut_min]
            + [trajet.duree for trajet in instantane.trajets]
        )
        self.pas = MINUTES_PAR_JOUR + marge + 1
        self.anomalies: list[Anomalie] = []
        self.conflits_verrous: list[Anomalie] = []
        self._domaines: dict[int, _Domaine] = {}
        self.seances = [
            self._preparer(index, seance) for index, seance in enumerate(instantane.seances)
        ]
        for besoin_id, domaine in self._domaines.items():
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
        domaine = self._domaines.get(seance.besoin.id)
        if domaine is None:
            domaine = self._domaine_libre(seance)
            self._domaines[seance.besoin.id] = domaine
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
        )

    def _domaine_libre(self, seance: SeanceAPlacer) -> _Domaine:
        """Domaines d'une séance sans verrou (voir les règles en tête de module).

        Ils ne dépendent que du besoin : les indisponibilités récurrentes
        valent toutes les semaines, quelles que soient les semaines actives.
        """
        besoin = seance.besoin
        debuts, elimines, examines = self._debuts_libres(seance)
        if not debuts:
            return _Domaine(probleme=self._probleme_de_debuts(seance, elimines, examines))

        compatibles = self.regles.salles_compatibles(besoin)
        if not compatibles.salles:
            return _Domaine(debuts=debuts, probleme=self._probleme_de_salles(besoin))
        salles, interdits = self._filtrer_salles_disponibles(
            besoin.duree, debuts, [salle.id for salle in compatibles.salles]
        )
        if not salles:
            return _Domaine(
                debuts=debuts,
                probleme=(
                    C.DISPO_SALLE,
                    f"aucune salle n'est libre : les {len(compatibles.salles)} salle(s) qui "
                    "conviennent sont indisponibles à tous les horaires possibles de la séance.",
                ),
            )
        if len(salles) == 1 and interdits:
            # Une seule salle : ses indisponibilités réduisent directement les débuts.
            debuts = tuple(d for d in debuts if d not in interdits[salles[0]])
            interdits = {}
        return _Domaine(debuts=debuts, salles=salles, debuts_interdits=interdits)

    def _debuts_libres(
        self, seance: SeanceAPlacer
    ) -> tuple[tuple[int, ...], dict[tuple[CodeContrainte, str], int], int]:
        """Débuts admissibles d'une séance sans verrou.

        Renvoie `(débuts codés, éliminations, nombre de débuts examinés)` ;
        les éliminations comptent les débuts écartés par chaque raison.
        """
        regles, besoin = self.regles, seance.besoin
        imposee = besoin.debut_impose is not None or besoin.ponctuel
        elimines: dict[tuple[CodeContrainte, str], int] = defaultdict(int)

        jour_impose = seance.jour_impose
        if jour_impose is not None:
            jours = [jour_impose]
            if (
                not imposee
                and regles.active(C.JOURS)
                and not regles.jour_permis(jour_impose, besoin)
            ):
                elimines[
                    (C.JOURS, f"le {JOURS[jour_impose]} imposé n'est pas un jour de cours permis")
                ] += 1
                return (), elimines, 1
        else:
            jours = [
                jour
                for jour in range(7)
                if not regles.active(C.JOURS) or regles.jour_permis(jour, besoin)
            ]

        plages_par_jour = defaultdict(list)
        for code, nom, plage in regles.recurrentes_des_personnes(besoin):
            plages_par_jour[plage.jour].append((code, nom, plage))

        debuts: list[int] = []
        examines = 0
        for jour in jours:
            for minute in regles.debuts_a_examiner(besoin, jour):
                examines += 1
                refus = regles.refus_horaire(besoin, besoin.duree, jour, minute)
                if refus is None:
                    refus = next(
                        (
                            (code, f"une indisponibilité de {nom}")
                            for code, nom, plage in plages_par_jour.get(jour, ())
                            if se_chevauchent(minute, minute + besoin.duree, plage.debut, plage.fin)
                        ),
                        None,
                    )
                if refus is not None:
                    elimines[refus] += 1
                    continue
                debuts.append(self.coder(jour, minute))
        return tuple(sorted(debuts)), elimines, examines

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
                "pour cette séance.",
            )
        (code, _), _ = max(elimines.items(), key=lambda item: item[1])
        details = enumerer(
            (f"{nombre} par {raison}" for (_, raison), nombre in elimines.items()), maximum=6
        )
        return (
            code,
            f"aucun horaire possible : {examines} début(s) examiné(s), tous écartés ({details}).",
        )

    def _probleme_de_salles(self, besoin: Besoin) -> tuple[CodeContrainte, str]:
        """Explication d'un domaine de salles vide : quelle exigence élimine les salles."""
        total = len(self.instantane.salles)
        exigences = self.regles.salles_compatibles(besoin).exigences
        if total == 0 or not exigences:
            return C.TYPE_SALLE, "aucune salle n'est enregistrée pour ce semestre."
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
            f"aucune salle ne convient. Sur {total} salle(s) : {details} ; {conclusion}.",
        )

    def _filtrer_salles_disponibles(
        self, duree: int, debuts: tuple[int, ...], salles: list[int]
    ) -> tuple[tuple[int, ...], dict[int, frozenset[int]]]:
        """Retire les salles indisponibles à tous les débuts ; garde les débuts interdits des autres.

        Sans effet si `DISPO_SALLE` est désactivée.
        """
        if not self.regles.active(C.DISPO_SALLE):
            return tuple(salles), {}
        gardees: list[int] = []
        interdits: dict[int, frozenset[int]] = {}
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
            if len(bloques) < len(debuts):
                gardees.append(salle_id)
                if bloques:
                    interdits[salle_id] = frozenset(bloques)
        return tuple(gardees), interdits

    # -- séances verrouillées ---------------------------------------------------

    def _preparer_verrouillee(
        self, index: int, seance: SeanceAPlacer, verrou: Placement, semaines: int
    ) -> SeancePreparee:
        """Séance fixée par son verrou : on vérifie que le verrou est tenable seul."""
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
                Anomalie(code, Gravite.ERREUR, message, seances=(seance.id,), **cibles)
            )

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
        if regles.active(C.HORAIRES):
            ecarts = []
            if preparee.duree != besoin.duree:
                ecarts.append(
                    f"dure {duree_vers_texte(preparee.duree)} au lieu des "
                    f"{duree_vers_texte(besoin.duree)} demandées"
                )
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
        for code, nom, plage in regles.recurrentes_des_personnes(besoin):
            if plage.jour == verrou.jour and se_chevauchent(
                verrou.debut, verrou.fin, plage.debut, plage.fin
            ):
                valide = False
                refuser(
                    code,
                    f"{designation} tombe pendant une indisponibilité de {nom}.",
                    jour=verrou.jour,
                )
        if not valide:
            return preparee
        preparee.debuts = (self.coder(verrou.jour, verrou.debut),)

        if verrou.salle_id is None:
            compatibles = [salle.id for salle in regles.salles_compatibles(besoin).salles]
            if not compatibles:
                code, message = self._probleme_de_salles(besoin)
                refuser(code, f"{designation} n'a pas de salle : {message}")
                return preparee
            salles, _ = self._filtrer_salles_disponibles(
                preparee.duree, preparee.debuts, compatibles
            )
            if not salles:
                refuser(
                    C.DISPO_SALLE,
                    f"{designation} n'a pas de salle : toutes les salles qui conviennent sont "
                    "indisponibles à cet horaire.",
                    jour=verrou.jour,
                )
            preparee.salles = salles
            return preparee

        try:
            salle = self.instantane.salle(verrou.salle_id)
        except ErreurIntrouvable:
            refuser(
                C.VERROU,
                f"{designation} désigne une salle (n° {verrou.salle_id}) qui n'existe pas "
                "dans ce semestre.",
            )
            return preparee
        defauts = [
            (code, libelle)
            for code, libelle, test in regles.exigences_de_salle(besoin)
            if not test(salle)
        ]
        for code, libelle in defauts:
            refuser(
                code,
                f"{designation} est verrouillée en salle {salle.code}, qui ne fait pas partie "
                f"des salles {libelle}. Changer la salle du verrou ou désactiver la contrainte "
                f"« {CATALOGUE[code].libelle} ».",
                salles=(salle.id,),
                jour=verrou.jour,
            )
        salles, _ = self._filtrer_salles_disponibles(preparee.duree, preparee.debuts, [salle.id])
        if not salles:
            refuser(
                C.DISPO_SALLE,
                f"{designation} est verrouillée en salle {salle.code}, indisponible à cet horaire.",
                salles=(salle.id,),
                jour=verrou.jour,
            )
        if not defauts:
            preparee.salles = salles
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
        """Paires de séances à horaire fixe qui violent déjà entre elles une contrainte.

        Deux séances verrouillées : `conflits_verrous` (violation héritée, que
        l'utilisateur peut tolérer). Sinon l'une au moins tient son horaire de
        son besoin (jour et heure imposés) : l'impossibilité est certaine.
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
                    codes = self.regles.codes_violes(
                        a.besoin, debut_a, fin_a, salle_a, b.besoin, debut_b, fin_b, salle_b
                    )
                    if not codes:
                        continue
                    violees = enumerer(f"« {CATALOGUE[code].libelle} »" for code in codes)
                    cibles = {"seances": (a.seance.id, b.seance.id), "jour": jour}
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
                                f"{nom_creneau(jour, debut_b, fin_b)}, ont des horaires fixes "
                                f"(verrou ou créneau imposé) qui se contredisent ({violees}).",
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
