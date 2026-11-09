"""Calendrier et grille : années scolaires, semestres, jours fermés, créneaux horaires."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased, selectinload

from edt.db.modeles import (
    AnneeScolaire,
    Besoin,
    Calcul,
    Creneau,
    Groupe,
    JourFerme,
    Matiere,
    Regle,
    Semestre,
    VersionEdt,
    besoin_groupe,
)
from edt.db.moteur import identifiant_valide
from edt.domaine.calendrier import decoupage_par_defaut
from edt.domaine.enumerations import JOURS, DemiJournee, NatureJourFerme
from edt.domaine.heures import duree_vers_texte, minutes_vers_texte
from edt.erreurs import ErreurIntrouvable, ErreurRegleMetier
from edt.services.calendrier import assurer_annee_scolaire, preremplir_feries
from edt.services.referentiels.description import (
    Champ,
    Colonne,
    Decompte,
    Entite,
    Filtre,
    TypeChamp,
    Unicite,
    Valeurs,
    choix_enum,
    choix_jours,
    libelle_choix,
    options_reference,
    par_colonne,
)
from edt.services.referentiels.operations import lire
from edt.services.referentiels.personnes import REF_ANNEE, REF_GROUPE, annee_par_defaut
from edt.services.regles import proposer_pour_annee

journal = logging.getLogger(__name__)

MIDI = 12 * 60


def _date(valeur: dt.date | None) -> str:
    return "" if valeur is None else valeur.strftime("%d/%m/%Y")


def _pluriel(nombre: int, singulier: str, pluriel: str) -> str:
    return f"{nombre} {singulier if nombre == 1 else pluriel}"


def _dans_l_annee(session: Session, v: Valeurs, noms: tuple[str, ...]) -> dict[str, str]:
    """Erreurs des dates hors de l'année scolaire choisie."""
    annee = (
        session.get(AnneeScolaire, v["annee_scolaire_id"]) if v.get("annee_scolaire_id") else None
    )
    erreurs: dict[str, str] = {}
    if annee is None:
        return erreurs
    for nom in noms:
        jour = v.get(nom)
        if jour and not annee.date_debut <= jour <= annee.date_fin:
            erreurs[nom] = (
                f"La date doit être comprise dans l'année {annee.libelle} "
                f"(du {_date(annee.date_debut)} au {_date(annee.date_fin)})."
            )
    return erreurs


def _dates_ordonnees(v: Valeurs) -> dict[str, str]:
    debut, fin = v.get("date_debut"), v.get("date_fin")
    if debut and fin and debut > fin:
        return {"date_fin": "La fin doit suivre le début."}
    return {}


ANNEE_DEBUT_MIN, ANNEE_DEBUT_MAX = 2000, 2100
"""Une année scolaire commence entre ces deux années (comme `creer_annee_par_defaut`)."""
DUREE_ANNEE_MAX_JOURS = 731
"""Deux ans : au-delà, une date de fin est une faute de frappe (« 2206 » pour « 2026 »)."""
DUREE_SEMESTRE_MAX_JOURS = 366
"""Un an."""


def _duree_bornee(v: Valeurs, jours_max: int, message: str) -> dict[str, str]:
    """Erreur sous la date de fin si la période dépasse `jours_max` jours.

    Une période démesurée (« 31/12/9999 ») ferait déborder le calcul des
    semaines et produirait des centaines de milliers de séances datées.
    """
    debut, fin = v.get("date_debut"), v.get("date_fin")
    if debut and fin and (fin - debut).days > jours_max:
        return {"date_fin": f"{message} (lu : du {_date(debut)} au {_date(fin)})."}
    return {}


def _valider_annee(v: Valeurs) -> dict[str, str]:
    """Dates ordonnées, deux ans au plus, début entre 2000 et 2100."""
    erreurs = {
        **_dates_ordonnees(v),
        **_duree_bornee(v, DUREE_ANNEE_MAX_JOURS, "Une année scolaire dure deux ans au plus"),
    }
    debut = v.get("date_debut")
    if debut and not ANNEE_DEBUT_MIN <= debut.year <= ANNEE_DEBUT_MAX:
        erreurs["date_debut"] = (
            f"Une année scolaire doit commencer entre {ANNEE_DEBUT_MIN} et {ANNEE_DEBUT_MAX} "
            f"(lu : {_date(debut)})."
        )
    return erreurs


# --------------------------------------------------------------------------- #
# Années scolaires
# --------------------------------------------------------------------------- #


def _analyser_annee(session: Session, annee: AnneeScolaire) -> tuple[list[str], list[str]]:
    semestres = select(Semestre.id).where(Semestre.annee_scolaire_id == annee.id)
    groupes = select(Groupe.id).where(Groupe.annee_scolaire_id == annee.id)
    exterieurs = session.scalar(
        select(func.count(func.distinct(Besoin.id))).where(
            Besoin.semestre_id.not_in(semestres),
            Besoin.id.in_(
                select(besoin_groupe.c.besoin_id).where(besoin_groupe.c.groupe_id.in_(groupes))
            )
            | Besoin.matiere_id.in_(select(Matiere.id).where(Matiere.promotion_id.in_(groupes))),
        )
    )
    refus = []
    if exterieurs:
        refus.append(
            f"Ses groupes ou ses matières servent à {_pluriel(exterieurs, 'besoin', 'besoins')} "
            "d'une autre année."
        )
    decomptes = (
        (select(func.count()).select_from(semestres.subquery()), "semestre", "semestres", False),
        (select(func.count()).select_from(groupes.subquery()), "groupe", "groupes", False),
        (select(func.count()).where(Besoin.semestre_id.in_(semestres)), "besoin", "besoins", False),
        (
            select(func.count()).where(JourFerme.annee_scolaire_id == annee.id),
            "jour fermé",
            "jours fermés",
            False,
        ),
        (
            select(func.count()).where(VersionEdt.semestre_id.in_(semestres)),
            "version d'emploi du temps",
            "versions d'emploi du temps",
            True,
        ),
    )
    consequences = []
    for requete, singulier, pluriel, feminin in decomptes:
        nombre = session.scalar(requete) or 0
        if nombre:
            participe = "supprimée" if feminin else "supprimé"
            verbe = f"sera {participe}" if nombre == 1 else f"seront {participe}s"
            consequences.append(f"{_pluriel(nombre, singulier, pluriel)} {verbe}.")
    return refus, consequences


ANNEE_SCOLAIRE = Entite(
    code="annee_scolaire",
    modele=AnneeScolaire,
    nom="année scolaire",
    pluriel="années scolaires",
    feminin=True,
    demonstratif="Cette année scolaire",
    nouveau="Nouvelle année scolaire",
    libelle=lambda a: a.libelle,
    champs=(
        Champ("libelle", "Libellé", obligatoire=True, aide="Par exemple « 2027-2028 »."),
        Champ("date_debut", "Premier jour", TypeChamp.DATE, obligatoire=True),
        Champ("date_fin", "Dernier jour", TypeChamp.DATE, obligatoire=True),
    ),
    colonnes=(
        Colonne("libelle", "Année", lambda a: a.libelle, tri=AnneeScolaire.libelle),
        Colonne("debut", "Début", lambda a: _date(a.date_debut), tri=AnneeScolaire.date_debut),
        Colonne("fin", "Fin", lambda a: _date(a.date_fin), tri=AnneeScolaire.date_fin),
        Colonne("semestres", "Semestres", lambda a: [s.code for s in a.semestres], apercu=4),
    ),
    tri_defaut="debut",
    recherche=(AnneeScolaire.libelle,),
    chargements=(selectinload(AnneeScolaire.semestres),),
    uniques=(Unicite(("libelle",), "Une année porte déjà ce libellé.", "libelle"),),
    valider=lambda session, v, actuel: _valider_annee(v),
    analyser=_analyser_annee,
    apres_creation=lambda session, annee: proposer_regles(session, annee.id),
    consequences=(
        Decompte(
            par_colonne(Regle.annee_scolaire_id),
            "Sa règle personnalisée sera supprimée avec elle",
            "Ses {n} règles personnalisées seront supprimées avec elle",
            lien="/regles?annee={id}&etat=",
            objets=("cette règle", "ces {n} règles"),
        ),
    ),
)


def creer_annee_par_defaut(session: Session, annee_debut: int) -> dict:
    """Crée l'année scolaire qui commence en `annee_debut`, avec ses deux semestres et ses fériés.

    Dates par défaut : voir `edt.domaine.calendrier.decoupage_par_defaut`.
    Lève `ErreurRegleMetier` si cette année existe déjà.
    """
    if not ANNEE_DEBUT_MIN <= annee_debut <= ANNEE_DEBUT_MAX:
        raise ErreurRegleMetier(
            f"Année invalide : {annee_debut} (entre {ANNEE_DEBUT_MIN} et {ANNEE_DEBUT_MAX})."
        )
    libelle = decoupage_par_defaut(annee_debut).libelle
    if session.scalar(select(AnneeScolaire.id).where(AnneeScolaire.libelle == libelle)):
        raise ErreurRegleMetier(f"L'année scolaire {libelle} existe déjà.")
    annee = assurer_annee_scolaire(session, annee_debut)
    proposees = proposer_regles(session, annee.id)
    journal.info(
        "Année scolaire %s créée depuis l'interface (%d règle(s) proposée(s))",
        libelle,
        proposees,
    )
    return {**lire(session, ANNEE_SCOLAIRE, annee.id), "regles_proposees": proposees}


def proposer_regles(session: Session, annee_scolaire_id: int) -> int:
    """Propose à une année ses règles de base et la reprise de l'année précédente.

    Les règles sont créées à l'état « proposée », sans effet tant qu'on ne les
    confirme pas (écran Règles personnalisées). Renvoie leur nombre.
    """
    return len(proposer_pour_annee(session, annee_scolaire_id).creees)


def preremplir_jours_feries(session: Session, annee_scolaire_id: int) -> int:
    """Ajoute les jours fériés manquants de l'année (fériés mobiles compris) ; renvoie le nombre ajouté."""
    annee = (
        session.get(AnneeScolaire, annee_scolaire_id)
        if identifiant_valide(annee_scolaire_id)
        else None
    )
    if annee is None:
        raise ErreurIntrouvable(f"Année scolaire n° {annee_scolaire_id} introuvable.")
    ajoutes = preremplir_feries(session, annee)
    journal.info("Jours fériés préremplis pour %s : %d ajouté(s)", annee.libelle, ajoutes)
    return ajoutes


# --------------------------------------------------------------------------- #
# Semestres
# --------------------------------------------------------------------------- #

SEMESTRE = Entite(
    code="semestre",
    modele=Semestre,
    nom="semestre",
    pluriel="semestres",
    feminin=False,
    demonstratif="Ce semestre",
    nouveau="Nouveau semestre",
    libelle=lambda s: s.libelle,
    champs=(
        Champ(
            "annee_scolaire_id",
            "Année scolaire",
            TypeChamp.REFERENCE,
            obligatoire=True,
            reference=REF_ANNEE,
        ),
        Champ("code", "Code", obligatoire=True, aide="Par exemple « S1 »."),
        Champ("libelle", "Libellé", obligatoire=True),
        Champ("date_debut", "Premier jour de cours", TypeChamp.DATE, obligatoire=True),
        Champ("date_fin", "Dernier jour de cours", TypeChamp.DATE, obligatoire=True),
    ),
    colonnes=(
        Colonne("annee", "Année", lambda s: s.annee.libelle, tri=AnneeScolaire.date_debut),
        Colonne("code", "Code", lambda s: s.code, tri=Semestre.code),
        Colonne("libelle", "Libellé", lambda s: s.libelle, tri=Semestre.libelle),
        Colonne("debut", "Début", lambda s: _date(s.date_debut), tri=Semestre.date_debut),
        Colonne("fin", "Fin", lambda s: _date(s.date_fin), tri=Semestre.date_fin),
    ),
    tri_defaut="debut",
    recherche=(Semestre.code, Semestre.libelle),
    jointures=((AnneeScolaire, Semestre.annee_scolaire_id == AnneeScolaire.id),),
    chargements=(selectinload(Semestre.annee),),
    uniques=(
        Unicite(("annee_scolaire_id", "code"), "Ce code existe déjà pour cette année.", "code"),
    ),
    valider=lambda session, v, actuel: {
        **_dans_l_annee(session, v, ("date_debut", "date_fin")),
        **_dates_ordonnees(v),
        **_duree_bornee(v, DUREE_SEMESTRE_MAX_JOURS, "Un semestre dure un an au plus"),
    },
    consequences=(
        Decompte(
            par_colonne(Regle.semestre_id),
            "1 règle personnalisée limitée à ce semestre sera supprimée",
            "{n} règles personnalisées limitées à ce semestre seront supprimées",
        ),
        Decompte(
            par_colonne(Besoin.semestre_id),
            "1 besoin sera supprimé",
            "{n} besoins seront supprimés",
        ),
        Decompte(
            par_colonne(VersionEdt.semestre_id),
            "1 version d'emploi du temps sera supprimée",
            "{n} versions d'emploi du temps seront supprimées",
        ),
        Decompte(
            par_colonne(Calcul.semestre_id),
            "1 calcul sera supprimé de l'historique",
            "{n} calculs seront supprimés de l'historique",
        ),
    ),
)


# --------------------------------------------------------------------------- #
# Jours fermés
# --------------------------------------------------------------------------- #

CHOIX_NATURE_FERMETURE = choix_enum(NatureJourFerme)
_Portee = aliased(Groupe)


def texte_periode(debut: dt.date, fin: dt.date) -> str:
    """« le 11/11/2026 » ou « du 26/10/2026 au 01/11/2026 »."""
    if debut == fin:
        return f"le {_date(debut)}"
    return f"du {_date(debut)} au {_date(fin)}"


def _preparer_jour_ferme(session: Session, v: Valeurs) -> Valeurs:
    if v.get("date_fin") is None:
        v["date_fin"] = v.get("date_debut")
    return v


def _valider_jour_ferme(session: Session, v: Valeurs, actuel: JourFerme | None) -> dict[str, str]:
    erreurs = {**_dans_l_annee(session, v, ("date_debut", "date_fin")), **_dates_ordonnees(v)}
    groupe_id = v.get("groupe_id")
    if groupe_id and v.get("annee_scolaire_id"):
        groupe = session.get(Groupe, groupe_id)
        if groupe is not None and groupe.annee_scolaire_id != v["annee_scolaire_id"]:
            erreurs["groupe_id"] = "Le groupe doit être de la même année scolaire."
    return erreurs


JOUR_FERME = Entite(
    code="jour_ferme",
    modele=JourFerme,
    nom="jour fermé",
    pluriel="jours fermés",
    feminin=False,
    demonstratif="Ce jour fermé",
    nouveau="Nouvelle fermeture",
    libelle=lambda j: f"{j.libelle} ({texte_periode(j.date_debut, j.date_fin)})",
    champs=(
        Champ(
            "annee_scolaire_id",
            "Année scolaire",
            TypeChamp.REFERENCE,
            obligatoire=True,
            reference=REF_ANNEE,
        ),
        Champ(
            "libelle", "Libellé", obligatoire=True, aide="Par exemple « Vacances de la Toussaint »."
        ),
        Champ(
            "nature",
            "Nature",
            TypeChamp.CHOIX,
            obligatoire=True,
            defaut=NatureJourFerme.VACANCES.value,
            choix=CHOIX_NATURE_FERMETURE,
            aide="Examens et semaines spéciales gardent les séances ponctuelles ; le reste ferme tout.",
        ),
        Champ("date_debut", "Premier jour", TypeChamp.DATE, obligatoire=True),
        Champ("date_fin", "Dernier jour", TypeChamp.DATE, aide="Vide : un seul jour."),
        Champ(
            "groupe_id",
            "Limité au groupe",
            TypeChamp.REFERENCE,
            reference=REF_GROUPE,
            aide="Vide : tout l'établissement. Sinon le groupe et ses sous-groupes.",
        ),
    ),
    colonnes=(
        Colonne(
            "periode",
            "Période",
            lambda j: texte_periode(j.date_debut, j.date_fin),
            tri=(JourFerme.date_debut, JourFerme.date_fin),
        ),
        Colonne("libelle", "Libellé", lambda j: j.libelle, tri=JourFerme.libelle, texte=True),
        Colonne(
            "nature",
            "Nature",
            lambda j: libelle_choix(CHOIX_NATURE_FERMETURE, j.nature),
            tri=JourFerme.nature,
        ),
        Colonne(
            "portee",
            "Portée",
            lambda j: j.groupe.code if j.groupe else "Tout l'établissement",
            tri=_Portee.code,
        ),
    ),
    tri_defaut="periode",
    recherche=(JourFerme.libelle, _Portee.code),
    jointures=((_Portee, JourFerme.groupe_id == _Portee.id),),
    chargements=(selectinload(JourFerme.groupe),),
    filtres=(
        Filtre(
            "annee",
            "Année",
            lambda s: options_reference(s, REF_ANNEE),
            lambda v: JourFerme.annee_scolaire_id == int(v),
            defaut=lambda s: None if annee_par_defaut(s) is None else str(annee_par_defaut(s)),
            champ="annee_scolaire_id",
        ),
        Filtre(
            "nature",
            "Nature",
            lambda s: [(str(v), libelle) for v, libelle in CHOIX_NATURE_FERMETURE],
            lambda v: JourFerme.nature == v,
            champ="nature",
        ),
        Filtre(
            "portee",
            "Portée",
            lambda s: [("globale", "Tout l'établissement"), ("groupe", "Limitée à un groupe")],
            lambda v: (
                JourFerme.groupe_id.is_(None)
                if v == "globale"
                else JourFerme.groupe_id.is_not(None)
            ),
        ),
    ),
    valider=_valider_jour_ferme,
    preparer=_preparer_jour_ferme,
)


# --------------------------------------------------------------------------- #
# Grille horaire
# --------------------------------------------------------------------------- #

CHOIX_DEMI_JOURNEE = choix_enum(DemiJournee)


def _preparer_creneau(session: Session, v: Valeurs) -> Valeurs:
    if v.get("demi_journee") is None:
        v["demi_journee"] = (
            DemiJournee.MATIN.value if v["debut_min"] < MIDI else DemiJournee.APRES_MIDI.value
        )
    return v


def _valider_creneau(session: Session, v: Valeurs, actuel: Creneau | None) -> dict[str, str]:
    debut, fin = v.get("debut_min"), v.get("fin_min")
    if debut is not None and fin is not None and debut >= fin:
        return {"fin_min": "La fin doit suivre le début."}
    return {}


CRENEAU = Entite(
    code="creneau",
    modele=Creneau,
    nom="créneau",
    pluriel="créneaux",
    feminin=False,
    demonstratif="Ce créneau",
    nouveau="Nouveau créneau",
    libelle=lambda c: f"{JOURS[c.jour]} {minutes_vers_texte(c.debut_min)}",
    champs=(
        Champ("jour", "Jour", TypeChamp.CHOIX, obligatoire=True, defaut=0, choix=choix_jours(6)),
        Champ("debut_min", "Début", TypeChamp.HEURE, obligatoire=True),
        Champ("fin_min", "Fin", TypeChamp.HEURE, obligatoire=True),
        Champ(
            "demi_journee",
            "Demi-journée",
            TypeChamp.CHOIX,
            choix=CHOIX_DEMI_JOURNEE,
            aide="Vide : déduite de l'heure de début (avant midi : matin).",
        ),
    ),
    colonnes=(
        Colonne(
            "jour",
            "Jour",
            lambda c: JOURS[c.jour].capitalize(),
            tri=(Creneau.jour, Creneau.debut_min),
        ),
        Colonne("debut", "Début", lambda c: minutes_vers_texte(c.debut_min), tri=Creneau.debut_min),
        Colonne("fin", "Fin", lambda c: minutes_vers_texte(c.fin_min), tri=Creneau.fin_min),
        Colonne("duree", "Durée", lambda c: duree_vers_texte(c.fin_min - c.debut_min)),
        Colonne(
            "demi_journee",
            "Demi-journée",
            lambda c: libelle_choix(CHOIX_DEMI_JOURNEE, c.demi_journee),
            tri=(Creneau.demi_journee, Creneau.jour, Creneau.debut_min),
        ),
    ),
    tri_defaut="jour",
    filtres=(
        Filtre(
            "jour",
            "Jour",
            lambda s: [(str(v), libelle) for v, libelle in choix_jours(6)],
            lambda v: Creneau.jour == int(v),
            champ="jour",
        ),
    ),
    uniques=(
        Unicite(
            ("jour", "debut_min"), "Un créneau commence déjà à cette heure ce jour-là.", "debut_min"
        ),
    ),
    valider=_valider_creneau,
    preparer=_preparer_creneau,
)


@dataclass(frozen=True)
class ApercuAnnee:
    """Une année scolaire pour la page « Calendrier » : semestres et nombre de fermetures."""

    id: int
    libelle: str
    date_debut: dt.date
    date_fin: dt.date
    semestres: tuple[tuple[int, str, str, dt.date, dt.date], ...]
    nb_jours_fermes: int
    nb_feries: int


def apercu_calendrier(session: Session) -> tuple[ApercuAnnee, ...]:
    """Les années scolaires (les plus récentes d'abord), avec semestres et fermetures."""
    annees = session.scalars(
        select(AnneeScolaire)
        .options(selectinload(AnneeScolaire.semestres))
        .order_by(AnneeScolaire.date_debut.desc())
    ).all()
    resultat = []
    for annee in annees:
        fermetures = select(func.count()).where(JourFerme.annee_scolaire_id == annee.id)
        resultat.append(
            ApercuAnnee(
                id=annee.id,
                libelle=annee.libelle,
                date_debut=annee.date_debut,
                date_fin=annee.date_fin,
                semestres=tuple(
                    (s.id, s.code, s.libelle, s.date_debut, s.date_fin) for s in annee.semestres
                ),
                nb_jours_fermes=session.scalar(fermetures) or 0,
                nb_feries=session.scalar(
                    fermetures.where(JourFerme.nature == NatureJourFerme.FERIE.value)
                )
                or 0,
            )
        )
    return tuple(resultat)
