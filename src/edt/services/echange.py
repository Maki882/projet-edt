"""Service d'échange de données par fichiers : gabarit, imports, exports.

Le web (`edt.web.routes.echange`) et la ligne de commande
(`edt.commandes.echange`) n'appellent que ces fonctions. Elles reçoivent la
session de l'appelant, ne la valident jamais et renvoient des objets typés
sérialisables en JSON (`vers_dict`).

    gabarit = produire_gabarit(session, "xlsx")
    rapport = simuler_import(session, fichiers, OptionsImport())      # n'écrit rien
    rapport = appliquer_import(session, fichiers, OptionsImport())
    fichier = exporter_donnees(session, "zip")
    fichier = exporter_emploi_du_temps(session, version_id, "ics", couche="date", groupe_id=3)

Un import est toujours exécuté de la même façon ; la simulation annule ses
écritures à la fin (point de sauvegarde), le bilan qu'elle affiche est donc
celui que donnera l'application. Un import est refusé pendant qu'un calcul
tourne ; en retour, `import_en_cours()` dit au lancement d'un calcul qu'un
import tourne dans le processus.

Les fichiers sont lus entièrement (tableaux, puis récurrences iCal) avant
l'ouverture de la transaction de l'import : la base n'est réservée en
écriture que le temps d'écrire (`edt.db.moteur.transaction_immediate`, appelé
par `transaction.simulation_possible`). La session reçue ne doit donc avoir
servi à rien : ce que l'import lit avant sa transaction (le semestre) se lit
dans une session à part.
"""

from __future__ import annotations

import base64
import datetime as dt
import logging
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from edt.db import modeles as m
from edt.domaine.enumerations import Gravite
from edt.echange import donnees, export_edt
from edt.echange.ecriture import archive_zip, classeur_xlsx
from edt.echange.export_edt import Couche, Filtre
from edt.echange.format import FEUILLES, identifier_feuille, texte_lisez_moi
from edt.echange.ical import BudgetRecurrences, lire_ical
from edt.echange.lecture import FichierEnvoye, Limites, lire_fichier
from edt.echange.rapport import (
    Bilan,
    ListeSignalements,
    RapportImport,
    Signalement,
    rapport_csv,
)
from edt.echange.reconstitution import reconstituer
from edt.echange.seances import ModeImport, SeanceLue, lire_tableau_seances
from edt.echange.transaction import simulation_possible
from edt.echange.valeurs import lire_choix, lire_date, lire_entier
from edt.erreurs import (
    ErreurCalculEnCours,
    ErreurDonneesInvalides,
    ErreurIntrouvable,
    ErreurRegleMetier,
)
from edt.services.calendrier import semestre_par_defaut
from edt.services.generation import calcul_en_cours

journal = logging.getLogger(__name__)

TAILLE_MAX_OCTETS = 20 * 1024 * 1024
"""Taille maximale d'un envoi (tous fichiers confondus)."""

__all__ = [
    "TAILLE_MAX_OCTETS",
    "ChoixExport",
    "Couche",
    "FichierEnvoye",
    "FichierProduit",
    "FormatDonnees",
    "FormatEdt",
    "ModeImport",
    "OptionsImport",
    "RapportImport",
    "appliquer_import",
    "choix_export",
    "compter_remplacement",
    "exporter_donnees",
    "exporter_emploi_du_temps",
    "import_en_cours",
    "lire_options",
    "produire_gabarit",
    "rapport_telechargeable",
    "resoudre_filtre",
    "semestres_importables",
    "simuler_import",
    "verifier_taille",
]


class FormatDonnees(StrEnum):
    """Présentation du format d'échange : classeur à onglets ou archive de CSV."""

    XLSX = "xlsx"
    ZIP = "zip"


class FormatEdt(StrEnum):
    """Format d'export d'un emploi du temps."""

    CSV = "csv"
    ICS = "ics"


TYPES_MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
    "csv": "text/csv; charset=utf-8",
    "ics": "text/calendar; charset=utf-8",
}


@dataclass(frozen=True)
class FichierProduit:
    """Un fichier produit par le service : son nom, son type MIME et son contenu."""

    nom: str
    type_mime: str
    contenu: bytes

    def vers_dict(self) -> dict:
        """Représentation JSON (contenu en base64)."""
        return {
            "nom": self.nom,
            "type_mime": self.type_mime,
            "taille": len(self.contenu),
            "contenu_base64": base64.b64encode(self.contenu).decode("ascii"),
        }


@dataclass(frozen=True)
class OptionsImport:
    """Réglages d'un import.

    - `mode` : données de l'établissement, ou emploi du temps existant
      (référence, verrous, à replacer) ;
    - `semestre_id` : semestre de l'emploi du temps importé (défaut : le
      semestre proposé par défaut) ;
    - `semaine_type` : une date de la semaine à retenir comme semaine type
      (défaut : la plus fréquente de chaque promotion) ;
    - `tout_remplacer` : vider les données de l'établissement avant d'importer ;
    - `confirmer_remplacement` : confirmation explicite, exigée pour appliquer
      un « tout remplacer ».
    """

    mode: ModeImport = ModeImport.DONNEES
    semestre_id: int | None = None
    semaine_type: dt.date | None = None
    tout_remplacer: bool = False
    confirmer_remplacement: bool = False

    def vers_dict(self) -> dict:
        """Représentation JSON."""
        return {
            "mode": self.mode.value,
            "semestre_id": self.semestre_id,
            "semaine_type": self.semaine_type.isoformat() if self.semaine_type else None,
            "tout_remplacer": self.tout_remplacer,
            "confirmer_remplacement": self.confirmer_remplacement,
        }


@dataclass(frozen=True)
class ChoixExport:
    """Listes proposées par l'écran d'export : versions, groupes, enseignants, salles.

    `version_par_defaut` : la version à présélectionner (voir `choix_export`).
    """

    versions: tuple[tuple[int, str], ...]
    groupes: tuple[tuple[int, str], ...]
    enseignants: tuple[tuple[int, str], ...]
    salles: tuple[tuple[int, str], ...]
    version_par_defaut: int | None = None

    def vers_dict(self) -> dict:
        """Représentation JSON."""
        listes = {
            nom: [{"id": i, "libelle": libelle} for i, libelle in getattr(self, nom)]
            for nom in ("versions", "groupes", "enseignants", "salles")
        }
        return listes | {"version_par_defaut": self.version_par_defaut}


def lire_options(
    mode: str | None,
    semestre_id: int | str | None = None,
    semaine_type: str | dt.date | None = None,
    tout_remplacer: bool = False,
    confirmer_remplacement: bool = False,
) -> OptionsImport:
    """Options d'import à partir de saisies (formulaire web, ligne de commande).

    `mode` est reconnu sans casse ni accents (« a-replacer » vaut
    « a_replacer ») ; `semaine_type` est une date « JJ/MM/AAAA » ou ISO ;
    une saisie vide vaut « non renseigné ».
    """
    try:
        mode_import = lire_choix(mode, ModeImport) or ModeImport.DONNEES
        semestre = lire_entier(semestre_id)
        semaine = lire_date(semaine_type)
    except ErreurDonneesInvalides as erreur:
        raise ErreurDonneesInvalides(f"Option d'import invalide : {erreur.message}") from None
    return OptionsImport(
        mode=mode_import,
        semestre_id=semestre,
        semaine_type=semaine,
        tout_remplacer=tout_remplacer,
        confirmer_remplacement=confirmer_remplacement,
    )


# --------------------------------------------------------------------------- #
# Gabarit et export des données
# --------------------------------------------------------------------------- #


def produire_gabarit(session: Session, format: str = FormatDonnees.XLSX) -> FichierProduit:
    """Gabarit vide : toutes les feuilles avec leurs en-têtes, et les explications."""
    format_ = _format_donnees(format)
    contenu = [(feuille, []) for feuille in FEUILLES]
    return _fichier_donnees(session, contenu, format_, "gabarit-edt")


def exporter_donnees(session: Session, format: str = FormatDonnees.XLSX) -> FichierProduit:
    """Toutes les données de l'établissement dans le format d'échange (réimportable)."""
    format_ = _format_donnees(format)
    contenu = donnees.exporter_donnees(session)
    fichier = _fichier_donnees(session, contenu, format_, f"donnees-edt-{dt.date.today():%Y-%m-%d}")
    journal.info(
        "Export des données (%s) : %s",
        format_.value,
        ", ".join(f"{len(lignes)} {feuille.nom}" for feuille, lignes in contenu),
    )
    return fichier


def _format_donnees(format: str) -> FormatDonnees:
    try:
        return FormatDonnees(str(format).lower())
    except ValueError:
        raise ErreurDonneesInvalides(
            f"Format {format!r} inconnu pour les données (formats : xlsx, zip)."
        ) from None


def _fichier_donnees(
    session: Session, contenu, format_: FormatDonnees, base: str
) -> FichierProduit:
    listes = {
        "Types de salle": [
            (t.code, t.libelle)
            for t in session.scalars(select(m.TypeSalle).order_by(m.TypeSalle.code))
        ],
        "Équipements": [
            (e.code, e.libelle)
            for e in session.scalars(select(m.Equipement).order_by(m.Equipement.code))
        ],
        "Types de séance": [
            (t.code, t.libelle)
            for t in session.scalars(select(m.TypeSeance).order_by(m.TypeSeance.code))
        ],
    }
    lisez_moi = texte_lisez_moi(listes)
    if format_ is FormatDonnees.XLSX:
        octets = classeur_xlsx(contenu, lisez_moi)
    else:
        octets = archive_zip(contenu, lisez_moi)
    return FichierProduit(f"{base}.{format_.value}", TYPES_MIME[format_.value], octets)


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #


def verifier_taille(tailles: Sequence[int]) -> None:
    """Refuse un envoi de plus de 20 Mo, avec un message clair."""
    total = sum(tailles)
    if total > TAILLE_MAX_OCTETS:
        raise ErreurDonneesInvalides(
            f"Envoi trop volumineux : {total / 1024 / 1024:.1f} Mo reçus, 20 Mo au plus. "
            "Découper l'import en plusieurs envois (par exemple une feuille à la fois)."
        )


_verrou_imports = threading.Lock()
_imports_actifs = 0
"""Imports (simulés ou appliqués) en train de tourner dans ce processus."""


def import_en_cours() -> bool:
    """Vrai si un import, simulé ou appliqué, tourne dans ce processus.

    Le lancement d'un calcul le consulte : un calcul ne doit pas lire des
    données en train d'être importées, de même qu'un import est refusé
    pendant un calcul. Une simulation compte aussi : elle réserve la base en
    écriture.

    Limite : le compteur est propre au processus. Un import lancé en ligne
    de commande pendant que le serveur lance un calcul n'est pas vu ici (à la
    différence de `services.jeux.operation_en_cours`, qui pose un fichier
    marqueur) ; le verrou d'écriture de la base sérialise alors les deux, et
    le calcul peut lire les données d'avant ou d'après l'import.
    """
    return _imports_actifs > 0


@contextmanager
def _import_signale() -> Iterator[None]:
    """Compte l'import comme en cours pendant le bloc, même s'il échoue.

    Un compteur et non un booléen : deux imports peuvent tourner ensemble
    (ils se suivent sur la base).
    """
    global _imports_actifs
    with _verrou_imports:
        _imports_actifs += 1
    try:
        yield
    finally:
        with _verrou_imports:
            _imports_actifs -= 1


def simuler_import(
    session: Session, fichiers: Sequence[FichierEnvoye], options: OptionsImport
) -> RapportImport:
    """Fait tout l'import puis l'annule : le rapport et le bilan, sans rien écrire."""
    with _import_signale():
        return _importer(session, fichiers, options, simuler=True)


def appliquer_import(
    session: Session, fichiers: Sequence[FichierEnvoye], options: OptionsImport
) -> RapportImport:
    """Importe pour de bon : les lignes valides entrent, les autres sont listées.

    Ne valide pas la transaction (l'appelant le fait). « Tout remplacer »
    exige `options.confirmer_remplacement`.
    """
    with _import_signale():
        return _importer(session, fichiers, options, simuler=False)


def _semestre_vise(session: Session, options: OptionsImport) -> tuple[int, dt.date, dt.date]:
    """Semestre d'un import d'emploi du temps : identifiant, premier et dernier jour.

    Lu dans une session à part, aussitôt fermée : la session de l'import ne
    doit servir à rien avant l'ouverture de sa transaction, qui réserve la base
    en écriture dès son début (`transaction.simulation_possible`) ; entre les
    deux, les fichiers sont lus sans rien réserver.
    """
    with Session(session.get_bind()) as lecture:
        semestre = (
            donnees.obtenir_par_id(lecture, m.Semestre, options.semestre_id)
            if options.semestre_id is not None
            else semestre_par_defaut(lecture)
        )
        if semestre is None:
            raise ErreurIntrouvable(f"Le semestre {options.semestre_id} n'existe pas.")
        return semestre.id, semestre.date_debut, semestre.date_fin


def compter_remplacement(session: Session) -> dict[str, int]:
    """Ce que « tout remplacer » supprimerait, par libellé (pour la demande de confirmation)."""
    return donnees.compter_donnees(session)


def semestres_importables(session: Session) -> list[tuple[int, str]]:
    """Semestres proposés pour importer un emploi du temps, du plus récent au plus ancien."""
    return [
        (s.id, s.libelle)
        for s in session.scalars(select(m.Semestre).order_by(m.Semestre.date_debut.desc()))
    ]


def _importer(
    session: Session, fichiers: Sequence[FichierEnvoye], options: OptionsImport, simuler: bool
) -> RapportImport:
    if not fichiers:
        raise ErreurDonneesInvalides(
            "Aucun fichier envoyé : choisir au moins un fichier à importer."
        )
    verifier_taille([len(f.contenu) for f in fichiers])
    if options.tout_remplacer and options.mode is not ModeImport.DONNEES:
        raise ErreurDonneesInvalides(
            "« Tout remplacer » ne vaut que pour l'import des données de l'établissement."
        )
    if options.tout_remplacer and not simuler and not options.confirmer_remplacement:
        raise ErreurRegleMetier(
            "« Tout remplacer » supprime d'abord toutes les données de l'établissement : "
            "le confirmer explicitement pour appliquer l'import."
        )
    # Par le service de génération : un calcul abandonné par son processus est d'abord clos.
    en_cours = calcul_en_cours()
    if en_cours is not None:
        raise ErreurCalculEnCours(
            f"Un calcul d'emploi du temps est en cours (n° {en_cours.id}) : l'import est refusé "
            "jusqu'à sa fin, car il modifierait les données que le calcul utilise."
        )

    journal.info(
        "%s d'import (%s) : %s",
        "Simulation" if simuler else "Application",
        options.mode.value,
        ", ".join(f.nom for f in fichiers),
    )
    signalements = ListeSignalements()
    limites = Limites()
    tableaux = []
    calendriers: list[tuple[str, str]] = []
    for fichier in fichiers:
        lu, lus = lire_fichier(fichier, limites)
        signalements.extend(lus)
        tableaux.extend(lu.tableaux)
        if lu.texte_ical is not None:
            calendriers.append((lu.nom, lu.texte_ical))

    # Tout ce qui se lit sans écrire est lu avant la transaction : le développement des
    # récurrences iCal (jusqu'à DELAI_RECURRENCES secondes) ne réserve pas la base. La
    # session de l'import, elle, ne sert pas avant sa transaction (voir _semestre_vise).
    semestre_id = None
    bilan_seances = Bilan("séances")
    seances: list[SeanceLue] = []
    if options.mode is ModeImport.DONNEES:
        for nom, _ in calendriers:
            signalements.append(
                Signalement(
                    Gravite.ERREUR,
                    "Un fichier iCal décrit un emploi du temps : choisir un mode d'import "
                    "d'emploi du temps (référence, verrous ou à replacer).",
                    fichier=nom,
                )
            )
    else:
        semestre_id, debut_semestre, fin_semestre = _semestre_vise(session, options)
        for tableau in tableaux:
            seances.extend(lire_tableau_seances(tableau, bilan_seances, signalements))
        budget = BudgetRecurrences()  # un seul temps de calcul pour tous les fichiers iCal
        for nom, texte in calendriers:
            lues, signales = lire_ical(
                nom,
                texte,
                (debut_semestre, fin_semestre),
                max_seances=limites.lignes_restantes,
                budget=budget,
            )
            limites.lignes_restantes -= len(lues)
            bilan_seances.ajouter(
                "rejetes", sum(1 for s in signales if s.gravite is Gravite.ERREUR)
            )
            seances.extend(lues)
            signalements.extend(signales)

    bilans: list[Bilan] = []
    version_id = None
    semaines_types: tuple[str, ...] = ()
    besoins: tuple[dict, ...] = ()
    with simulation_possible(session, simuler):
        if options.mode is ModeImport.DONNEES:
            if options.tout_remplacer:
                bilans.append(_remplacer(session, signalements))
            importes, _ = donnees.importer_tableaux(session, tableaux, signalements)
            bilans.extend(importes)
            apercus = tuple(
                t.apercu(f.nom if (f := identifier_feuille(t.nom, t.entetes)) else t.nom)
                for t in tableaux
            )
        else:
            resultat = reconstituer(
                session,
                seances,
                options.mode,
                semestre_id,
                semaine_type=options.semaine_type,
                fichiers=[f.nom for f in fichiers],
                bilan_seances=bilan_seances,
                signalements=signalements,
            )
            bilans.extend(resultat.bilans)
            version_id = None if simuler else resultat.version_id
            semaines_types = resultat.semaines_types
            besoins = resultat.besoins
            apercus = tuple(t.apercu("séances") for t in tableaux)

    rapport = RapportImport(
        mode=options.mode.value,
        simulation=simuler,
        fichiers=tuple(f.nom for f in fichiers),
        bilans=tuple(bilans),
        signalements=tuple(signalements),
        apercus=apercus,
        version_id=version_id,
        semaines_types=semaines_types,
        besoins=besoins,
        signalements_omis=signalements.omis,
        erreurs_omises=signalements.erreurs_omises,
    )
    niveau = logging.WARNING if rapport.a_des_rejets else logging.INFO
    journal.log(
        niveau,
        "%s terminé(e) : %s ; %d erreur(s) dans le rapport",
        "Simulation" if simuler else "Import",
        " | ".join(f"{b.objet} : {b.texte}" for b in bilans) or "rien à importer",
        rapport.nb_erreurs,
    )
    return rapport


def _remplacer(session: Session, signalements: list[Signalement]) -> Bilan:
    comptes = donnees.vider_donnees(session)
    bilan = Bilan("tout remplacer")
    bilan.ajouter("supprimes", sum(comptes.values()))
    detail = ", ".join(f"{n} {libelle}" for libelle, n in comptes.items() if n) or "rien"
    signalements.append(
        Signalement(
            Gravite.AVERTISSEMENT, f"Tout remplacer : données supprimées avant l'import ({detail})."
        )
    )
    return bilan


def rapport_telechargeable(rapport: RapportImport) -> FichierProduit:
    """Le rapport d'un import en CSV, à télécharger."""
    nature = "simulation" if rapport.simulation else "import"
    return FichierProduit(
        f"rapport-{nature}-{dt.datetime.now():%Y-%m-%d-%H%M}.csv",
        TYPES_MIME["csv"],
        rapport_csv(rapport),
    )


# --------------------------------------------------------------------------- #
# Export d'un emploi du temps
# --------------------------------------------------------------------------- #


def exporter_emploi_du_temps(
    session: Session,
    version_id: int,
    format: str = FormatEdt.CSV,
    couche: str = Couche.HEBDO,
    *,
    groupe_id: int | None = None,
    enseignant_id: int | None = None,
    salle_id: int | None = None,
) -> FichierProduit:
    """Emploi du temps d'une version en CSV ou en iCal, entier ou pour une seule ressource.

    Lève `ErreurDonneesInvalides` pour un format, une couche ou des filtres
    invalides (au plus un filtre), `ErreurIntrouvable` pour une version ou
    une ressource inconnue.
    """
    try:
        format_ = FormatEdt(str(format).lower())
    except ValueError:
        raise ErreurDonneesInvalides(f"Format {format!r} inconnu (formats : csv, ics).") from None
    try:
        couche_ = Couche(str(couche).lower())
    except ValueError:
        raise ErreurDonneesInvalides(
            f"Couche {couche!r} inconnue (couches : hebdo, date)."
        ) from None
    filtre = Filtre(groupe_id=groupe_id, enseignant_id=enseignant_id, salle_id=salle_id)
    export = export_edt.charger(session, version_id, couche_, filtre)
    if format_ is FormatEdt.CSV:
        contenu = export_edt.en_csv(export)
    else:
        contenu = export_edt.en_ical(session, export)
    nom = export_edt.nom_fichier(export, format_.value)
    journal.info("Export de l'emploi du temps : %s (%d séance(s))", nom, len(export.elements))
    return FichierProduit(nom, TYPES_MIME[format_.value], contenu)


def resoudre_filtre(
    session: Session,
    version_id: int,
    *,
    groupe: str | None = None,
    enseignant: str | None = None,
    salle: str | None = None,
) -> dict[str, int | None]:
    """Identifiants des ressources désignées par leur code (ligne de commande).

    Le groupe est cherché dans l'année du semestre de la version ; la salle
    s'écrit « code » ou « code@campus ». Renvoie les paramètres `groupe_id`,
    `enseignant_id` et `salle_id` d'`exporter_emploi_du_temps`.
    """
    version = donnees.obtenir_par_id(session, m.VersionEdt, version_id)
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps {version_id} n'existe pas.")
    contexte = donnees.Contexte(session)
    try:
        return {
            "groupe_id": (
                contexte.groupe(version.semestre.annee, groupe, "groupe").id if groupe else None
            ),
            "enseignant_id": (
                contexte.enseignant(enseignant, "enseignant").id if enseignant else None
            ),
            "salle_id": contexte.salle(salle, "salle").id if salle else None,
        }
    except donnees.Rejet as rejet:
        raise ErreurIntrouvable(rejet.message) from None


def choix_export(session: Session, aujourdhui: dt.date | None = None) -> ChoixExport:
    """Versions et ressources proposées par l'écran d'export.

    La version présélectionnée est la version active du semestre proposé par
    défaut (celui du jour) ; à défaut, la version active la plus récente ; à
    défaut, la première de la liste.
    """
    versions = list(
        session.scalars(
            select(m.VersionEdt)
            .options(selectinload(m.VersionEdt.semestre))
            .order_by(m.VersionEdt.semestre_id.desc(), m.VersionEdt.numero.desc())
        )
    )
    groupes = session.scalars(
        select(m.Groupe).options(selectinload(m.Groupe.annee)).order_by(m.Groupe.code)
    )
    salles = session.scalars(
        select(m.Salle).options(selectinload(m.Salle.campus)).order_by(m.Salle.code)
    )
    return ChoixExport(
        versions=tuple(
            (
                v.id,
                f"{v.semestre.libelle} - {v.libelle}" + (" (active)" if v.active else ""),
            )
            for v in versions
        ),
        groupes=tuple((g.id, f"{g.code} - {g.libelle} ({g.annee.libelle})") for g in groupes),
        enseignants=tuple(
            (e.id, f"{e.nom_affiche} ({e.identifiant})")
            for e in session.scalars(select(m.Enseignant).order_by(m.Enseignant.nom))
        ),
        salles=tuple((s.id, f"{s.code} ({s.campus.code})") for s in salles),
        version_par_defaut=_version_par_defaut(session, versions, aujourdhui),
    )


def _version_par_defaut(
    session: Session, versions: list[m.VersionEdt], aujourdhui: dt.date | None
) -> int | None:
    if not versions:
        return None
    actives = [v for v in versions if v.active]
    try:
        semestre_id = semestre_par_defaut(session, aujourdhui).id
    except ErreurIntrouvable:
        journal.info("Aucun semestre : la version active la plus récente est présélectionnée")
        semestre_id = None
    for version in actives:
        if version.semestre_id == semestre_id:
            return version.id
    return actives[0].id if actives else versions[0].id
