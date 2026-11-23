"""Recette de l'import, de l'export et de l'impression, par les écrans de l'application.

Chaque test passe par le client HTTP de l'application, sur une base d'essai
chargée du jeu minimal. L'emploi du temps utilisé est la version saisie à la
main (`tests.outils.verification.versions`), jamais une solution du moteur :
les résultats ne dépendent ni de la machine ni du système.
"""

from __future__ import annotations

import html
import re
from collections import Counter

from sqlalchemy import func, select
from tests.outils.verification.versions import enregistrer_version

from edt.db import modeles as m
from edt.db.moteur import session_base

TABLES_DE_DONNEES = (
    "campus",
    "trajet",
    "salle",
    "salle_equipement",
    "enseignant",
    "groupe",
    "groupe_chevauchement",
    "etudiant",
    "inscription",
    "matiere",
    "besoin",
    "besoin_groupe",
    "besoin_enseignant",
    "besoin_type_salle",
    "besoin_equipement",
    "indisponibilite",
    "regle",
    "jour_ferme",
    "annee_scolaire",
    "semestre",
)
"""Tables des données de l'établissement, qu'un export complet puis un réimport parcourent."""


def _comptes() -> dict[str, int]:
    with session_base() as session:
        return {
            nom: session.scalar(select(func.count()).select_from(m.Base.metadata.tables[nom]))
            for nom in TABLES_DE_DONNEES
        }


def _envoi(nom: str, contenu: bytes, **donnees: str) -> dict:
    return {"files": [("fichiers", (nom, contenu, "application/octet-stream"))], "data": donnees}


def _bilan(page: str) -> str:
    """Le tableau « Bilan » du compte rendu d'import."""
    debut = page.index("<h3>Bilan</h3>")
    return page[debut : page.index("</table>", debut)]


def _version_saisie(jeu_minimal) -> int:
    with session_base() as session:
        return enregistrer_version(session, jeu_minimal.semestre_id)


def _places(version_id: int) -> Counter:
    """(jour, début, salle, verrou) de chaque séance de la semaine type d'une version."""
    with session_base() as session:
        seances = session.scalars(
            select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == version_id)
        )
        return Counter((s.jour, s.debut_min, s.salle.code, s.verrou) for s in seances)


def _versions() -> list[tuple[int, str, bool]]:
    with session_base() as session:
        return [
            (v.id, v.libelle, v.active)
            for v in session.scalars(select(m.VersionEdt).order_by(m.VersionEdt.id))
        ]


# --------------------------------------------------------------------------- #
# U13 : réimport appliqué des données de l'établissement
# --------------------------------------------------------------------------- #


def test_export_complet_reimporte_deux_fois_par_l_ecran_sans_doublon(jeu_minimal, client):
    """L'utilisateur exporte toutes les données, puis les réimporte deux fois en appliquant.

    Chaque réimport est appliqué par l'écran « Import » (pas simulé) ; après chacun,
    le nombre de lignes de chaque table est celui d'avant, et le bilan du compte rendu
    ne donne, pour chacune des quatorze feuilles, que des lignes inchangées.
    """
    export = client.get("/export/donnees.xlsx")
    assert export.status_code == 200
    avant = _comptes()
    assert avant["salle"] == jeu_minimal.nb_salles and avant["besoin"] > 0

    for passage in (1, 2):
        reponse = client.post(
            "/import/appliquer",
            **_envoi("donnees-etablissement.xlsx", export.content, mode="donnees"),
        )
        page = html.unescape(reponse.text)
        assert reponse.status_code == 200, passage
        assert "Aucune ligne rejetée." in page, passage
        bilan = re.findall(r"<tr>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*</tr>", _bilan(page))
        assert len(bilan) == 14, (passage, bilan)  # une ligne par feuille du format
        assert all(re.fullmatch(r"\d+ inchangés?", texte) for _, texte in bilan), (passage, bilan)
        assert _comptes() == avant, passage


# --------------------------------------------------------------------------- #
# U13 : import d'un emploi du temps existant (iCal, verrous, à replacer)
# --------------------------------------------------------------------------- #


def test_agenda_ical_exporte_puis_reimporte_comme_reference(jeu_minimal, client):
    """L'utilisateur exporte un emploi du temps vers un agenda, puis le réimporte comme référence.

    Le fichier iCal de la semaine type est appliqué en mode « Emploi du temps de
    référence » : une nouvelle version non active est créée, ses séances occupent
    exactement les créneaux et les salles de la version exportée, ses séances datées
    sont créées, et la version active reste celle d'avant.
    """
    source = _version_saisie(jeu_minimal)
    agenda = client.get(
        "/export/emploi-du-temps.ics", params={"version_id": source, "couche": "hebdo"}
    )
    assert agenda.status_code == 200 and agenda.text.count("BEGIN:VEVENT") > 0

    reponse = client.post(
        "/import/appliquer",
        **_envoi(
            "agenda.ics", agenda.content, mode="reference", semestre_id=str(jeu_minimal.semestre_id)
        ),
    )

    assert reponse.status_code == 200
    versions = _versions()
    assert len(versions) == 2
    (ancienne, _, ancienne_active), (nouvelle, libelle, nouvelle_active) = versions
    assert ancienne == source and ancienne_active and not nouvelle_active
    assert libelle.startswith("Référence importée")
    # Mêmes créneaux et mêmes salles (une séance ponctuelle partage le créneau d'une autre).
    assert set(_places(nouvelle)) == set(_places(source))
    with session_base() as session:
        datees = session.scalar(
            select(func.count())
            .select_from(m.Occurrence)
            .where(m.Occurrence.version_id == nouvelle)
        )
    assert datees > 0


def test_import_en_seances_verrouillees_par_l_ecran(jeu_minimal, client):
    """L'utilisateur importe un emploi du temps existant en « séances verrouillées ».

    Le tableau CSV de la semaine type est appliqué : une nouvelle version devient la
    version active du semestre, avec chaque séance à la même place qu'à l'export et
    verrouillée (le générateur la gardera à sa place).
    """
    source = _version_saisie(jeu_minimal)
    tableau = client.get(
        "/export/emploi-du-temps.csv", params={"version_id": source, "couche": "hebdo"}
    )

    reponse = client.post(
        "/import/appliquer",
        **_envoi(
            "seances.csv", tableau.content, mode="verrous", semestre_id=str(jeu_minimal.semestre_id)
        ),
    )

    assert reponse.status_code == 200
    (ancienne, _, ancienne_active), (nouvelle, libelle, nouvelle_active) = _versions()
    assert ancienne == source and not ancienne_active and nouvelle_active
    assert libelle.startswith("Verrous importés")
    attendu = Counter(
        {(jour, debut, salle, True): n for (jour, debut, salle, _), n in _places(source).items()}
    )
    assert _places(nouvelle) == attendu


def test_import_en_seances_a_replacer_ne_cree_que_les_besoins(jeu_minimal, client):
    """L'utilisateur importe un emploi du temps existant en « séances à replacer ».

    L'agenda iCal est appliqué : les besoins sont repris (créés ou mis à jour), mais
    aucune version ni aucune séance n'est créée ; le générateur placera tout.
    """
    source = _version_saisie(jeu_minimal)
    agenda = client.get(
        "/export/emploi-du-temps.ics", params={"version_id": source, "couche": "hebdo"}
    )
    besoins_avant = _comptes()["besoin"]
    with session_base() as session:
        seances_avant = session.scalar(select(func.count()).select_from(m.SeanceHebdo))

    reponse = client.post(
        "/import/appliquer",
        **_envoi(
            "agenda.ics",
            agenda.content,
            mode="a_replacer",
            semestre_id=str(jeu_minimal.semestre_id),
        ),
    )

    page = html.unescape(reponse.text)
    assert reponse.status_code == 200
    assert re.search(r"<td>Besoins</td>\s*<td>[^<]*inchangés</td>", _bilan(page))
    assert [v[0] for v in _versions()] == [source]
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.SeanceHebdo)) == seances_avant
    assert _comptes()["besoin"] >= besoins_avant


# --------------------------------------------------------------------------- #
# U12 : impression et agenda depuis l'écran « Emploi du temps »
# --------------------------------------------------------------------------- #


def test_ecran_emploi_du_temps_imprimer_et_agenda_de_la_ressource(jeu_minimal, client):
    """L'utilisateur affiche l'emploi du temps d'un enseignant, l'imprime ou l'envoie à son agenda.

    La page porte le bouton « Imprimer » et un titre réservé à l'impression qui nomme
    l'enseignant ; le lien « Exporter vers un agenda » répond par un fichier iCal qui
    ne contient que les séances de cet enseignant. Un test par HTTP ne peut pas
    vérifier la boîte d'impression du navigateur ni la mise en page imprimée (règles
    « @media print ») : cet essai reste manuel.
    """
    version = _version_saisie(jeu_minimal)
    with session_base() as session:
        enseignant = session.scalar(
            select(m.Enseignant).where(m.Enseignant.identifiant == "ENS003")
        )
        ident, nom = enseignant.id, enseignant.nom

    page = client.get("/emploi-du-temps", params={"version_id": version, "enseignant_id": ident})

    assert page.status_code == 200
    texte = html.unescape(page.text)
    assert '<button type="button" class="bouton" data-imprimer>Imprimer</button>' in texte
    impression = re.search(r'<div class="edt-impression-seulement">\s*<h1>([^<]*)</h1>', texte)
    assert impression and nom in impression.group(1)
    lien = re.search(r'<a class="bouton" href="([^"]*)"[^>]*>Exporter vers un agenda</a>', texte)
    assert lien and lien.group(1).startswith("/export/emploi-du-temps.ics?")

    agenda = client.get(lien.group(1))
    tout = client.get(
        "/export/emploi-du-temps.ics", params={"version_id": version, "couche": "hebdo"}
    )
    assert agenda.status_code == 200 and agenda.headers["content-type"].startswith("text/calendar")
    assert "attachment;" in agenda.headers["content-disposition"]
    evenements = agenda.text.split("BEGIN:VEVENT")[1:]
    assert 0 < len(evenements) < tout.text.count("BEGIN:VEVENT")
    assert all("ENS003" in evenement for evenement in evenements)
