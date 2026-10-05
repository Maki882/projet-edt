"""Jeu de données minimal : un tout petit établissement cohérent et entièrement fictif.

Chargé par `python -m edt jeu-minimal` et par les tests (fixture
`jeu_minimal`). Il couvre chaque notion du modèle au moins une fois :

- 2 campus (Notre-Dame-des-Champs, Issy) et leur trajet ;
- 6 salles de types, capacités, équipements et accessibilités variés ;
- 5 enseignants (permanents, vacataire, extérieur), deux indisponibilités
  récurrentes, une indisponibilité de salle datée ;
- 1 promotion (A1), 2 classes, 4 demi-groupes, 1 groupe transverse de
  langue (chevauchement déclaré avec les deux classes) et 1 groupe PMR
  (un étudiant fictif inscrit aussi dans un demi-groupe) ;
- 6 matières, 16 besoins dans les trois modes de volume (dont un besoin à
  deux groupes sans enseignant, un DST et un examen ponctuels) ;
- le semestre 1 de l'année 2026-2027, deux périodes de vacances et une
  semaine d'examens propre à la promotion.

Les jeux réalistes de trois tailles (petit, moyen, grand) relèvent du lot
« données fictives » ; celui-ci reste volontairement minuscule et stable.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from edt.db.initialisation import assurer_annee_scolaire
from edt.db.modeles import (
    Besoin,
    Campus,
    Enseignant,
    Equipement,
    Etudiant,
    Groupe,
    Indisponibilite,
    JourFerme,
    Matiere,
    Salle,
    Trajet,
    TypeSalle,
    TypeSeance,
    groupe_chevauchement,
)
from edt.domaine.enumerations import (
    Accessibilite,
    CibleIndisponibilite,
    ModeVolume,
    NatureGroupe,
    NatureJourFerme,
    StatutEnseignant,
)
from edt.erreurs import ErreurRegleMetier

journal = logging.getLogger(__name__)

ANNEE_DEBUT = 2026
H = 60


@dataclass(frozen=True)
class JeuMinimal:
    """Repères du jeu minimal créé (identifiants utiles aux tests et à la CLI)."""

    annee_scolaire_id: int
    semestre_id: int
    nb_campus: int
    nb_salles: int
    nb_enseignants: int
    nb_groupes: int
    nb_matieres: int
    nb_besoins: int


def creer_jeu_minimal(session: Session) -> JeuMinimal:
    """Crée le jeu minimal dans une base initialisée et encore vide de référentiels.

    Lève `ErreurRegleMetier` si la base n'a pas ses données par défaut ou si
    elle contient déjà des campus, salles, enseignants ou groupes. Ne valide
    pas la transaction : l'appelant appelle `session.commit()`.
    """
    types_salle = {t.code: t for t in session.scalars(select(TypeSalle))}
    equipements = {e.code: e for e in session.scalars(select(Equipement))}
    types_seance = {t.code: t for t in session.scalars(select(TypeSeance))}
    if not (types_salle and equipements and types_seance):
        raise ErreurRegleMetier(
            "La base n'a pas ses données par défaut : lancer d'abord « python -m edt init-db »."
        )
    for modele, nom in ((Campus, "campus"), (Salle, "salles"), (Enseignant, "enseignants")):
        if session.scalar(select(modele.id).limit(1)) is not None:
            raise ErreurRegleMetier(
                f"La base contient déjà des {nom} : le jeu minimal se charge dans une base "
                "vide (« python -m edt init-db --reset » pour repartir de zéro)."
            )
    if session.scalar(select(Groupe.id).limit(1)) is not None:
        raise ErreurRegleMetier(
            "La base contient déjà des groupes : le jeu minimal se charge dans une base "
            "vide (« python -m edt init-db --reset » pour repartir de zéro)."
        )

    annee = assurer_annee_scolaire(session, ANNEE_DEBUT)
    semestre = next(s for s in annee.semestres if s.code == "S1")

    # -- campus et salles ---------------------------------------------------------
    ndc = Campus(code="NDC", nom="Notre-Dame-des-Champs", prefixe_salle="N")
    issy = Campus(code="ISSY", nom="Issy-les-Moulineaux", prefixe_salle="L")
    session.add_all([ndc, issy])
    session.flush()
    session.add(
        Trajet(campus_a_id=min(ndc.id, issy.id), campus_b_id=max(ndc.id, issy.id), duree_min=60)
    )

    def salle(code, campus, etage, capacite, type_code, accessibilite, *codes_equipement):
        return Salle(
            code=code,
            campus=campus,
            batiment="Principal",
            etage=etage,
            capacite=capacite,
            type_salle=types_salle[type_code],
            accessibilite=accessibilite,
            equipements=[equipements[c] for c in codes_equipement],
        )

    oui, non, inconnu = Accessibilite.OUI, Accessibilite.NON, Accessibilite.INCONNU
    salles = [
        salle("N016", ndc, 0, 120, "AMPHI", oui, "VIDEOPROJ", "VISIO"),
        salle("N105", ndc, 1, 40, "COURS", oui, "VIDEOPROJ"),
        salle("N204", ndc, 2, 40, "COURS", non, "VIDEOPROJ"),
        salle("N310", ndc, 3, 24, "INFO", inconnu, "VIDEOPROJ", "ORDINATEURS"),
        salle("L012", issy, 0, 24, "INFO", oui, "VIDEOPROJ", "ORDINATEURS"),
        salle("L115", issy, 1, 30, "TP", oui, "ELECTRONIQUE"),
    ]
    session.add_all(salles)
    salle_tp = salles[-1]

    # -- enseignants ----------------------------------------------------------------
    def enseignant(identifiant, civilite, nom, prenom, statut, max_jour=None, max_semaine=None):
        return Enseignant(
            identifiant=identifiant,
            civilite=civilite,
            nom=nom,
            prenom=prenom,
            statut=statut,
            max_minutes_jour=max_jour,
            max_minutes_semaine=max_semaine,
        )

    martin = enseignant("ENS001", "Mme", "Martin", "Claire", StatutEnseignant.PERMANENT)
    bernard = enseignant("ENS002", "M.", "Bernard", "Hugo", StatutEnseignant.PERMANENT)
    dubois = enseignant("ENS003", "Mme", "Dubois", "Inès", StatutEnseignant.PERMANENT, 6 * H)
    leroy = enseignant("ENS004", "M.", "Leroy", "Paul", StatutEnseignant.VACATAIRE, None, 8 * H)
    moreau = enseignant("ENS005", "Mme", "Moreau", "Jeanne", StatutEnseignant.EXTERIEUR)
    session.add_all([martin, bernard, dubois, leroy, moreau])

    # -- groupes ----------------------------------------------------------------------
    def groupe(code, libelle, nature, effectif, parent=None, accessible=False):
        return Groupe(
            annee_scolaire_id=annee.id,
            code=code,
            libelle=libelle,
            nature=nature,
            effectif=effectif,
            parent=parent,
            accessibilite_requise=accessible,
        )

    promo = groupe("A1", "1re année", NatureGroupe.PROMOTION, 56)
    g1 = groupe("A1-G1", "1re année, classe 1", NatureGroupe.CLASSE, 28, promo)
    g2 = groupe("A1-G2", "1re année, classe 2", NatureGroupe.CLASSE, 28, promo)
    g1a = groupe("A1-G1a", "Classe 1, demi-groupe a", NatureGroupe.DEMI_GROUPE, 14, g1)
    g1b = groupe("A1-G1b", "Classe 1, demi-groupe b", NatureGroupe.DEMI_GROUPE, 14, g1)
    g2a = groupe("A1-G2a", "Classe 2, demi-groupe a", NatureGroupe.DEMI_GROUPE, 14, g2)
    g2b = groupe("A1-G2b", "Classe 2, demi-groupe b", NatureGroupe.DEMI_GROUPE, 14, g2)
    anglais_b2 = groupe("A1-ANG-B2", "Anglais niveau B2", NatureGroupe.TRANSVERSE, 20)
    pmr = groupe("A1-PMR", "Étudiants à mobilité réduite", NatureGroupe.TRANSVERSE, 1, None, True)
    groupes = [promo, g1, g2, g1a, g1b, g2a, g2b, anglais_b2, pmr]
    session.add_all(groupes)
    session.flush()

    # Le groupe de langue mélange des étudiants des deux classes : chevauchements déclarés.
    for classe in (g1, g2):
        a, b = sorted((classe.id, anglais_b2.id))
        session.execute(insert(groupe_chevauchement).values(groupe_a_id=a, groupe_b_id=b))

    # Un étudiant fictif à mobilité réduite, inscrit dans son demi-groupe et dans le groupe PMR.
    session.add(Etudiant(identifiant="ETU001", nom="Petit", prenom="Camille", groupes=[g1a, pmr]))

    # -- indisponibilités -----------------------------------------------------------
    session.add_all(
        [
            Indisponibilite(
                cible=CibleIndisponibilite.ENSEIGNANT,
                enseignant=leroy,
                jour=0,
                motif="Activité professionnelle le lundi",
            ),
            Indisponibilite(
                cible=CibleIndisponibilite.ENSEIGNANT,
                enseignant=moreau,
                jour=4,
                debut_min=13 * H + 45,
                fin_min=18 * H,
                motif="Indisponible le vendredi après-midi",
            ),
            Indisponibilite(
                cible=CibleIndisponibilite.SALLE,
                salle=salle_tp,
                date_debut=dt.date(2026, 11, 2),
                date_fin=dt.date(2026, 11, 6),
                motif="Travaux",
            ),
        ]
    )

    # -- calendrier propre à l'école ----------------------------------------------
    session.add_all(
        [
            JourFerme(
                annee_scolaire_id=annee.id,
                date_debut=dt.date(2026, 10, 26),
                date_fin=dt.date(2026, 11, 1),
                libelle="Vacances de la Toussaint",
                nature=NatureJourFerme.VACANCES,
            ),
            JourFerme(
                annee_scolaire_id=annee.id,
                date_debut=dt.date(2026, 12, 21),
                date_fin=dt.date(2027, 1, 3),
                libelle="Vacances de Noël",
                nature=NatureJourFerme.VACANCES,
            ),
            # Une fermeture de portée « groupe » vaut pour le groupe et ses descendants :
            # le groupe de langue, hors de l'arbre de la promotion, reçoit la sienne.
            *(
                JourFerme(
                    annee_scolaire_id=annee.id,
                    date_debut=dt.date(2027, 1, 11),
                    date_fin=dt.date(2027, 1, 15),
                    libelle="Partiels de 1re année",
                    nature=NatureJourFerme.EXAMENS,
                    groupe=groupe_ferme,
                )
                for groupe_ferme in (promo, anglais_b2)
            ),
        ]
    )

    # -- matières -------------------------------------------------------------------
    def matiere(code, libelle):
        return Matiere(code=code, libelle=libelle, promotion=promo)

    maths = matiere("IM.1101", "Mathématiques")
    algo = matiere("II.1101", "Algorithmique")
    elec = matiere("IE.1101", "Électronique")
    physique = matiere("IP.1101", "Physique")
    anglais = matiere("IL.1101", "Anglais")
    projet = matiere("IG.1101", "Projet d'intégration")
    matieres = [maths, algo, elec, physique, anglais, projet]
    session.add_all(matieres)

    # -- besoins ----------------------------------------------------------------------
    def besoin(matiere_, type_code, groupes_, enseignants_, **champs):
        types = [types_salle[c] for c in champs.pop("types_salle", ())]
        equipements_ = [equipements[c] for c in champs.pop("equipements", ())]
        return Besoin(
            semestre_id=semestre.id,
            matiere=matiere_,
            type_seance=types_seance[type_code],
            groupes=list(groupes_),
            enseignants=list(enseignants_),
            types_salle=types,
            equipements=equipements_,
            **champs,
        )

    info = {"types_salle": ("INFO",), "equipements": ("ORDINATEURS",)}
    tp_elec = {
        "types_salle": ("TP",),
        "equipements": ("ELECTRONIQUE",),
        "mode_volume": ModeVolume.TOTAL,
        "nombre": 6,
        "max_par_semaine": 1,
        "date_debut": dt.date(2026, 10, 5),
        "date_fin": dt.date(2026, 12, 18),
    }
    besoins = [
        # Volume par semaine.
        besoin(maths, "CM", [promo], [martin]),
        besoin(maths, "TD", [g1], [martin]),
        besoin(maths, "TD", [g2], [bernard]),
        besoin(algo, "CM", [promo], [bernard]),
        besoin(algo, "TP", [g1a], [dubois], **info),
        besoin(algo, "TP", [g1b], [dubois], **info),
        besoin(algo, "TP", [g2a], [leroy], **info),
        besoin(algo, "TP", [g2b], [leroy], **info),
        besoin(anglais, "LANGUE", [anglais_b2], [moreau]),
        besoin(projet, "PROJET", [g1, g2], [], types_salle=("AMPHI",)),
        # Volume total réparti sur une fenêtre.
        besoin(elec, "CM", [promo], [dubois], mode_volume=ModeVolume.TOTAL, nombre=8),
        besoin(elec, "TP", [g1], [dubois], **tp_elec),
        besoin(elec, "TP", [g2], [dubois], **tp_elec),
        besoin(
            physique,
            "CM",
            [promo],
            [bernard],
            mode_volume=ModeVolume.TOTAL,
            nombre=10,
            max_par_semaine=2,
            date_debut=dt.date(2026, 9, 7),
            date_fin=dt.date(2026, 10, 23),
        ),
        # Séances ponctuelles, datées à la main.
        besoin(
            maths,
            "DST",
            [promo],
            [martin],
            libelle="Devoir surveillé de mathématiques",
            duree_min=3 * H,
            mode_volume=ModeVolume.PONCTUEL,
            date_ponctuelle=dt.date(2026, 11, 19),
            debut_impose_min=13 * H + 45,
            types_salle=("AMPHI",),
        ),
        besoin(
            algo,
            "EXAMEN",
            [promo],
            [bernard],
            libelle="Partiel d'algorithmique",
            mode_volume=ModeVolume.PONCTUEL,
            date_ponctuelle=dt.date(2027, 1, 12),
            debut_impose_min=8 * H + 30,
            types_salle=("AMPHI",),
        ),
    ]
    session.add_all(besoins)
    session.flush()

    jeu = JeuMinimal(
        annee_scolaire_id=annee.id,
        semestre_id=semestre.id,
        nb_campus=2,
        nb_salles=len(salles),
        nb_enseignants=5,
        nb_groupes=len(groupes),
        nb_matieres=len(matieres),
        nb_besoins=len(besoins),
    )
    journal.info(
        "Jeu minimal créé : %d salles, %d groupes, %d besoins (semestre %s)",
        jeu.nb_salles,
        jeu.nb_groupes,
        jeu.nb_besoins,
        semestre.libelle,
    )
    return jeu
