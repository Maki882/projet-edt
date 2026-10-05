"""Génération d'un jeu de données fictif complet dans une base initialisée.

`generer_jeu(session, taille, graine)` peuple une base créée par
`edt.db.initialisation.initialiser_base` (dont il réutilise la grille, les
types de salle, les équipements, les types de séance, les paramètres,
l'année scolaire, ses semestres et ses jours fériés) avec un établissement
fictif réaliste, puis renvoie un `RapportJeu`.

Déterminisme : un seul générateur `random.Random(graine)` sert à tous les
tirages, Faker est initialisé avec la graine, aucune donnée ne dépend de
l'horloge, l'ordre des insertions est fixe. Même taille et même graine
donnent exactement les mêmes lignes.

Faisabilité : les besoins sont placés au fur et à mesure dans un emploi du
temps témoin (`edt.synthese.temoin`) ; un besoin dont une séance ne trouve
pas de place n'est pas créé (il est compté et expliqué dans le rapport).
Les indisponibilités et fermetures sont posées pour ne jamais contredire le
témoin : celles qui contraignent le placement (vacataires, sport) sont
décidées avant et respectées par le témoin ; les autres (indisponibilités
datées, salles réservées aux partiels, maxima horaires) sont posées après,
là où le témoin laisse la place.

Étapes (chacune journalisée) : lieux, groupes et étudiants, calendrier,
indisponibilités des groupes, matières et besoins en projet, vivier
d'enseignants, témoin semestre par semestre, enseignants, besoins,
contrôle du dépliage, indisponibilités posées après coup, version de
référence (en option), rapport.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import random
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from faker import Faker
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.db.initialisation import assurer_annee_scolaire
from edt.domaine.calendrier import Semaine, lundi_de, semaine_de, semaines_entre
from edt.domaine.depliage import deplier, semaines_ouvertes
from edt.domaine.enumerations import (
    Accessibilite,
    CibleIndisponibilite,
    ModeVolume,
    NatureGroupe,
    NatureJourFerme,
    OrigineVersion,
    StatutEnseignant,
)
from edt.domaine.instantane import Besoin as BesoinDomaine
from edt.domaine.instantane import JourFerme as JourFermeDomaine
from edt.domaine.instantane import Placement, id_seance
from edt.domaine.parametres import Parametres
from edt.erreurs import ErreurDonneesInvalides, ErreurEDT, ErreurRegleMetier
from edt.services.conflits_groupes import calculer_conflits_groupes
from edt.services.instantane import charger_instantane
from edt.services.parametres import lire_parametres
from edt.synthese import catalogue as cat
from edt.synthese.configuration import ConfigurationJeu, configuration_nommee
from edt.synthese.rapport import (
    BesoinEcarte,
    PlaceTemoin,
    RapportJeu,
    SeancePlacee,
    calculer_charge,
)
from edt.synthese.temoin import (
    CreneauGrille,
    Demande,
    EnseignantTemoin,
    Grille,
    Resultat,
    SalleTemoin,
    Temoin,
    Vivier,
)
from edt.verification import Indicateurs, calculer_indicateurs, verifier

journal = logging.getLogger(__name__)

H = 60
JOURNEE = (0, 24 * H)
APRES_MIDI = (13 * H + 45, 18 * H)
PLAFONDS: dict[StatutEnseignant, int] = {
    StatutEnseignant.PERMANENT: 15 * H,
    StatutEnseignant.VACATAIRE: 8 * H,
    StatutEnseignant.EXTERIEUR: 6 * H,
}
"""Plafond hebdomadaire d'enseignement visé par statut, pour le choix des enseignants du témoin."""
SERVICE_VISE = 11 * H
"""Service hebdomadaire moyen visé pour dimensionner le vivier initial."""
TYPES_SEANCE_REQUIS = ("CM", "TD", "TP", "PROJET", "LANGUE", "DST", "EXAMEN", "AUTRE")
SEMAINES_CONFERENCE = tuple(range(3, 15))
"""Semaines où peut se tenir la conférence métiers d'une promotion (la date est choisie par le témoin)."""


def generer_jeu(
    session: Session,
    taille: str | ConfigurationJeu = "petit",
    graine: int = 0,
    annee_debut: int | None = None,
    enregistrer_reference: bool = False,
) -> RapportJeu:
    """Peuple une base initialisée avec un établissement fictif et renvoie le rapport.

    - `taille` : « petit », « moyen », « grand » ou une `ConfigurationJeu` sur mesure ;
    - `graine` : graine de tous les tirages (même graine = mêmes données) ;
    - `annee_debut` : année scolaire visée (par exemple 2026 pour 2026-2027),
      créée si besoin ; par défaut la plus récente de la base ;
    - `enregistrer_reference` : enregistre aussi le témoin comme version
      d'emploi du temps « référence » (non active) de chaque semestre.

    La base doit être initialisée (`initialiser_base`) et vide de
    référentiels : sinon `ErreurRegleMetier`. Ne valide pas la transaction
    (`session.flush()` seulement) : c'est l'appelant qui valide.
    """
    configuration = taille if isinstance(taille, ConfigurationJeu) else configuration_nommee(taille)
    debut = time.perf_counter()
    journal.info("Génération du jeu fictif « %s » (graine %d) : début", configuration.nom, graine)
    generateur = _Generateur(session, configuration, graine)
    rapport = generateur.executer(annee_debut, enregistrer_reference, debut)
    journal.info(
        "Génération du jeu fictif « %s » terminée en %.1f s : %d besoins, %d écartés",
        configuration.nom,
        rapport.duree_s,
        rapport.nb_besoins,
        len(rapport.besoins_ecartes),
    )
    return rapport


# --------------------------------------------------------------------------- #
# Structures de travail
# --------------------------------------------------------------------------- #


@dataclass
class _Classe:
    groupe: m.Groupe
    apprentis: bool
    demi_groupes: list[m.Groupe]


@dataclass
class _Promotion:
    annee: int
    site: str
    groupe: m.Groupe
    classes: list[_Classe] = field(default_factory=list)
    anglais: list[m.Groupe] = field(default_factory=list)
    lv2: list[tuple[cat.Langue, m.Groupe]] = field(default_factory=list)
    parcours: list[tuple[cat.Parcours, m.Groupe]] = field(default_factory=list)
    option: m.Groupe | None = None
    tiers_temps: m.Groupe | None = None

    @property
    def code(self) -> str:
        return self.groupe.code

    def classes_de(self, filiere: str) -> list[_Classe]:
        """Classes de la filière : « classique », « apprentis » ou « commun » (toutes)."""
        if filiere == "commun":
            return list(self.classes)
        return [c for c in self.classes if c.apprentis == (filiere == "apprentis")]

    @property
    def rattaches(self) -> list[m.Groupe]:
        """Groupes sans parent rattachés à la promotion (transverses, option, tiers-temps)."""
        groupes = list(self.anglais) + [g for _, g in self.lv2] + [g for _, g in self.parcours]
        groupes += [g for g in (self.option, self.tiers_temps) if g is not None]
        return groupes


@dataclass
class _Spec:
    """Besoin en projet : créé en base seulement si le témoin place toutes ses séances."""

    cle: int
    semestre: str
    promotion: _Promotion
    matiere: m.Matiere
    type_seance: str
    groupes: tuple[m.Groupe, ...]
    duree: int
    profil: str
    priorite: int
    mode: ModeVolume = ModeVolume.PAR_SEMAINE
    nombre: int = 1
    max_par_semaine: int = 1
    date_debut: dt.date | None = None
    date_fin: dt.date | None = None
    date_ponctuelle: dt.date | None = None
    dates_possibles: tuple[dt.date, ...] = ()
    jour_impose: int | None = None
    debut_impose: int | None = None
    campus_impose: str | None = None
    salle_imposee: m.Salle | None = None
    discipline: str | None = None
    nb_enseignants: int = 0
    enseignants_fixes: tuple[EnseignantTemoin, ...] | None = None
    libelle: str | None = None
    commentaire: str | None = None
    barrette: str | None = None

    @property
    def effectif(self) -> int:
        return sum(g.effectif for g in self.groupes)

    @property
    def description(self) -> str:
        base = self.libelle or f"{self.type_seance} {self.matiere.libelle}"
        return f"{self.matiere.code} {base} - {', '.join(g.code for g in self.groupes)}"


@dataclass
class _Eleve:
    """Étudiant fictif en cours de répartition dans ses groupes."""

    classe: int
    demi_groupe: int
    apprenti: bool
    groupes: list[int] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Générateur
# --------------------------------------------------------------------------- #


class _Generateur:
    def __init__(self, session: Session, config: ConfigurationJeu, graine: int) -> None:
        self.session = session
        self.config = config
        self.graine = graine
        self.rng = random.Random(graine)
        self.faker = Faker("fr_FR")
        self.faker.seed_instance(graine)
        self._noms_pris: set[tuple[str | None, str]] = set()
        self.promotions: list[_Promotion] = []
        self.specs: list[_Spec] = []
        self.ecartes: list[BesoinEcarte] = []
        self.resultats: dict[int, Resultat] = {}

    # -- orchestration ---------------------------------------------------------

    def executer(
        self, annee_debut: int | None, enregistrer_reference: bool, debut: float
    ) -> RapportJeu:
        self._verifier_base()
        self._charger_referentiels(annee_debut)
        self._creer_lieux()
        self._creer_groupes()
        self._calculer_conflits()
        self._creer_calendrier()
        self._creer_indisponibilites_groupes()
        self._preparer_besoins()
        self._constituer_vivier()
        for code in self.config.semestres:
            self._placer_semestre(code)
        self._creer_enseignants()
        self._creer_besoins()
        self._controler_depliage()
        self._poser_indisponibilites_apres_temoin()
        versions = self._enregistrer_references() if enregistrer_reference else {}
        self.session.flush()
        anomalies, indicateurs = self._verifier_temoin()
        return self._rapport(versions, anomalies, indicateurs, time.perf_counter() - debut)

    # -- référentiels -----------------------------------------------------------

    def _verifier_base(self) -> None:
        for modele in (m.Campus, m.Salle, m.Groupe, m.Enseignant, m.Matiere, m.Besoin, m.Etudiant):
            nombre = self.session.scalar(select(func.count()).select_from(modele))
            if nombre:
                raise ErreurRegleMetier(
                    f"La base contient déjà des données ({nombre} ligne(s) dans la table "
                    f"« {modele.__tablename__} ») : la réinitialiser avant de charger un jeu "
                    "fictif (« python -m edt init-db --reset »)."
                )

    def _charger_referentiels(self, annee_debut: int | None) -> None:
        s = self.session
        if annee_debut is not None:
            self.annee = assurer_annee_scolaire(s, annee_debut)
        else:
            self.annee = s.scalar(
                select(m.AnneeScolaire).order_by(m.AnneeScolaire.date_debut.desc()).limit(1)
            )
            if self.annee is None:
                raise ErreurDonneesInvalides(
                    "Aucune année scolaire en base : initialiser la base avant de générer un jeu."
                )
        self.semestres = {sem.code: sem for sem in self.annee.semestres}
        manquants = [code for code in self.config.semestres if code not in self.semestres]
        if manquants:
            raise ErreurDonneesInvalides(
                f"L'année {self.annee.libelle} n'a pas de semestre {', '.join(manquants)}."
            )
        self.semaines: dict[str, tuple[Semaine, ...]] = {
            code: semaines_entre(sem.date_debut, sem.date_fin)
            for code, sem in self.semestres.items()
        }
        self.parametres: Parametres = lire_parametres(s)
        creneaux = list(s.scalars(select(m.Creneau).order_by(m.Creneau.jour, m.Creneau.debut_min)))
        if not creneaux:
            raise ErreurDonneesInvalides("La grille horaire est vide : initialiser la base.")
        self.grille = Grille(
            (CreneauGrille(c.jour, c.debut_min, c.fin_min, c.demi_journee) for c in creneaux),
            self.parametres,
        )
        self.types_seance = {t.code: t for t in s.scalars(select(m.TypeSeance))}
        absents = [code for code in TYPES_SEANCE_REQUIS if code not in self.types_seance]
        if absents:
            raise ErreurDonneesInvalides(
                f"Types de séance absents de la base : {', '.join(absents)} (initialiser la base)."
            )
        self.types_salle = {t.code: t for t in s.scalars(select(m.TypeSalle))}
        for code, libelle in cat.TYPES_SALLE_AJOUTES:
            if code not in self.types_salle:
                self.types_salle[code] = m.TypeSalle(code=code, libelle=libelle)
                s.add(self.types_salle[code])
        self.equipements = {e.code: e for e in s.scalars(select(m.Equipement))}
        requis = {e for genre in cat.GENRES_SALLE.values() for e in genre.equipements}
        requis |= {genre.option for genre in cat.GENRES_SALLE.values() if genre.option}
        if requis - set(self.equipements):
            raise ErreurDonneesInvalides(
                f"Équipements absents de la base : {', '.join(sorted(requis - set(self.equipements)))}."
            )
        s.flush()
        journal.info(
            "Référentiels chargés : année %s, semestres %s, %d créneaux",
            self.annee.libelle,
            ", ".join(self.config.semestres),
            len(creneaux),
        )

    # -- lieux ------------------------------------------------------------------

    def _creer_lieux(self) -> None:
        s = self.session
        self.campus: dict[str, m.Campus] = {}
        for code in self.config.sites:
            site = cat.SITES[code]
            self.campus[code] = m.Campus(code=code, nom=site.nom, prefixe_salle=site.prefixe)
            s.add(self.campus[code])
        s.flush()
        self.code_campus = {c.id: code for code, c in self.campus.items()}
        self.trajets: dict[tuple[int, int], int] = {}
        codes = list(self.config.sites)
        for i, code_a in enumerate(codes):
            for code_b in codes[i + 1 :]:
                duree = cat.TRAJETS.get((code_a, code_b), cat.TRAJETS.get((code_b, code_a)))
                if duree is None:
                    raise ErreurDonneesInvalides(f"Trajet {code_a}-{code_b} absent du catalogue.")
                a, b = sorted((self.campus[code_a].id, self.campus[code_b].id))
                s.add(m.Trajet(campus_a_id=a, campus_b_id=b, duree_min=duree))
                self.trajets[(a, b)] = duree

        self.salles: list[m.Salle] = []
        for code_site, salles_site in self.config.salles:
            site = cat.SITES[code_site]
            numeros: Counter[int] = Counter()
            for cle_genre, genre in cat.GENRES_SALLE.items():
                if cle_genre == "amphis":
                    capacites = list(salles_site.amphis)
                else:
                    nombre = getattr(salles_site, cle_genre)
                    capacites = [self.rng.choice(genre.capacites) for _ in range(nombre)]
                for rang, capacite in enumerate(capacites):
                    etages = cat.ETAGES_STAN if code_site == "STAN" else genre.etages
                    etage = etages[rang % len(etages)]
                    numeros[etage] += 1
                    equipements = list(genre.equipements)
                    if genre.option and rang % 3 == 0:
                        equipements.append(genre.option)
                    if rang == 0:
                        accessibilite = Accessibilite.OUI
                    else:
                        accessibilite = self.rng.choices(
                            (Accessibilite.OUI, Accessibilite.NON, Accessibilite.INCONNU),
                            weights=(55, 25, 20),
                        )[0]
                    salle = m.Salle(
                        code=f"{site.prefixe}{etage}{numeros[etage]:02d}",
                        campus=self.campus[code_site],
                        batiment=site.batiments[rang % len(site.batiments)],
                        etage=etage,
                        capacite=capacite,
                        type_salle=self.types_salle[genre.type_salle],
                        accessibilite=accessibilite,
                    )
                    salle.equipements = [self.equipements[e] for e in equipements]
                    self.salles.append(salle)
        s.add_all(self.salles)
        s.flush()
        accepte_inconnu = self.parametres.accessibilite_inconnue_acceptee
        self.salles_temoin = [
            SalleTemoin(
                id=salle.id,
                code=salle.code,
                campus_id=salle.campus_id,
                capacite=salle.capacite,
                type_code=salle.type_salle.code,
                equipements=frozenset(e.code for e in salle.equipements),
                accessible=salle.accessibilite is Accessibilite.OUI
                or (accepte_inconnu and salle.accessibilite is Accessibilite.INCONNU),
            )
            for salle in self.salles
        ]
        journal.info(
            "Lieux créés : %d sites, %d trajets, %d salles",
            len(self.campus),
            len(self.trajets),
            len(self.salles),
        )

    def _site_de(self, annee: int) -> str:
        site = cat.CAMPUS_PROMOTION[annee]
        return site if site in self.campus else self.config.sites[0]

    # -- groupes et étudiants ---------------------------------------------------

    def _creer_groupes(self) -> None:
        s = self.session
        annee_id = self.annee.id
        self.eleves: list[_Eleve] = []
        tous: list[m.Groupe] = []

        def groupe(code: str, libelle: str, nature: NatureGroupe, parent=None) -> m.Groupe:
            g = m.Groupe(
                annee_scolaire_id=annee_id,
                code=code,
                libelle=libelle,
                nature=nature,
                parent=parent,
                effectif=0,
                accessibilite_requise=False,
            )
            tous.append(g)
            return g

        for config_promotion in sorted(self.config.promotions, key=lambda p: p.annee):
            a = config_promotion.annee
            cycle = "cycle intégré" if a <= 2 else "cycle ingénieur"
            ordinal = "1re" if a == 1 else f"{a}e"
            promotion = _Promotion(
                a,
                self._site_de(a),
                groupe(f"A{a}", f"{ordinal} année ({cycle})", NatureGroupe.PROMOTION),
            )
            debut_eleves = len(self.eleves)
            specs_classes = [(False, i + 1) for i in range(config_promotion.nb_classes)]
            specs_classes += [(True, i + 1) for i in range(config_promotion.nb_classes_apprentis)]
            for index, (apprentis, numero) in enumerate(specs_classes):
                bornes = (
                    self.config.effectif_classe_apprentis
                    if apprentis
                    else self.config.effectif_classe
                )
                taille = self.rng.randint(*bornes)
                code = f"A{a}-APP{numero}" if apprentis else f"A{a}-G{numero}"
                libelle = (
                    f"Classe d'apprentis {numero} ({ordinal} année)"
                    if apprentis
                    else f"Groupe {numero} ({ordinal} année)"
                )
                classe = groupe(code, libelle, NatureGroupe.CLASSE, promotion.groupe)
                demi = [
                    groupe(
                        f"{code}{lettre}",
                        f"{libelle}, demi-groupe {lettre.upper()}",
                        NatureGroupe.DEMI_GROUPE,
                        classe,
                    )
                    for lettre in "ab"
                ]
                promotion.classes.append(_Classe(classe, apprentis, demi))
                moitie = (taille + 1) // 2
                for rang in range(taille):
                    self.eleves.append(_Eleve(index, 0 if rang < moitie else 1, apprentis))
            eleves = self.eleves[debut_eleves:]
            classiques = [e for e in eleves if not e.apprenti]
            nb_classiques = len(classiques)

            def repartir(liste: list[_Eleve], nb: int) -> list[list[_Eleve]]:
                melange = list(liste)
                self.rng.shuffle(melange)
                return [melange[i::nb] for i in range(nb)]

            nb_niveaux = max(1, round(nb_classiques / cat.TAILLE_GROUPE_TRANSVERSE))
            for niveau, membres in enumerate(repartir(classiques, nb_niveaux), start=1):
                g = groupe(
                    f"A{a}-ANG{niveau}",
                    f"Anglais niveau {niveau} ({ordinal} année)",
                    NatureGroupe.TRANSVERSE,
                )
                promotion.anglais.append(g)
                for e in membres:
                    e.groupes.append(len(tous) - 1)
            if a in cat.ANNEES_LV2:
                melange = list(classiques)
                self.rng.shuffle(melange)
                debut = 0
                for rang, langue in enumerate(cat.LANGUES_LV2):
                    if rang == len(cat.LANGUES_LV2) - 1:
                        part = melange[debut:]
                    else:
                        part = melange[debut : debut + round(langue.part * nb_classiques)]
                    debut += len(part)
                    if not part:
                        continue
                    nb = math.ceil(len(part) / cat.TAILLE_GROUPE_TRANSVERSE)
                    for numero in range(1, nb + 1):
                        g = groupe(
                            f"A{a}-{langue.code}{numero}",
                            f"{langue.libelle} LV2 groupe {numero} ({ordinal} année)",
                            NatureGroupe.TRANSVERSE,
                        )
                        promotion.lv2.append((langue, g))
                        for e in part[numero - 1 :: nb]:
                            e.groupes.append(len(tous) - 1)
            if a in cat.ANNEES_PARCOURS:
                nb_parcours = min(
                    len(cat.PARCOURS), max(2, round(nb_classiques / cat.TAILLE_GROUPE_TRANSVERSE))
                )
                for parcours, membres in zip(
                    cat.PARCOURS, repartir(classiques, nb_parcours), strict=False
                ):
                    g = groupe(
                        f"A{a}-P-{parcours.code}",
                        f"Parcours {parcours.libelle} (A{a})",
                        NatureGroupe.TRANSVERSE,
                    )
                    promotion.parcours.append((parcours, g))
                    for e in membres:
                        e.groupes.append(len(tous) - 1)
            if a == max(cat.ANNEES_PARCOURS):
                promotion.option = groupe(
                    f"A{a}-ENTR",
                    f"Option entrepreneuriat (A{a}, liste des étudiants non transmise)",
                    NatureGroupe.TRANSVERSE,
                )
                promotion.option.effectif = max(12, round(0.12 * nb_classiques))
            promotion.tiers_temps = groupe(
                f"A{a}-TT", f"Tiers-temps aux épreuves ({ordinal} année)", NatureGroupe.TIERS_TEMPS
            )
            promotion.tiers_temps.effectif = max(
                2, round(self.config.part_tiers_temps * len(eleves))
            )
            # Effectifs : un demi-groupe compte ses étudiants, une classe ses demi-groupes...
            for e in eleves:
                classe = promotion.classes[e.classe]
                e.groupes.insert(0, tous.index(classe.demi_groupes[e.demi_groupe]))
            self.promotions.append(promotion)

        for e in self.eleves:
            for indice in e.groupes:
                tous[indice].effectif += 1
        for promotion in self.promotions:
            for classe in promotion.classes:
                classe.groupe.effectif = sum(d.effectif for d in classe.demi_groupes)
            promotion.groupe.effectif = sum(c.groupe.effectif for c in promotion.classes)
        self._marquer_pmr()
        s.add_all(tous)
        s.flush()
        self.groupes = tous
        self.parents = {g.id: g.parent_id for g in tous}

        self.inscriptions: list[tuple[int, int]] = []
        self.chevauchements: list[tuple[int, int]] = []
        if self.config.etudiants:
            self._creer_etudiants()
        else:
            for promotion in self.promotions:
                for g in promotion.rattaches:
                    if g is not promotion.tiers_temps and g is not promotion.option:
                        self.chevauchements.append(tuple(sorted((g.id, promotion.groupe.id))))
        for promotion in self.promotions:
            if promotion.option is not None:
                self.chevauchements.append(
                    tuple(sorted((promotion.option.id, promotion.groupe.id)))
                )
        if self.chevauchements:
            s.execute(
                insert(m.groupe_chevauchement),
                [{"groupe_a_id": a, "groupe_b_id": b} for a, b in self.chevauchements],
            )
        journal.info(
            "Groupes créés : %d groupes (%d promotions), %d étudiants, %d inscriptions, "
            "%d chevauchement(s) déclaré(s)",
            len(tous),
            len(self.promotions),
            len(self.eleves) if self.config.etudiants else 0,
            len(self.inscriptions),
            len(self.chevauchements),
        )

    def _marquer_pmr(self) -> None:
        """Marque des demi-groupes « salle accessible requise » (un étudiant à mobilité réduite)."""
        self.groupes_pmr: list[m.Groupe] = []
        for rang in range(self.config.nb_groupes_pmr):
            promotion = self.promotions[rang % len(self.promotions)]
            candidats = [
                d
                for c in promotion.classes_de("classique")
                for d in c.demi_groupes
                if not d.accessibilite_requise
            ]
            if not candidats:
                continue
            choisi = self.rng.choice(candidats)
            choisi.accessibilite_requise = True
            self.groupes_pmr.append(choisi)

    def _creer_etudiants(self) -> None:
        etudiants = []
        for numero, eleve in enumerate(self.eleves, start=1):
            nom, prenom = self.faker.last_name(), self.faker.first_name()
            etudiant = m.Etudiant(identifiant=f"ETU{numero:05d}", nom=nom, prenom=prenom)
            etudiant.groupes = [self.groupes[i] for i in eleve.groupes]
            etudiants.append(etudiant)
            self.inscriptions.extend((numero, self.groupes[i].id) for i in eleve.groupes)
        self.session.add_all(etudiants)
        self.session.flush()

    def _calculer_conflits(self) -> None:
        conflits = calculer_conflits_groupes(self.parents, self.chevauchements, self.inscriptions)
        lies: dict[int, set[int]] = {g: {g} for g in self.parents}
        for a, b in conflits:
            lies[a].add(b)
            lies[b].add(a)
        self.lies = {g: frozenset(v) for g, v in lies.items()}
        self.pmr = frozenset(g.id for g in self.groupes if g.accessibilite_requise)
        enfants: dict[int, list[int]] = {}
        for g, parent in self.parents.items():
            if parent is not None:
                enfants.setdefault(parent, []).append(g)
        self.enfants = enfants
        journal.info("Conflits entre groupes calculés : %d paires", len(conflits))

    def _descendants(self, racine: int) -> frozenset[int]:
        vus, pile = {racine}, [racine]
        while pile:
            for enfant in self.enfants.get(pile.pop(), ()):
                if enfant not in vus:
                    vus.add(enfant)
                    pile.append(enfant)
        return frozenset(vus)

    # -- calendrier ---------------------------------------------------------------

    def _creer_calendrier(self) -> None:
        s = self.session
        y = self.annee.date_debut.year
        lignes: list[m.JourFerme] = []

        def fermer(debut, fin, libelle, nature, groupes=(None,)) -> None:
            for g in groupes:
                lignes.append(
                    m.JourFerme(
                        annee_scolaire_id=self.annee.id,
                        date_debut=debut,
                        date_fin=fin,
                        libelle=libelle,
                        nature=nature,
                        groupe=g,
                    )
                )

        vacances = (
            ("Vacances de la Toussaint", lundi_de(dt.date(y, 11, 1)), lundi_de(dt.date(y, 11, 1))),
            ("Vacances de Noël", lundi_de(dt.date(y, 12, 25)), lundi_de(dt.date(y + 1, 1, 1))),
            ("Vacances d'hiver", lundi_de(dt.date(y + 1, 2, 22)), lundi_de(dt.date(y + 1, 2, 22))),
            (
                "Vacances de printemps",
                lundi_de(dt.date(y + 1, 4, 22)),
                lundi_de(dt.date(y + 1, 4, 22)),
            ),
        )
        for libelle, premier_lundi, dernier_lundi in vacances:
            fermer(
                premier_lundi,
                dernier_lundi + dt.timedelta(days=6),
                libelle,
                NatureJourFerme.VACANCES,
            )
        s.add_all(lignes)
        s.flush()
        self._charger_fermetures()

        self.semaine_partiels: dict[tuple[int, str], Semaine] = {}
        for promotion in self.promotions:
            groupes = [promotion.groupe, *promotion.rattaches]
            if promotion.annee == 1 and "S1" in self.config.semestres:
                premiere = self.semaines["S1"][0]
                fermer(
                    premiere.lundi,
                    premiere.date_du(4),
                    "Semaine d'intégration",
                    NatureJourFerme.SEMAINE_SPECIALE,
                    groupes,
                )
            stage = promotion.annee in self.config.annees_stage and "S2" in self.config.semestres
            if stage:
                semaines = self.semaines["S2"]
                fermer(
                    semaines[-4].lundi,
                    self.semestres["S2"].date_fin,
                    "Stage en entreprise",
                    NatureJourFerme.AUTRE,
                    groupes,
                )
            if not self.config.partiels or promotion.annee < 3:
                continue
            for code in self.config.semestres:
                semaines = self.semaines[code]
                indice = len(semaines) - (5 if code == "S2" and stage else 1)
                while indice > 0 and self._semaine_fermee(semaines[indice]):
                    indice -= 1
                semaine = semaines[indice]
                self.semaine_partiels[(promotion.annee, code)] = semaine
                fermer(
                    semaine.lundi,
                    semaine.date_du(4),
                    f"Partiels du semestre {code[1]}",
                    NatureJourFerme.EXAMENS,
                    groupes,
                )
        s.add_all(lignes)
        s.flush()
        self._charger_fermetures()
        journal.info(
            "Calendrier créé : %d fermetures (vacances, intégration, partiels, stage)", len(lignes)
        )

    def _charger_fermetures(self) -> None:
        """Fermetures de l'année au format du domaine (portée de groupe étendue aux descendants)."""
        self.fermetures = tuple(
            JourFermeDomaine(
                j.date_debut,
                j.date_fin,
                j.libelle,
                j.nature,
                self._descendants(j.groupe_id) if j.groupe_id is not None else None,
            )
            for j in self.session.scalars(
                select(m.JourFerme)
                .where(m.JourFerme.annee_scolaire_id == self.annee.id)
                .order_by(m.JourFerme.date_debut, m.JourFerme.id)
            )
        )

    def _semaine_fermee(self, semaine: Semaine) -> bool:
        """Vrai si une fermeture globale couvre les cinq jours ouvrés de la semaine."""
        return all(
            any(f.groupes is None and f.concerne(semaine.date_du(j), ()) for f in self.fermetures)
            for j in range(5)
        )

    def _date_ouverte(self, jour: dt.date, groupes: Sequence[int]) -> bool:
        return not any(f.concerne(jour, groupes, ponctuelle=True) for f in self.fermetures)

    def _creer_indisponibilites_groupes(self) -> None:
        """Après-midi du mercredi libéré pour le sport (cycle intégré) : respecté par le témoin."""
        self.indispos_groupes: dict[int, list[tuple[int, int, int]]] = {}
        for promotion in self.promotions:
            if promotion.annee not in cat.ANNEES_SPORT:
                continue
            self.session.add(
                m.Indisponibilite(
                    cible=CibleIndisponibilite.GROUPE,
                    groupe_id=promotion.groupe.id,
                    jour=2,
                    debut_min=APRES_MIDI[0],
                    fin_min=APRES_MIDI[1],
                    motif=cat.MOTIF_SPORT,
                )
            )
            self.indispos_groupes[promotion.groupe.id] = [(2, *APRES_MIDI)]
        self.session.flush()

    # -- besoins en projet ------------------------------------------------------

    def _preparer_besoins(self) -> None:
        self.matieres: list[m.Matiere] = []
        for code in self.config.semestres:
            numero_semestre = int(code[1])
            for promotion in self.promotions:
                self._preparer_promotion(promotion, code, numero_semestre)
        self.session.add_all(self.matieres)
        self.session.flush()
        self._confier_aux_fictifs()
        compte = Counter(spec.semestre for spec in self.specs)
        journal.info(
            "Besoins préparés : %d matières, %s",
            len(self.matieres),
            ", ".join(f"{code} {n} besoins" for code, n in sorted(compte.items())),
        )

    def _matiere(self, promotion: _Promotion, departement: str, s: int, numero: int, libelle: str):
        matiere = m.Matiere(
            code=f"{departement}.{promotion.annee}{s}{numero:02d}",
            libelle=libelle,
            promotion=promotion.groupe,
        )
        self.matieres.append(matiere)
        return matiere

    def _ajouter(self, **champs) -> _Spec:
        spec = _Spec(cle=len(self.specs), **champs)
        self.specs.append(spec)
        return spec

    def _preparer_promotion(self, promotion: _Promotion, code: str, s: int) -> None:
        modules = cat.MODULES.get((promotion.annee, s), ())
        if self.config.modules_max is not None:
            modules = modules[: self.config.modules_max]
        matieres_partiel: dict[str, list[m.Matiere]] = {"classique": [], "apprentis": []}
        for numero, module in enumerate(modules, start=1):
            if not promotion.classes_de(module.filiere):
                continue
            cibles = {e.cible for e in module.enseignements}
            if "option" in cibles and promotion.option is None:
                continue
            if "parcours" in cibles:
                for rang, (parcours, groupe) in enumerate(promotion.parcours, start=1):
                    matiere = self._matiere(
                        promotion,
                        parcours.departement,
                        s,
                        cat.NUMERO_PARCOURS + rang,
                        f"Parcours {parcours.libelle}",
                    )
                    for indice, enseignement in enumerate(module.enseignements):
                        self._specs_enseignement(
                            promotion,
                            code,
                            module,
                            matiere,
                            enseignement,
                            [(groupe,)],
                            discipline=cat.DISCIPLINES[parcours.departement],
                            barrette=f"{promotion.code}-{code}-parcours-{indice}",
                        )
                continue
            matiere = self._matiere(promotion, module.departement, s, numero, module.libelle)
            if module.partiel:
                matieres_partiel[module.filiere].append(matiere)
            for enseignement in module.enseignements:
                self._specs_enseignement(promotion, code, module, matiere, enseignement)
        if self.config.dst and promotion.annee in cat.ANNEES_DST:
            self._preparer_dst(promotion, code, s)
        if self.config.partiels and (promotion.annee, code) in self.semaine_partiels:
            self._preparer_partiels(promotion, code, matieres_partiel)
        if self.config.evenements:
            self._preparer_evenements(promotion, code, s)

    def _specs_enseignement(
        self,
        promotion: _Promotion,
        code: str,
        module: cat.Module,
        matiere: m.Matiere,
        ens: cat.Enseignement,
        cibles: list[tuple[m.Groupe, ...]] | None = None,
        discipline: str | None = None,
        barrette: str | None = None,
    ) -> None:
        classes = promotion.classes_de(module.filiere)
        barrettes: list[str | None]
        if cibles is None:
            if ens.cible == "promotion":
                if module.filiere == "commun" or len(classes) == len(promotion.classes):
                    cibles = [(promotion.groupe,)]
                else:
                    cibles = [tuple(c.groupe for c in classes)]
            elif ens.cible == "classe":
                cibles = [(c.groupe,) for c in classes]
            elif ens.cible == "demi_groupe":
                cibles = [(d,) for c in classes for d in c.demi_groupes]
            elif ens.cible == "anglais":
                cibles = [(g,) for g in promotion.anglais]
            elif ens.cible == "lv2":
                cibles = [(g,) for _, g in promotion.lv2]
            elif ens.cible == "option":
                cibles = [(promotion.option,)]
            else:
                raise ErreurDonneesInvalides(f"Cible d'enseignement inconnue : {ens.cible!r}.")
        if ens.cible == "anglais":
            vagues = cat.VAGUES_ANGLAIS.get(promotion.annee, 1)
            barrettes = [
                f"{promotion.code}-{code}-anglais-{i * vagues // len(cibles)}"
                for i in range(len(cibles))
            ]
        elif ens.cible == "lv2":
            barrettes = [f"{promotion.code}-{code}-lv2"] * len(cibles)
        else:
            barrettes = [barrette] * len(cibles)

        if discipline is None:
            discipline = cat.DISCIPLINES[module.departement]
        semaines = self.semaines[code]
        campus = ens.campus if ens.campus in self.campus else None
        for rang, (groupes, cle_barrette) in enumerate(zip(cibles, barrettes, strict=True)):
            discipline_cible = discipline
            if ens.cible == "lv2":
                discipline_cible = promotion.lv2[rang][0].discipline
            fenetre = ens.fenetre
            if fenetre is not None and ens.alternance:
                milieu = (fenetre[0] + fenetre[1]) // 2
                fenetre = (fenetre[0], milieu) if rang % 2 == 0 else (milieu + 1, fenetre[1])
            date_debut = date_fin = None
            if fenetre is not None:
                premiere = semaines[min(fenetre[0], len(semaines)) - 1]
                derniere = semaines[min(fenetre[1], len(semaines)) - 1]
                date_debut = max(premiere.lundi, self.semestres[code].date_debut)
                date_fin = min(derniere.date_du(4), self.semestres[code].date_fin)
            spec = self._ajouter(
                semestre=code,
                promotion=promotion,
                matiere=matiere,
                type_seance=ens.type_seance,
                groupes=groupes,
                duree=ens.duree,
                profil=ens.profil,
                priorite=self._priorite(ens, groupes, cle_barrette),
                mode=ModeVolume.TOTAL if ens.total else ModeVolume.PAR_SEMAINE,
                nombre=ens.total or ens.nombre,
                max_par_semaine=ens.max_par_semaine,
                date_debut=date_debut,
                date_fin=date_fin,
                campus_impose=campus,
                discipline=discipline_cible,
                nb_enseignants=ens.enseignants,
                barrette=cle_barrette,
            )
            if spec.mode is ModeVolume.TOTAL:
                ouvertes = semaines_ouvertes(
                    self._domaine(spec, spec.cle), semaines, self.fermetures
                )
                spec.nombre = min(spec.nombre, len(ouvertes) * spec.max_par_semaine)
                if spec.nombre == 0:
                    self.specs.pop()
                    self._ecarter(spec, "aucune semaine de cours ouverte dans sa fenêtre")

    @staticmethod
    def _priorite(ens: cat.Enseignement, groupes: tuple, barrette: str | None) -> int:
        """Ordre de placement : les besoins les plus contraints d'abord.

        Imposés (0), barrettes (1), séances de promotion (2), séances longues
        de classe (3), autres séances de classe (4), séances longues de
        demi-groupe (5), TP (6), séances des matières courtes (7) ; les
        épreuves ponctuelles (8) et les événements à date libre (9) viennent
        en dernier. Les TP passent après les classes : deux demi-groupes
        peuvent être en TP en même temps, et le samedi matin leur reste ouvert.
        """
        if barrette:
            return 1
        if ens.cible in ("promotion", "option") or len(groupes) > 1:
            return 2
        if ens.cible == "classe" and not ens.total:
            return 3 if ens.duree >= 4 * H else 4
        if ens.duree >= 4 * H:
            return 5
        if ens.type_seance == "TP" and not ens.total:
            return 6
        return 7

    def _preparer_dst(self, promotion: _Promotion, code: str, s: int) -> None:
        jour, debut = cat.ANNEES_DST[promotion.annee]
        matiere = self._matiere(promotion, "IX", s, cat.NUMERO_DST, "Devoirs surveillés")
        commentaire = (
            "Créneau réservé chaque semaine aux devoirs surveillés ; la durée de l'épreuve "
            "est fixée par l'enseignant (3 h retenues, 4 h pour le tiers-temps)."
        )
        cibles = [(c.groupe, cat.DUREE_DST, "DST") for c in promotion.classes_de("classique")]
        cibles.append((promotion.tiers_temps, cat.DUREE_DST_TIERS_TEMPS, "DST (tiers-temps)"))
        for groupe, duree, libelle in cibles:
            self._ajouter(
                semestre=code,
                promotion=promotion,
                matiere=matiere,
                type_seance="DST",
                groupes=(groupe,),
                duree=duree,
                profil="epreuve",
                priorite=0,
                jour_impose=jour,
                debut_impose=debut,
                libelle=libelle,
                commentaire=commentaire,
            )

    def _preparer_partiels(
        self, promotion: _Promotion, code: str, matieres: dict[str, list[m.Matiere]]
    ) -> None:
        semaine = self.semaine_partiels[(promotion.annee, code)]
        groupes_promo = [promotion.groupe.id]
        creneaux = [
            (semaine.date_du(j), debut, duree)
            for j in range(5)
            if self._date_ouverte(semaine.date_du(j), groupes_promo)
            for debut, duree in cat.EPREUVES_PARTIEL
        ]
        if not creneaux:
            journal.warning("Partiels %s %s : aucune demi-journée ouverte", promotion.code, code)
            return
        decalage = promotion.annee - 3
        for filiere, liste in matieres.items():
            for i, matiere in enumerate(liste[: self.config.epreuves_par_partiel]):
                jour, debut, duree = creneaux[(decalage + 3 * i) % len(creneaux)]
                cibles = [(c.groupe, duree, "Partiel") for c in promotion.classes_de(filiere)]
                if filiere == "classique":
                    cibles.append((promotion.tiers_temps, duree * 4 // 3, "Partiel (tiers-temps)"))
                for groupe, duree_cible, libelle in cibles:
                    self._ajouter(
                        semestre=code,
                        promotion=promotion,
                        matiere=matiere,
                        type_seance="EXAMEN",
                        groupes=(groupe,),
                        duree=duree_cible,
                        profil="epreuve",
                        priorite=8,
                        mode=ModeVolume.PONCTUEL,
                        date_ponctuelle=jour,
                        debut_impose=debut,
                        libelle=libelle,
                    )

    def _preparer_evenements(self, promotion: _Promotion, code: str, s: int) -> None:
        semaines = self.semaines[code]
        matiere = self._matiere(
            promotion, "IG", s, cat.NUMERO_VIE_ECOLE, "Vie de l'école et insertion professionnelle"
        )
        if promotion.annee == 1 and code == "S1":
            premiere = semaines[0]
            amphis = sorted(
                (
                    x
                    for x in self.salles
                    if x.type_salle.code == "AMPHI" and x.campus is self.campus[promotion.site]
                ),
                key=lambda x: (-x.capacite, x.code),
            )
            self._ajouter(
                semestre=code,
                promotion=promotion,
                matiere=matiere,
                type_seance="AUTRE",
                groupes=(promotion.groupe,),
                duree=2 * H,
                profil="amphi",
                priorite=0,
                mode=ModeVolume.PONCTUEL,
                date_ponctuelle=premiere.lundi,
                debut_impose=10 * H,
                salle_imposee=amphis[0] if amphis else None,
                libelle="Amphi de rentrée",
            )
            jours = tuple(premiere.date_du(j) for j in range(1, 5))
            for classe in promotion.classes_de("classique"):
                self._ajouter(
                    semestre=code,
                    promotion=promotion,
                    matiere=matiere,
                    type_seance="AUTRE",
                    groupes=(classe.groupe,),
                    duree=2 * H,
                    profil="cours",
                    priorite=9,
                    mode=ModeVolume.PONCTUEL,
                    dates_possibles=jours,
                    libelle="Rencontre avec le tuteur de classe",
                )
        dates = tuple(
            semaines[n - 1].date_du(j)
            for n in _semaines_bornees(SEMAINES_CONFERENCE, len(semaines))
            for j in range(5)
            if self._date_ouverte(semaines[n - 1].date_du(j), [promotion.groupe.id])
        )
        if dates:
            self._ajouter(
                semestre=code,
                promotion=promotion,
                matiere=matiere,
                type_seance="AUTRE",
                groupes=(promotion.groupe,),
                duree=2 * H,
                profil="amphi",
                priorite=2,
                mode=ModeVolume.PONCTUEL,
                dates_possibles=dates,
                libelle="Conférence métiers",
            )

    def _confier_aux_fictifs(self) -> None:
        """Confie quelques TD à un enseignant « à recruter » (enseignant fictif de la matière)."""
        self.fictifs: list[EnseignantTemoin] = []
        candidats = [s for s in self.specs if s.type_seance == "TD" and s.barrette is None]
        nombre = min(self.config.nb_enseignants_fictifs, len(candidats))
        for spec in sorted(self.rng.sample(candidats, nombre), key=lambda s: s.cle):
            fictif = EnseignantTemoin(
                cle=-1 - len(self.fictifs),
                discipline=spec.discipline or "",
                statut=StatutEnseignant.VACATAIRE,
                civilite=None,
                nom=f"À recruter ({spec.matiere.code})",
                prenom=None,
                fictif=True,
            )
            self.fictifs.append(fictif)
            spec.enseignants_fixes = (fictif,)
            spec.commentaire = "Enseignant non encore recruté : enseignant fictif de la matière."

    # -- vivier d'enseignants -----------------------------------------------------

    def _constituer_vivier(self) -> None:
        self.vivier = Vivier(self._fabriquer_enseignant)
        demande: dict[str, Counter[str]] = {}
        for spec in self.specs:
            if spec.discipline and spec.enseignants_fixes is None and spec.nb_enseignants:
                hebdo = (
                    spec.duree
                    * spec.nb_enseignants
                    * (spec.nombre if spec.mode is ModeVolume.PAR_SEMAINE else spec.max_par_semaine)
                )
                demande.setdefault(spec.discipline, Counter())[spec.semestre] += hebdo
        for discipline in sorted(demande):
            besoin = max(demande[discipline].values())
            taille = max(1, math.ceil(besoin / SERVICE_VISE))
            for rang in range(taille):
                tirage = self.rng.random()
                if rang == 0 or tirage >= self.config.part_vacataires + self.config.part_exterieurs:
                    statut = StatutEnseignant.PERMANENT
                elif tirage < self.config.part_vacataires:
                    statut = StatutEnseignant.VACATAIRE
                else:
                    statut = StatutEnseignant.EXTERIEUR
                self.vivier.ajouter(discipline, statut)
        if (
            not any(e.statut is StatutEnseignant.VACATAIRE for e in self.vivier.enseignants)
            and demande
        ):
            discipline = max(sorted(demande), key=lambda d: max(demande[d].values()))
            self.vivier.ajouter(discipline, StatutEnseignant.VACATAIRE)
        for fictif in self.fictifs:
            self.vivier.ajouter_existant(fictif)
        journal.info(
            "Vivier initial : %d enseignants dans %d disciplines",
            len(self.vivier.enseignants),
            len(demande),
        )

    def _fabriquer_enseignant(
        self, cle: int, discipline: str, statut: StatutEnseignant
    ) -> EnseignantTemoin:
        while True:
            femme = self.rng.random() < 0.5
            civilite = "Mme" if femme else "M."
            nom = self.faker.last_name()
            if (civilite, nom) not in self._noms_pris:
                break
        self._noms_pris.add((civilite, nom))
        prenom = self.faker.first_name_female() if femme else self.faker.first_name_male()
        indispos: list[tuple[int, int, int]] = []
        if statut is not StatutEnseignant.PERMANENT:
            nb_jours = 1 if statut is StatutEnseignant.EXTERIEUR else self.rng.choice((1, 2))
            presents = set(self.rng.sample(range(5), nb_jours))
            samedi = (
                statut is StatutEnseignant.VACATAIRE
                and discipline in ("informatique", "electronique", "physique")
                and self.rng.random() < 0.3
            )
            for jour in range(6):
                if jour in presents or (jour == 5 and samedi):
                    continue
                indispos.append((jour, *JOURNEE))
            if statut is StatutEnseignant.VACATAIRE and nb_jours == 2 and self.rng.random() < 0.3:
                jour_partiel = min(presents)
                indispos.append((jour_partiel, 0, APRES_MIDI[0]))
        return EnseignantTemoin(
            cle=cle,
            discipline=discipline,
            statut=statut,
            civilite=civilite,
            nom=nom,
            prenom=prenom,
            indispos=tuple(indispos),
            plafond=PLAFONDS[statut],
        )

    # -- témoin -------------------------------------------------------------------

    def _domaine(self, spec: _Spec, identifiant: int) -> BesoinDomaine:
        """Le besoin au format du domaine (pour le dépliage)."""
        return BesoinDomaine(
            id=identifiant,
            libelle=spec.description,
            matiere_id=0,
            matiere_code=spec.matiere.code,
            type_seance_id=0,
            type_seance_code=spec.type_seance,
            samedi_autorise=self.types_seance[spec.type_seance].samedi_autorise,
            duree=spec.duree,
            groupes=frozenset(g.id for g in spec.groupes),
            enseignants=frozenset(),
            effectif=spec.effectif,
            accessibilite_requise=False,
            mode_volume=spec.mode,
            nombre=spec.nombre,
            max_par_semaine=spec.max_par_semaine,
            date_debut=spec.date_debut,
            date_fin=spec.date_fin,
            date_ponctuelle=spec.date_ponctuelle,
            jour_impose=spec.jour_impose,
            debut_impose=spec.debut_impose,
        )

    def _exigences(self, spec: _Spec) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Types de salle acceptés (résolus) et équipements requis du besoin."""
        types, equipements = cat.PROFILS_SALLE[spec.profil]
        if not types:
            defaut = self.types_seance[spec.type_seance].type_salle_defaut
            types = (defaut.code,) if defaut is not None else ()
        return types, equipements

    def _demande(self, spec: _Spec) -> Demande | str:
        """Demande de placement du besoin, ou la raison pour laquelle il ne peut pas être déplié."""
        groupes = tuple(g.id for g in spec.groupes)
        lies = frozenset().union(*(self.lies[g] for g in groupes))
        accessible = any(not self.lies[g].isdisjoint(self.pmr) for g in groupes)
        types, equipements = self._exigences(spec)
        effectif = spec.effectif
        campus_id = self.campus[spec.campus_impose].id if spec.campus_impose else None
        ordre = cat.ORDRE_SITES.get(spec.promotion.site, self.config.sites)
        rang_site = {self.campus[c].id: i for i, c in enumerate(ordre) if c in self.campus}
        salles = sorted(
            (
                x
                for x in self.salles_temoin
                if x.capacite >= effectif
                and (not types or x.type_code in types)
                and x.equipements.issuperset(equipements)
                and (x.accessible or not accessible)
                and (campus_id is None or x.campus_id == campus_id)
                and (spec.salle_imposee is None or x.id == spec.salle_imposee.id)
            ),
            key=lambda x: (
                rang_site.get(x.campus_id, 9),
                types.index(x.type_code) if x.type_code in types else 0,
                x.capacite,
                x.code,
            ),
        )
        semaines = self.semaines[spec.semestre]
        options: tuple[tuple[int, int], ...] = ()
        if spec.dates_possibles:
            options = tuple(
                (jour.weekday(), 1 << semaine_de(jour, semaines).numero)
                for jour in spec.dates_possibles
            )
            seances: tuple[tuple[int, int], ...] = ((1, 0),)
        else:
            depliage = deplier([self._domaine(spec, spec.cle)], semaines, self.fermetures)
            if depliage.problemes:
                return "; ".join(p.message for p in depliage.problemes)
            seances = tuple((s.rang, sum(1 << n for n in s.semaines)) for s in depliage.seances)
        jour_impose = spec.jour_impose
        if spec.date_ponctuelle is not None:
            jour_impose = spec.date_ponctuelle.weekday()
        return Demande(
            cle=spec.cle,
            libelle=spec.description,
            groupes=groupes,
            lies=lies,
            salles=tuple(salles),
            duree=spec.duree,
            seances=seances,
            samedi=self.types_seance[spec.type_seance].samedi_autorise,
            jour_impose=jour_impose,
            debut_impose=spec.debut_impose,
            options=options,
            discipline=spec.discipline if spec.nb_enseignants else None,
            nb_enseignants=spec.nb_enseignants,
            enseignants_fixes=spec.enseignants_fixes
            if spec.enseignants_fixes is not None
            else (None if spec.nb_enseignants and spec.discipline else ()),
            freres=self._freres(groupes),
            proches=self._proches(groupes),
        )

    def _proches(self, groupes: tuple[int, ...]) -> tuple[int, ...]:
        """Les groupes et tous leurs ascendants."""
        proches = set(groupes)
        for g in groupes:
            parent = self.parents.get(g)
            while parent is not None:
                proches.add(parent)
                parent = self.parents.get(parent)
        return tuple(sorted(proches))

    def _freres(self, groupes: tuple[int, ...]) -> tuple[int, ...]:
        """Groupes qui ont le même parent qu'un groupe de la liste (eux exclus)."""
        freres = {
            frere
            for g in groupes
            if self.parents.get(g) is not None
            for frere in self.enfants.get(self.parents[g], ())
        }
        return tuple(sorted(freres - set(groupes)))

    def _placer_semestre(self, code: str) -> None:
        specs = [spec for spec in self.specs if spec.semestre == code]
        temoin = Temoin(self.grille, self.trajets, self.vivier, self.rng, self.indispos_groupes)
        demandes: dict[int, Demande] = {}
        for spec in specs:
            demande = self._demande(spec)
            if isinstance(demande, str):
                self._ecarter(spec, f"dépliage impossible : {demande}")
            else:
                demandes[spec.cle] = demande
        unites: dict[str | int, list[_Spec]] = {}
        for spec in specs:
            if spec.cle in demandes:
                unites.setdefault(spec.barrette or spec.cle, []).append(spec)
        ordre = sorted(
            unites.values(),
            key=lambda u: (min(s.priorite for s in u), -max(s.effectif for s in u), u[0].cle),
        )
        recrutes = self.vivier.nb_recrutes
        for unite in ordre:
            resultats = temoin.placer_barrette([demandes[s.cle] for s in unite])
            for spec, resultat in zip(unite, resultats, strict=True):
                if resultat.place:
                    self.resultats[spec.cle] = resultat
                    if spec.dates_possibles:
                        spec.date_ponctuelle = spec.dates_possibles[resultat.option]
                else:
                    self._ecarter(spec, resultat.raison or "raison inconnue")
        places = sum(1 for s in specs if s.cle in self.resultats)
        journal.info(
            "Témoin %s : %d besoins placés sur %d, %d enseignant(s) recruté(s)",
            code,
            places,
            len(specs),
            self.vivier.nb_recrutes - recrutes,
        )

    def _ecarter(self, spec: _Spec, raison: str) -> None:
        journal.warning("Besoin écarté [%s] %s : %s", spec.semestre, spec.description, raison)
        self.ecartes.append(BesoinEcarte(spec.semestre, spec.description, raison))

    # -- écriture des enseignants et des besoins ----------------------------------

    def _creer_enseignants(self) -> None:
        utilises = sorted(
            {e.cle for r in self.resultats.values() for e in r.enseignants},
        )
        par_cle = {e.cle: e for e in self.vivier.enseignants}
        self.enseignants_orm: dict[int, m.Enseignant] = {}
        numero = 0
        for cle in sorted(utilises, key=lambda c: (c < 0, abs(c))):
            source = par_cle[cle]
            if source.fictif:
                identifiant = f"FICTIF-{abs(cle):02d}"
            else:
                numero += 1
                identifiant = f"E{numero:04d}"
            enseignant = m.Enseignant(
                identifiant=identifiant,
                civilite=source.civilite,
                nom=source.nom,
                prenom=source.prenom,
                statut=source.statut,
                fictif=source.fictif,
            )
            self.enseignants_orm[cle] = enseignant
            motif = (
                cat.MOTIF_EXTERIEUR
                if source.statut is StatutEnseignant.EXTERIEUR
                else cat.MOTIF_VACATAIRE
            )
            for jour, debut, fin in source.indispos:
                self.session.add(
                    m.Indisponibilite(
                        cible=CibleIndisponibilite.ENSEIGNANT,
                        enseignant=enseignant,
                        jour=jour,
                        debut_min=debut,
                        fin_min=fin,
                        motif=motif,
                    )
                )
        self.session.add_all(self.enseignants_orm.values())
        self.session.flush()
        self._poser_maxima()
        journal.info(
            "Enseignants créés : %d (%d recrutés pendant le témoin, %d du vivier inutilisés)",
            len(self.enseignants_orm),
            self.vivier.nb_recrutes,
            len(self.vivier.enseignants) - len(self.enseignants_orm),
        )

    def _poser_maxima(self) -> None:
        """Maxima horaires d'une partie des enseignants, toujours compatibles avec le témoin.

        Mesure prudente : la somme des séances de la semaine type (et du jour),
        quelles que soient leurs semaines actives.
        """
        semaine: Counter[tuple[int, str]] = Counter()
        jour: Counter[tuple[int, str, int]] = Counter()
        for spec in self.specs:
            resultat = self.resultats.get(spec.cle)
            if resultat is None:
                continue
            for e in resultat.enseignants:
                for place in resultat.places:
                    semaine[(e.cle, spec.semestre)] += place.fin - place.debut
                    jour[(e.cle, spec.semestre, place.jour)] += place.fin - place.debut
        candidats = sorted(c for c, e in self.enseignants_orm.items() if not e.fictif)
        nombre = round(self.config.part_maxima * len(candidats))
        for cle in sorted(self.rng.sample(candidats, nombre)):
            hebdo = max((v for (c, _), v in semaine.items() if c == cle), default=0)
            quotidien = max((v for (c, _, _), v in jour.items() if c == cle), default=0)
            enseignant = self.enseignants_orm[cle]
            enseignant.max_minutes_semaine = max(12 * H, _heure_sup(hebdo) + 2 * H)
            enseignant.max_minutes_jour = max(6 * H, _heure_sup(quotidien))

    def _creer_besoins(self) -> None:
        self.besoins_orm: dict[int, m.Besoin] = {}
        for spec in self.specs:
            resultat = self.resultats.get(spec.cle)
            if resultat is None:
                continue
            types, equipements = cat.PROFILS_SALLE[spec.profil]
            besoin = m.Besoin(
                semestre_id=self.semestres[spec.semestre].id,
                matiere=spec.matiere,
                type_seance=self.types_seance[spec.type_seance],
                libelle=spec.libelle,
                duree_min=spec.duree,
                mode_volume=spec.mode,
                nombre=spec.nombre,
                max_par_semaine=spec.max_par_semaine,
                date_debut=spec.date_debut,
                date_fin=spec.date_fin,
                date_ponctuelle=spec.date_ponctuelle,
                jour_impose=spec.jour_impose,
                debut_impose_min=spec.debut_impose,
                salle_imposee=spec.salle_imposee,
                campus_impose_id=self.campus[spec.campus_impose].id if spec.campus_impose else None,
                commentaire=spec.commentaire,
            )
            besoin.groupes = list(spec.groupes)
            besoin.enseignants = [self.enseignants_orm[e.cle] for e in resultat.enseignants]
            besoin.types_salle = [self.types_salle[t] for t in types]
            besoin.equipements = [self.equipements[e] for e in equipements]
            self.besoins_orm[spec.cle] = besoin
        self.session.add_all(self.besoins_orm.values())
        self.session.flush()
        journal.info("Besoins créés : %d", len(self.besoins_orm))

    def _controler_depliage(self) -> None:
        """Le dépliage officiel des besoins créés doit redonner exactement les séances du témoin."""
        self.temoin: dict[str, dict[str, PlaceTemoin]] = {}
        self.placees: dict[str, list[SeancePlacee]] = {}
        identifiants = {cle: e.identifiant for cle, e in self.enseignants_orm.items()}
        for code in self.config.semestres:
            specs = [s for s in self.specs if s.semestre == code and s.cle in self.besoins_orm]
            attendu: dict[str, int] = {}
            temoin: dict[str, PlaceTemoin] = {}
            placees: list[SeancePlacee] = []
            for spec in specs:
                besoin_id = self.besoins_orm[spec.cle].id
                resultat = self.resultats[spec.cle]
                for place in resultat.places:
                    seance = id_seance(besoin_id, place.rang)
                    attendu[seance] = place.masque
                    temoin[seance] = PlaceTemoin(place.jour, place.debut, place.fin, place.salle.id)
                    placees.append(
                        SeancePlacee(
                            seance,
                            place.jour,
                            place.debut,
                            place.fin,
                            place.masque,
                            place.salle.id,
                            tuple(g.id for g in spec.groupes),
                            tuple(identifiants[e.cle] for e in resultat.enseignants),
                        )
                    )
            depliage = deplier(
                [self._domaine(spec, self.besoins_orm[spec.cle].id) for spec in specs],
                self.semaines[code],
                self.fermetures,
            )
            obtenu = {s.id: sum(1 << n for n in s.semaines) for s in depliage.seances}
            if depliage.problemes or obtenu != attendu:
                ecarts = sorted(set(obtenu.items()) ^ set(attendu.items()))[:5]
                raise ErreurEDT(
                    f"Incohérence interne du générateur : le dépliage du semestre {code} ne "
                    "redonne pas les séances du témoin.",
                    [p.message for p in depliage.problemes] + [str(e) for e in ecarts],
                )
            self.temoin[code] = temoin
            self.placees[code] = placees
        journal.info("Dépliage contrôlé : il redonne exactement les séances du témoin")

    # -- indisponibilités posées après le témoin ------------------------------------

    def _poser_indisponibilites_apres_temoin(self) -> None:
        """Indisponibilités datées et récurrentes posées là où le témoin laisse la place."""
        occ_enseignant: dict[int, list[tuple[str, int, int, int, int]]] = {}
        occ_salle: dict[int, list[tuple[str, int, int, int, int]]] = {}
        for spec in self.specs:
            resultat = self.resultats.get(spec.cle)
            if resultat is None:
                continue
            for place in resultat.places:
                entree = (spec.semestre, place.jour, place.debut, place.fin, place.masque)
                occ_salle.setdefault(place.salle.id, []).append(entree)
                for e in resultat.enseignants:
                    occ_enseignant.setdefault(e.cle, []).append(entree)
        nb = 0
        nb += self._indispos_datees_enseignants(occ_enseignant)
        nb += self._salles_reservees_partiels(occ_salle)
        nb += self._salles_en_travaux(occ_salle)
        nb += self._salle_reunion(occ_salle)
        self.session.flush()
        journal.info("Indisponibilités posées après le témoin : %d", nb)

    def _libre_le(self, occupations, code: str, semaine: Semaine, jour: int) -> bool:
        return not any(
            c == code and j == jour and masque >> semaine.numero & 1
            for c, j, _, _, masque in occupations
        )

    def _indispos_datees_enseignants(self, occupations) -> int:
        """Absences datées de quelques permanents (trois jours ouvrés de suite si possible).

        Les jours retenus sont des jours où l'enseignant n'a aucune séance du
        témoin cette semaine-là : l'absence ne provoque aucun replacement.
        """
        permanents = sorted(
            c
            for c, e in self.enseignants_orm.items()
            if e.statut is StatutEnseignant.PERMANENT and not e.fictif
        )
        nombre = min(self.config.nb_absences_enseignants, len(permanents))
        poses = 0
        for cle in sorted(self.rng.sample(permanents, nombre)):
            code = self.rng.choice(self.config.semestres)
            semaines = list(self.semaines[code])
            self.rng.shuffle(semaines)
            choix = None
            for longueur in (3, 2, 1):
                choix = next(
                    (
                        (semaine, premier, longueur)
                        for semaine in semaines
                        for premier in range(5 - longueur + 1)
                        if all(
                            self._libre_le(occupations.get(cle, []), code, semaine, j)
                            and self._date_ouverte(semaine.date_du(j), [])
                            for j in range(premier, premier + longueur)
                        )
                    ),
                    None,
                )
                if choix is not None:
                    break
            if choix is None:
                journal.info("Aucune journée libre pour une absence datée (enseignant %d)", cle)
                continue
            semaine, premier, longueur = choix
            self.session.add(
                m.Indisponibilite(
                    cible=CibleIndisponibilite.ENSEIGNANT,
                    enseignant=self.enseignants_orm[cle],
                    date_debut=semaine.date_du(premier),
                    date_fin=semaine.date_du(premier + longueur - 1),
                    motif=self.rng.choice(cat.MOTIFS_INDISPO_ENSEIGNANT),
                )
            )
            poses += 1
        return poses

    def _salles_reservees_partiels(self, occupations) -> int:
        """Deux salles de cours réservées par le service des examens pendant chaque session.

        Une réservation par site et par semaine de partiels, sur des salles que
        le témoin n'utilise pas cette semaine-là.
        """
        sessions: dict[tuple[str, str, int], list[int]] = {}
        for (annee, code), semaine in sorted(self.semaine_partiels.items()):
            sessions.setdefault((code, self._site_de(annee), semaine.numero), []).append(annee)
        poses = 0
        for (code, site, numero), annees in sorted(sessions.items()):
            semaine = self.semaines[code][numero - 1]
            libres = [
                x
                for x in self.salles
                if x.campus is self.campus[site]
                and x.type_salle.code == "COURS"
                and all(
                    self._libre_le(occupations.get(x.id, []), code, semaine, j) for j in range(5)
                )
            ]
            promotions = ", ".join(f"A{a}" for a in annees)
            for salle in libres[:2]:
                self._poser_indispo_salle(
                    salle,
                    semaine.lundi,
                    semaine.date_du(4),
                    f"{cat.MOTIF_PARTIELS_SALLE} {promotions}",
                )
                poses += 1
        return poses

    def _poser_indispo_salle(
        self, salle: m.Salle, debut: dt.date, fin: dt.date, motif: str
    ) -> None:
        self.session.add(
            m.Indisponibilite(
                cible=CibleIndisponibilite.SALLE,
                salle=salle,
                date_debut=debut,
                date_fin=fin,
                motif=motif,
            )
        )

    def _salles_en_travaux(self, occupations) -> int:
        """Une salle en travaux pendant une semaine où le témoin ne l'utilise pas."""
        code = self.config.semestres[-1]
        semaines = self.semaines[code]
        salles = list(self.salles)
        self.rng.shuffle(salles)
        for salle in salles:
            for semaine in semaines[2:-2]:
                if all(
                    self._libre_le(occupations.get(salle.id, []), code, semaine, j)
                    for j in range(6)
                ):
                    self._poser_indispo_salle(
                        salle, semaine.lundi, semaine.date_du(5), cat.MOTIF_TRAVAUX
                    )
                    return 1
        journal.info("Aucune salle libre une semaine entière : pas de salle en travaux")
        return 0

    def _salle_reunion(self, occupations) -> int:
        """Une salle de cours réservée chaque vendredi après-midi (indisponibilité récurrente)."""
        candidates = sorted(
            (x for x in self.salles if x.type_salle.code == "COURS"), key=lambda x: x.code
        )
        for salle in candidates:
            if not any(
                j == 4 and debut < APRES_MIDI[1] and APRES_MIDI[0] < fin
                for _, j, debut, fin, _ in occupations.get(salle.id, [])
            ):
                self.session.add(
                    m.Indisponibilite(
                        cible=CibleIndisponibilite.SALLE,
                        salle=salle,
                        jour=4,
                        debut_min=APRES_MIDI[0],
                        fin_min=APRES_MIDI[1],
                        motif=cat.MOTIF_REUNION,
                    )
                )
                return 1
        journal.info("Aucune salle de cours libre le vendredi après-midi : pas de salle de réunion")
        return 0

    # -- version de référence et rapport -------------------------------------------

    def _enregistrer_references(self) -> dict[str, int]:
        versions: dict[str, int] = {}
        for code in self.config.semestres:
            semestre = self.semestres[code]
            numero = (
                self.session.scalar(
                    select(func.max(m.VersionEdt.numero)).where(
                        m.VersionEdt.semestre_id == semestre.id
                    )
                )
                or 0
            ) + 1
            version = m.VersionEdt(
                semestre_id=semestre.id,
                numero=numero,
                libelle=f"Témoin du jeu fictif « {self.config.nom} » (graine {self.graine})",
                origine=OrigineVersion.REFERENCE,
                active=False,
                cree_le=dt.datetime.combine(self.annee.date_debut, dt.time()),
                commentaire=(
                    "Emploi du temps témoin construit par le générateur de données fictives : "
                    "preuve de faisabilité, à comparer au résultat du solveur. Version non "
                    "active : le solveur n'en reçoit aucun indice."
                ),
            )
            for seance, place in self.temoin[code].items():
                besoin_id, rang = (int(x) for x in seance.split("-"))
                version.seances.append(
                    m.SeanceHebdo(
                        besoin_id=besoin_id,
                        rang=rang,
                        jour=place.jour,
                        debut_min=place.debut,
                        fin_min=place.fin,
                        salle_id=place.salle_id,
                        verrou=False,
                        semaines=_semaines(
                            next(p.masque for p in self.placees[code] if p.seance_id == seance)
                        ),
                    )
                )
            self.session.add(version)
            self.session.flush()
            versions[code] = version.id
        journal.info("Versions de référence enregistrées : %s", versions)
        return versions

    def _verifier_temoin(self) -> tuple[dict[str, dict[str, int]], dict[str, Indicateurs]]:
        """Soumet le témoin au vérificateur officiel et calcule ses indicateurs.

        Une anomalie de gravité « erreur » trahirait un défaut du générateur :
        elle est journalisée comme erreur et comptée dans le rapport.
        """
        anomalies_par_semestre: dict[str, dict[str, int]] = {}
        indicateurs: dict[str, Indicateurs] = {}
        for code in self.config.semestres:
            instantane = charger_instantane(self.session, self.semestres[code].id)
            placements = [
                Placement(seance, p.jour, p.debut, p.fin, p.salle_id)
                for seance, p in self.temoin[code].items()
            ]
            anomalies = verifier(instantane, placements)
            compte = Counter(str(a.gravite.value) for a in anomalies)
            anomalies_par_semestre[code] = dict(sorted(compte.items()))
            for anomalie in anomalies:
                if anomalie.bloquante:
                    journal.error(
                        "Témoin %s refusé par le vérificateur (%s) : %s",
                        code,
                        anomalie.code,
                        anomalie.message,
                    )
            indicateurs[code] = calculer_indicateurs(instantane, placements, anomalies)
            detail = ", ".join(f"{g} {n}" for g, n in sorted(compte.items()))
            journal.info("Témoin %s vérifié : %s", code, detail or "aucune anomalie")
        return anomalies_par_semestre, indicateurs

    def _rapport(
        self,
        versions: dict[str, int],
        anomalies: dict[str, dict[str, int]],
        indicateurs: dict[str, Indicateurs],
        duree: float,
    ) -> RapportJeu:
        s = self.session
        compteurs = {
            modele.__tablename__: s.scalar(select(func.count()).select_from(modele))
            for modele in m.MODELES
            if modele not in (m.Calcul, m.Occurrence, m.Parametre, m.Creneau)
        }
        for table in (m.inscription, m.groupe_chevauchement):
            compteurs[table.name] = s.scalar(select(func.count()).select_from(table))
        crees = [spec for spec in self.specs if spec.cle in self.besoins_orm]
        suivis = {d.id: d.code for p in self.promotions for c in p.classes for d in c.demi_groupes}
        salles = {x.id: (x.code, x.type_salle.code) for x in self.salles}
        charges = tuple(
            calculer_charge(
                code,
                len(self.semaines[code]),
                self.grille.minutes_ouvrees(),
                self.placees[code],
                suivis,
                self.lies,
                salles,
            )
            for code in self.config.semestres
        )
        return RapportJeu(
            configuration=self.config,
            graine=self.graine,
            annee=self.annee.libelle,
            compteurs=compteurs,
            besoins_par_mode=dict(sorted(Counter(str(sp.mode.value) for sp in crees).items())),
            besoins_par_type=dict(sorted(Counter(sp.type_seance for sp in crees).items())),
            seances_par_semestre={code: len(self.temoin[code]) for code in self.config.semestres},
            charges=charges,
            besoins_ecartes=tuple(self.ecartes),
            temoin_par_semestre=self.temoin,
            semestres={code: self.semestres[code].id for code in self.config.semestres},
            versions_reference=versions,
            nb_recrutements=self.vivier.nb_recrutes,
            anomalies_temoin=anomalies,
            indicateurs=indicateurs,
            duree_s=round(duree, 3),
        )


def _semaines_bornees(numeros: Sequence[int], nb_semaines: int) -> list[int]:
    """Numéros de semaine retenus parmi `numeros`, bornés au semestre."""
    return [n for n in numeros if 1 <= n <= nb_semaines]


def _heure_sup(minutes: int) -> int:
    """Arrondi à l'heure supérieure."""
    return -(-minutes // H) * H


def _semaines(masque: int) -> list[int]:
    """Numéros des semaines d'un masque de bits, triés."""
    return [n for n in range(masque.bit_length()) if masque >> n & 1]
