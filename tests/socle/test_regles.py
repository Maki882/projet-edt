"""Règles personnalisées, côté domaine : catalogue, validation, phrases, résolution.

Les cas de précédence des jours sont ceux de `docs/regles-personnalisees.md`
(§ 1.3) : standard, groupe qui ferme, groupe qui ouvre le samedi, enseignant
sans règle, deux lignées qui se croisent.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from edt.domaine.calendrier import semaines_entre
from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.depliage import deplier
from edt.domaine.enumerations import Accessibilite, DemiJournee, NatureGroupe, StatutEnseignant
from edt.domaine.instantane import (
    Besoin,
    Campus,
    Creneau,
    Enseignant,
    Groupe,
    Instantane,
    Salle,
)
from edt.domaine.parametres import Parametres
from edt.domaine.regles import (
    CATALOGUE_TYPES,
    REGLES_DE_BASE,
    ErreurSaisie,
    EtatRegle,
    FamilleRegle,
    NaturePersonne,
    NaturePortee,
    OrigineRegle,
    Personne,
    Portee,
    Regle,
    SorteRegle,
    TypeRegle,
    decrire_catalogue,
    definition_type,
    enregistrer,
    phrase,
    resoudre_regle,
    types_non_couverts,
    valider_parametres,
    valider_portee,
    valider_regle,
)
from edt.erreurs import ErreurDonneesInvalides

H = 60
SAMEDI = 5
LUNDI_A_VENDREDI = ["lundi", "mardi", "mercredi", "jeudi", "vendredi"]
PARAMETRES = Parametres()


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #


def test_le_catalogue_couvre_les_quatorze_types():
    assert len(TypeRegle) == 14
    assert set(CATALOGUE_TYPES) == set(TypeRegle)
    for code, definition in CATALOGUE_TYPES.items():
        assert definition.code is code and definition.libelle
        assert definition.description and definition.exemples
        assert definition.portees  # au moins une portée admise
        assert definition.niveau_defaut in Niveau
        assert all(champ.libelle for champ in definition.champs)
    # Les réglages (exceptions à un paramètre global) refusent la portée établissement.
    for code in (TypeRegle.JOURS_TRAVAILLES, TypeRegle.PAUSE_DEJEUNER, TypeRegle.PAUSE_MINIMALE):
        assert CATALOGUE_TYPES[code].famille is FamilleRegle.REGLAGE
        with pytest.raises(ErreurSaisie, match="paramètre global"):
            valider_portee(code, NaturePortee.ETABLISSEMENT)
    assert definition_type("SANS_TROU").sorte is SorteRegle.EMPLOI_DU_TEMPS
    with pytest.raises(ErreurDonneesInvalides, match="Type de règle inconnu"):
        definition_type("INCONNU")


def test_portees_refusees_avec_explication():
    with pytest.raises(ErreurSaisie) as capture:
        valider_portee(TypeRegle.SALLES, NaturePortee.BESOIN)
    assert "salle ou son campus imposé" in capture.value.erreurs["portee.nature"]
    with pytest.raises(ErreurSaisie, match="n'admet pas la portée"):
        valider_portee(TypeRegle.MATIERES_PAR_JOUR, NaturePortee.ENSEIGNANT)
    valider_portee(TypeRegle.CRENEAUX_INTERDITS, NaturePortee.BESOIN)  # admise


def test_regles_de_base_et_description_du_catalogue():
    assert all(base.niveau is not Niveau.NECESSAIRE for base in REGLES_DE_BASE)
    assert [base.phrase for base in REGLES_DE_BASE] == [
        "Dans chaque matière, le CM précède les TD de la semaine.",
        "Chaque enseignant a au moins une demi-journée libre par semaine.",
    ]
    description = decrire_catalogue()
    assert len(description["types"]) == 14
    pause = next(t for t in description["types"] if t["code"] == "PAUSE_DEJEUNER")
    assert [p["nom"] for p in pause["parametres"]] == ["mode", "debut", "fin", "duree"]
    assert pause["portees_refusees"]["etablissement"]
    assert "intersection" in description["precedence_des_jours"]


def test_registre_des_implementations(monkeypatch):
    # Registres vierges pour le test : les lots solveur et vérificateur remplissent les vrais.
    import edt.domaine.regles as module_regles

    monkeypatch.setattr(module_regles, "REGISTRES", {"solveur": {}, "verificateur": {}})
    assert types_non_couverts("solveur") == frozenset(TypeRegle)
    assert types_non_couverts("verificateur") == frozenset(TypeRegle)

    @enregistrer("verificateur", TypeRegle.SANS_TROU)
    def verifier_sans_trou():
        return "essai"

    assert TypeRegle.SANS_TROU not in types_non_couverts("verificateur")
    assert module_regles.REGISTRES["verificateur"][TypeRegle.SANS_TROU]() == "essai"
    with pytest.raises(ErreurDonneesInvalides, match="déjà une implémentation"):
        enregistrer("verificateur", TypeRegle.SANS_TROU)(verifier_sans_trou)
    with pytest.raises(ErreurDonneesInvalides, match="Registre inconnu"):
        enregistrer("interface", TypeRegle.SANS_TROU)


# --------------------------------------------------------------------------- #
# Validation : un cas valide et un cas invalide par type
# --------------------------------------------------------------------------- #

CODES = {
    "campus": {"NDC", "ISSY"},
    "salle": {"N016", "N018", "L101"},
    "type_seance": {"CM", "TD", "TP"},
}

CAS_VALIDES = {
    TypeRegle.JOURS_TRAVAILLES: (
        {"jours": ["Samedi", "lundi", 1]},
        {"jours": ["lundi", "mardi", "samedi"]},
    ),
    TypeRegle.PLAGE_HORAIRE: (
        {"debut": "10h", "jours": "lundi"},
        {"debut": "10:00", "fin": "18:00", "jours": ["lundi"]},
    ),
    TypeRegle.CRENEAUX_INTERDITS: (
        {"jours": ["vendredi"], "debut": "13 h 45"},
        {"jours": ["vendredi"], "debut": "13:45", "fin": "18:00"},
    ),
    TypeRegle.CAMPUS: (
        {"mode": "Imposer", "campus": "ISSY"},
        {"mode": "imposer", "campus": ["ISSY"]},
    ),
    TypeRegle.SALLES: (
        {"mode": "interdire", "salles": ["L101", "L101"]},
        {"mode": "interdire", "salles": ["L101"]},
    ),
    TypeRegle.PAUSE_DEJEUNER: (
        {"mode": "libre", "debut": "11h45", "fin": "14:15", "duree": "1 h 15"},
        {"mode": "libre", "debut": "11:45", "fin": "14:15", "duree": 75},
    ),
    TypeRegle.PAUSE_MINIMALE: ({"duree": 30}, {"duree": 30}),
    TypeRegle.MAX_PAR_JOUR: (
        {"maximum": 360},
        {"personnes": "groupes", "unite": "minutes", "maximum": 360},
    ),
    TypeRegle.JOURNEES_LIBRES: (
        {"unite": "journee", "nombre": 2, "parmi": ["Lundi", "mardi"]},
        {"personnes": "groupes", "unite": "journee", "nombre": 2, "parmi": ["lundi", "mardi"]},
    ),
    TypeRegle.SANS_TROU: ({}, {"maximum": 0}),
    TypeRegle.CAMPUS_PAR_JOUR: ({"personnes": "tous"}, {"personnes": "tous"}),
    TypeRegle.MATIERES_PAR_JOUR: ({"maximum": "3"}, {"maximum": 3}),
    TypeRegle.ORDRE_SEMAINE: ({}, {"avant": "CM", "apres": "TD"}),
    TypeRegle.REPARTITION_MATIERE: ({"mode": "jours différents"}, {"mode": "jours_differents"}),
}

CAS_INVALIDES = {
    TypeRegle.JOURS_TRAVAILLES: ({"jours": ["lundi", "dimanche soir"]}, "parametres.jours"),
    TypeRegle.PLAGE_HORAIRE: ({"debut": "12:00", "fin": "10:00"}, "parametres.fin"),
    TypeRegle.CRENEAUX_INTERDITS: ({"debut": "8:00", "fin": "10:00"}, "parametres.jours"),
    TypeRegle.CAMPUS: ({"mode": "imposer", "campus": ["MARS"]}, "parametres.campus"),
    TypeRegle.SALLES: ({"mode": "peut-être", "salles": ["L101"]}, "parametres.mode"),
    TypeRegle.PAUSE_DEJEUNER: (
        {"mode": "libre", "debut": "13:00", "fin": "14:00", "duree": 90},
        "parametres.duree",
    ),
    TypeRegle.PAUSE_MINIMALE: ({"duree": 200}, "parametres.duree"),
    TypeRegle.MAX_PAR_JOUR: ({"unite": "seances", "maximum": 9}, "parametres.maximum"),
    TypeRegle.JOURNEES_LIBRES: ({"unite": "journee", "parmi": ["lundi matin"]}, "parametres.parmi"),
    TypeRegle.SANS_TROU: ({"maximum": 11}, "parametres.maximum"),
    TypeRegle.CAMPUS_PAR_JOUR: ({"personnes": "robots"}, "parametres.personnes"),
    TypeRegle.MATIERES_PAR_JOUR: ({"maximum": 0}, "parametres.maximum"),
    TypeRegle.ORDRE_SEMAINE: ({"avant": "TD", "apres": "TD"}, "parametres.apres"),
    TypeRegle.REPARTITION_MATIERE: ({"mode": "alternés"}, "parametres.mode"),
}


@pytest.mark.parametrize("type_regle", list(TypeRegle), ids=lambda t: t.value)
def test_validation_par_type_valide_et_canonique(type_regle):
    brouillon, canonique = CAS_VALIDES[type_regle]
    assert valider_parametres(type_regle, brouillon, PARAMETRES, CODES) == canonique


@pytest.mark.parametrize("type_regle", list(TypeRegle), ids=lambda t: t.value)
def test_validation_par_type_invalide_avec_le_champ_fautif(type_regle):
    brouillon, champ = CAS_INVALIDES[type_regle]
    with pytest.raises(ErreurSaisie) as capture:
        valider_parametres(type_regle, brouillon, PARAMETRES, CODES)
    assert champ in capture.value.erreurs, capture.value.erreurs
    assert capture.value.erreurs[champ]


def test_validation_complete_les_defauts_et_refuse_l_inconnu():
    # Les défauts globaux viennent des paramètres, ceux du type de son schéma.
    assert valider_parametres(TypeRegle.PAUSE_DEJEUNER, {}, PARAMETRES) == {
        "mode": "fixe",
        "debut": "12:45",
        "fin": "13:45",
    }
    assert valider_parametres(TypeRegle.PAUSE_MINIMALE, {}, PARAMETRES) == {"duree": 15}
    with pytest.raises(ErreurSaisie) as capture:
        valider_parametres(TypeRegle.SANS_TROU, {"maxi": 2})
    assert "parametres.maxi" in capture.value.erreurs
    # Sans paramètres globaux, un champ au défaut global est simplement absent.
    assert "debut" not in valider_parametres(TypeRegle.PAUSE_DEJEUNER, {"mode": "aucun"})
    # Un maximum en minutes pour un enseignant est refusé (maxima de sa fiche).
    with pytest.raises(ErreurSaisie) as capture:
        valider_parametres(
            TypeRegle.MAX_PAR_JOUR, {"maximum": 300}, PARAMETRES, portee=NaturePortee.ENSEIGNANT
        )
    assert "parametres.unite" in capture.value.erreurs


def test_valider_regle_entiere():
    regle = Regle(
        TypeRegle.PAUSE_DEJEUNER,
        Portee(NaturePortee.ETABLISSEMENT),
        {"mode": "libre", "duree": 500},
        Niveau.IMPORTANT,
        semestre_id=1,
        date_debut=dt.date(2026, 10, 1),
    )
    with pytest.raises(ErreurSaisie) as capture:
        valider_regle(regle, PARAMETRES)
    assert set(capture.value.erreurs) == {"portee.nature", "parametres.duree", "periode"}


# --------------------------------------------------------------------------- #
# Phrases
# --------------------------------------------------------------------------- #

PROMO = Portee(NaturePortee.GROUPE, 1, "A1", "1re année", NatureGroupe.PROMOTION)
CLASSE = Portee(NaturePortee.GROUPE, 2, "A2-G3", "Classe 3", NatureGroupe.CLASSE)
PETIT = Portee(NaturePortee.ENSEIGNANT, 9, "E009", "M. Petit")
ROUX = Portee(NaturePortee.ENSEIGNANT, 8, "E008", "Mme Roux")


@pytest.mark.parametrize(
    ("type_regle", "portee", "valeurs", "attendue"),
    [
        (
            TypeRegle.JOURS_TRAVAILLES,
            PROMO,
            {"jours": LUNDI_A_VENDREDI[:4]},
            "La promotion A1 ne travaille pas le vendredi.",
        ),
        (
            TypeRegle.JOURS_TRAVAILLES,
            CLASSE,
            {"jours": LUNDI_A_VENDREDI + ["samedi"]},
            "La classe A2-G3 a aussi cours le samedi.",
        ),
        (
            TypeRegle.JOURS_TRAVAILLES,
            PETIT,
            {"jours": ["lundi", "mardi"]},
            "M. Petit ne travaille que le lundi et le mardi.",
        ),
        (
            TypeRegle.PLAGE_HORAIRE,
            Portee(NaturePortee.ETABLISSEMENT),
            {"debut": "08:30", "fin": "17:00", "jours": ["vendredi"]},
            "Le vendredi, l'établissement ferme à 17 h 00.",
        ),
        (
            TypeRegle.PLAGE_HORAIRE,
            PROMO,
            {"debut": "10:00", "fin": "18:00", "jours": ["lundi"]},
            "Le lundi, la promotion A1 commence à 10 h 00 au plus tôt.",
        ),
        (
            TypeRegle.CRENEAUX_INTERDITS,
            Portee(NaturePortee.TYPE_SEANCE, 1, "CM", "Cours magistral"),
            {"jours": ["vendredi"], "debut": "13:45", "fin": "18:00"},
            "Les CM n'ont pas lieu le vendredi de 13 h 45 à 18 h 00.",
        ),
        (
            TypeRegle.CRENEAUX_INTERDITS,
            ROUX,
            {"jours": ["lundi"], "debut": "08:30", "fin": "10:30"},
            "Mme Roux n'a pas cours le lundi de 8 h 30 à 10 h 30.",
        ),
        (
            TypeRegle.CAMPUS,
            PROMO,
            {"mode": "imposer", "campus": ["ISSY"]},
            "Les séances de la promotion A1 ont lieu sur le campus ISSY.",
        ),
        (
            TypeRegle.SALLES,
            PETIT,
            {"mode": "interdire", "salles": ["L101"]},
            "Les séances de M. Petit n'ont jamais lieu en L101.",
        ),
        (
            TypeRegle.PAUSE_DEJEUNER,
            CLASSE,
            {"mode": "libre", "debut": "11:45", "fin": "14:15", "duree": 75},
            "La classe A2-G3 a une pause déjeuner libre d'au moins 1 h 15 entre 11 h 45 et 14 h 15.",
        ),
        (
            TypeRegle.PAUSE_DEJEUNER,
            PETIT,
            {"mode": "fixe", "debut": "12:00", "fin": "13:00"},
            "M. Petit déjeune de 12 h 00 à 13 h 00.",
        ),
        (
            TypeRegle.PAUSE_MINIMALE,
            Portee(NaturePortee.GROUPE, 4, "A1-G2a", "", NatureGroupe.DEMI_GROUPE),
            {"duree": 30},
            "Le demi-groupe A1-G2a a au moins 30 min entre deux séances.",
        ),
        (
            TypeRegle.MAX_PAR_JOUR,
            PROMO,
            {"unite": "minutes", "maximum": 360},
            "La promotion A1 n'a pas plus de 6 h de cours par jour.",
        ),
        (
            TypeRegle.MAX_PAR_JOUR,
            ROUX,
            {"unite": "seances", "maximum": 3},
            "Mme Roux ne donne pas plus de 3 séances par jour.",
        ),
        (
            TypeRegle.JOURNEES_LIBRES,
            Portee(NaturePortee.ETABLISSEMENT),
            {"personnes": "enseignants", "unite": "demi_journee", "nombre": 1, "parmi": []},
            "Chaque enseignant a au moins une demi-journée libre par semaine.",
        ),
        (
            TypeRegle.JOURNEES_LIBRES,
            ROUX,
            {"unite": "journee", "nombre": 3, "parmi": []},
            "Mme Roux vient au plus deux jours par semaine.",
        ),
        (
            TypeRegle.SANS_TROU,
            CLASSE,
            {"maximum": 0},
            "La classe A2-G3 n'a aucun trou dans la semaine.",
        ),
        (
            TypeRegle.CAMPUS_PAR_JOUR,
            PROMO,
            {},
            "La promotion A1 ne change pas de campus dans la journée.",
        ),
        (
            TypeRegle.MATIERES_PAR_JOUR,
            PROMO,
            {"maximum": 3},
            "La promotion A1 n'a pas plus de 3 matières différentes par jour.",
        ),
        (
            TypeRegle.ORDRE_SEMAINE,
            Portee(NaturePortee.ETABLISSEMENT),
            {"avant": "CM", "apres": "TD"},
            "Dans chaque matière, le CM précède les TD de la semaine.",
        ),
        (
            TypeRegle.REPARTITION_MATIERE,
            Portee(NaturePortee.MATIERE, 5, "IM.1101", "mathématiques"),
            {"mode": "jours_differents"},
            "Les séances de mathématiques ont lieu des jours différents (pour chaque groupe).",
        ),
    ],
    ids=lambda x: x if isinstance(x, str) and len(x) > 20 else None,
)
def test_phrases(type_regle, portee, valeurs, attendue):
    assert phrase(type_regle, portee, valeurs, PARAMETRES) == attendue


# --------------------------------------------------------------------------- #
# Résolution : un petit établissement construit à la main
# --------------------------------------------------------------------------- #

DEBUT, FIN = dt.date(2026, 9, 7), dt.date(2026, 12, 18)  # quinze semaines


class Etablissement:
    """Un instantané minuscule : arbre de groupes, enseignants, besoins et règles."""

    def __init__(self) -> None:
        self.groupes: list[Groupe] = []
        self.enseignants: list[Enseignant] = []
        self.besoins: list[Besoin] = []
        self.regles: list[Regle] = []
        self.campus = (Campus(1, "NDC", "Notre-Dame"), Campus(2, "ISSY", "Issy"))
        self.salles = (
            Salle(1, "N016", 1, 100, 1, frozenset(), Accessibilite.OUI),
            Salle(2, "L101", 2, 40, 1, frozenset(), Accessibilite.OUI),
        )
        self.parametres = PARAMETRES

    def groupe(self, code: str, nature: NatureGroupe, parent: int | None = None) -> int:
        identifiant = len(self.groupes) + 1
        self.groupes.append(Groupe(identifiant, code, code, nature, parent, 20, False))
        return identifiant

    def enseignant(self, nom: str, fictif: bool = False) -> int:
        identifiant = len(self.enseignants) + 1
        self.enseignants.append(
            Enseignant(
                identifiant, f"E{identifiant}", nom, StatutEnseignant.PERMANENT, None, None, fictif
            )
        )
        return identifiant

    def besoin(
        self,
        libelle: str,
        groupes: list[int],
        enseignants: list[int] = (),
        *,
        type_seance: tuple[int, str] = (2, "TD"),
        samedi: bool = False,
        matiere: tuple[int, str] = (1, "IM.1101"),
        **champs,
    ) -> int:
        identifiant = len(self.besoins) + 1
        self.besoins.append(
            Besoin(
                identifiant,
                libelle,
                matiere[0],
                matiere[1],
                type_seance[0],
                type_seance[1],
                samedi,
                120,
                frozenset(groupes),
                frozenset(enseignants),
                20 * len(groupes),
                False,
                **champs,
            )
        )
        return identifiant

    def regle(
        self,
        type_regle: TypeRegle,
        portee: Portee,
        parametres: dict,
        niveau: Niveau = Niveau.NECESSAIRE,
        etat: EtatRegle = EtatRegle.ACTIVE,
        **champs,
    ) -> Regle:
        regle = Regle(
            type_regle,
            portee,
            valider_parametres(type_regle, parametres, self.parametres),
            niveau,
            etat,
            id=len(self.regles) + 1,
            code=f"R{len(self.regles) + 1:03d}",
            **champs,
        )
        self.regles.append(regle)
        return regle

    def instantane(self) -> Instantane:
        semaines = semaines_entre(DEBUT, FIN)
        depliage = deplier(self.besoins, semaines, [], self.parametres.jours_travailles)
        creneaux = tuple(
            Creneau(
                jour,
                debut,
                debut + 120,
                DemiJournee.MATIN if debut < 12 * H else DemiJournee.APRES_MIDI,
            )
            for jour in range(6)
            for debut in (8 * H + 30, 10 * H + 45, 13 * H + 45, 16 * H)
            if jour < 5 or debut < 12 * H
        )
        parents = {g.id: g.parent_id for g in self.groupes}
        conflits = set()
        for groupe_id in parents:
            courant = parents[groupe_id]
            while courant is not None:
                conflits.add((min(groupe_id, courant), max(groupe_id, courant)))
                courant = parents[courant]
        sans_regles = Instantane(
            semestre_id=1,
            libelle="S1",
            date_debut=DEBUT,
            date_fin=FIN,
            semaines=semaines,
            creneaux=creneaux,
            campus=self.campus,
            trajets=(),
            salles=self.salles,
            enseignants=tuple(self.enseignants),
            groupes=tuple(self.groupes),
            conflits_groupes=frozenset(conflits),
            indisponibilites=(),
            jours_fermes=(),
            parametres=self.parametres,
            besoins=tuple(self.besoins),
            seances=depliage.seances,
        )
        identifiants = {
            "campus": {"NDC": 1, "ISSY": 2},
            "salle": {"N016": 1, "L101": 2},
            "type_seance": {"CM": 1, "TD": 2, "TP": 3},
        }
        resolues = tuple(
            r
            for r in (
                resoudre_regle(regle, sans_regles, identifiants)
                for regle in self.regles
                if regle.active
            )
            if r.semaines
        )
        return dataclasses.replace(sans_regles, regles=resolues)


def groupe_portee(etab: Etablissement, groupe_id: int) -> Portee:
    groupe = etab.groupes[groupe_id - 1]
    return Portee(NaturePortee.GROUPE, groupe_id, groupe.code, groupe.libelle, groupe.nature)


def enseignant_portee(etab: Etablissement, enseignant_id: int) -> Portee:
    return Portee(
        NaturePortee.ENSEIGNANT,
        enseignant_id,
        f"E{enseignant_id}",
        etab.enseignants[enseignant_id - 1].nom,
    )


@pytest.fixture
def etab() -> Etablissement:
    """A1 > A1-G1 > (A1-G1a, A1-G1b) et A1-G2 ; A3-APP1 (classe seule) ; M. Petit, Mme Roux."""
    e = Etablissement()
    a1 = e.groupe("A1", NatureGroupe.PROMOTION)
    g1 = e.groupe("A1-G1", NatureGroupe.CLASSE, a1)
    e.groupe("A1-G1a", NatureGroupe.DEMI_GROUPE, g1)
    e.groupe("A1-G1b", NatureGroupe.DEMI_GROUPE, g1)
    e.groupe("A1-G2", NatureGroupe.CLASSE, a1)
    e.groupe("A3-APP1", NatureGroupe.CLASSE)
    e.enseignant("M. Petit")
    e.enseignant("Mme Roux")
    e.besoin("CM maths A1", [1], [1], type_seance=(1, "CM"))  # séance 1-1
    e.besoin("TD maths G1", [2], [1])  # 2-1
    e.besoin("TP info G1a", [3], [2], type_seance=(3, "TP"), samedi=True)  # 3-1
    e.besoin("TD G2", [5], [2])  # 4-1
    e.besoin("TD APP1", [6], [2])  # 5-1
    e.besoin("DST A1", [1], [], type_seance=(4, "DST"), samedi=True)  # 6-1
    return e


def test_standard_sans_regle(etab):
    instantane = etab.instantane()
    reglages = instantane.reglages
    assert instantane.jours_travailles == frozenset(range(5))
    assert reglages.jours_de("2-1").jours_permis() == frozenset(range(5))
    assert reglages.jours_de("3-1").jours_permis() == frozenset(range(6))  # TP samedi autorisé
    assert reglages.jours_de("2-1").entorses(SAMEDI) == (
        pytest.approx(reglages.jours_de("2-1").entorses(SAMEDI)[0]),
    )
    (source,) = reglages.jours_de("2-1").entorses(SAMEDI)
    assert source.est_standard and source.contrainte is CodeContrainte.JOURS
    assert reglages.jours_de("3-1").entorses(SAMEDI) == ()
    assert reglages.jours_de("2-1").entorses(2) == ()


def test_un_groupe_qui_ferme_le_samedi_ferme_aussi_la_permission_du_type(etab):
    etab.regle(TypeRegle.JOURS_TRAVAILLES, groupe_portee(etab, 1), {"jours": LUNDI_A_VENDREDI})
    reglages = etab.instantane().reglages

    for seance in ("1-1", "2-1", "3-1", "4-1", "6-1"):  # toute la lignée de A1, DST compris
        assert reglages.jours_de(seance).jours_permis() == frozenset(range(5)), seance
        assert [r.code for r in reglages.jours_de(seance).retenues] == ["R001"]
    assert reglages.jours_de("5-1").retenues == ()  # A3-APP1 n'est pas concernée
    (source,) = reglages.jours_de("3-1").entorses(SAMEDI)
    assert source.regle is not None and source.regle.code == "R001"


def test_un_groupe_ouvre_le_samedi_et_un_enseignant_sans_regle_ne_bloque_pas(etab):
    etab.regle(
        TypeRegle.JOURS_TRAVAILLES, groupe_portee(etab, 6), {"jours": LUNDI_A_VENDREDI + ["samedi"]}
    )
    reglages = etab.instantane().reglages

    assert reglages.jours_de("5-1").jours_permis() == frozenset(range(6))  # Mme Roux, sans règle
    assert reglages.jours_de("4-1").jours_permis() == frozenset(range(5))  # ses autres séances
    assert reglages.jours_de("5-1").entorses(SAMEDI) == ()  # ni la règle ni le standard
    assert [s.texte for s in reglages.jours_de("5-1").entorses(6)] == [
        "règle R001",
        "établissement",
    ]

    # Si l'enseignant ferme le samedi, la séance satisfait les deux : samedi fermé.
    etab.regle(TypeRegle.JOURS_TRAVAILLES, enseignant_portee(etab, 2), {"jours": LUNDI_A_VENDREDI})
    reglages = etab.instantane().reglages
    assert reglages.jours_de("5-1").jours_permis() == frozenset(range(5))
    assert {r.code for r in reglages.jours_de("5-1").retenues} == {"R001", "R002"}
    assert [s.texte for s in reglages.jours_de("5-1").entorses(SAMEDI)] == ["règle R002"]


def test_la_regle_la_plus_precise_l_emporte_le_long_de_la_lignee(etab):
    etab.regle(TypeRegle.JOURS_TRAVAILLES, groupe_portee(etab, 1), {"jours": LUNDI_A_VENDREDI[:4]})
    etab.regle(
        TypeRegle.JOURS_TRAVAILLES, groupe_portee(etab, 3), {"jours": LUNDI_A_VENDREDI + ["samedi"]}
    )
    reglages = etab.instantane().reglages

    # Le demi-groupe G1a suit sa propre règle (samedi ouvert) ; G1b suit la promotion (vendredi fermé).
    assert reglages.jours_de("3-1").jours_permis() == frozenset(range(6))
    # Le TD de la classe G1 concerne G1a (sa règle) et G1b (règle de A1) : intersection.
    assert reglages.jours_de("2-1").jours_permis() == frozenset(range(4))
    assert {r.code for r in reglages.jours_de("2-1").retenues} == {"R001", "R002"}
    # Le CM de la promotion : G1a dit samedi, G1b, G2 disent lundi-jeudi : intersection.
    assert reglages.jours_de("1-1").jours_permis() == frozenset(range(4))


def test_une_regle_sur_le_type_l_emporte_sur_la_case_samedi(etab):
    etab.regle(
        TypeRegle.JOURS_TRAVAILLES,
        Portee(NaturePortee.TYPE_SEANCE, 4, "DST", "Devoir surveillé"),
        {"jours": LUNDI_A_VENDREDI},
    )
    reglages = etab.instantane().reglages
    assert reglages.jours_de("6-1").standard == frozenset(range(5))  # la case ne joue plus
    assert reglages.jours_de("6-1").jours_permis() == frozenset(range(5))
    assert reglages.jours_de("3-1").jours_permis() == frozenset(range(6))  # le TP garde sa case


def test_regle_de_campus_et_axe_enseignement(etab):
    etab.regle(
        TypeRegle.JOURS_TRAVAILLES,
        Portee(NaturePortee.CAMPUS, 2, "ISSY", "Issy"),
        {"jours": ["lundi", "mardi"]},
    )
    etab.regle(
        TypeRegle.JOURS_TRAVAILLES,
        Portee(NaturePortee.MATIERE, 1, "IM.1101", "Maths"),
        {"jours": LUNDI_A_VENDREDI[:3]},
    )
    etab.regle(
        TypeRegle.JOURS_TRAVAILLES,
        Portee(NaturePortee.BESOIN, 2, "2", "TD maths G1"),
        {"jours": ["jeudi"]},
    )
    reglages = etab.instantane().reglages

    assert reglages.jours_de("1-1").jours_permis() == frozenset(range(3))  # matière
    assert reglages.jours_de("1-1").jours_permis(campus_id=2) == frozenset({0, 1})  # et campus
    assert reglages.jours_de("1-1").jours_permis(campus_id=1) == frozenset(range(3))
    assert reglages.jours_de("2-1").jours_permis() == frozenset({3})  # le besoin prime la matière
    assert reglages.jours_de("1-1").campus_regles == frozenset({2})


def test_dejeuner_et_pause_par_personne_avec_leur_source(etab):
    etab.regle(
        TypeRegle.PAUSE_DEJEUNER,
        groupe_portee(etab, 2),
        {"mode": "libre", "debut": "11:45", "fin": "14:15", "duree": 75},
        Niveau.IMPORTANT,
    )
    etab.regle(
        TypeRegle.PAUSE_DEJEUNER, groupe_portee(etab, 3), {"mode": "aucun"}, Niveau.IMPORTANT
    )
    etab.regle(
        TypeRegle.PAUSE_MINIMALE, enseignant_portee(etab, 1), {"duree": 30}, Niveau.IMPORTANT
    )
    instantane = etab.instantane()
    reglages = instantane.reglages
    g1a, g1b, g2 = (Personne(NaturePersonne.GROUPE, g) for g in (3, 4, 5))
    petit, roux = (Personne(NaturePersonne.ENSEIGNANT, e) for e in (1, 2))

    assert (
        reglages.dejeuner[g1a].mode == "aucun"
        and reglages.dejeuner[g1a].source.texte == "règle R002"
    )
    assert reglages.dejeuner[g1b].fenetre == (11 * H + 45, 14 * H + 15, 75)  # hérité de la classe
    assert reglages.dejeuner[g2].plage == (12 * H + 45, 13 * H + 45)  # standard
    assert reglages.dejeuner[g2].source.est_standard
    assert reglages.dejeuner[g2].source.contrainte is CodeContrainte.DEJEUNER
    assert reglages.pause[petit].duree == 30 and reglages.pause[petit].source.regle.code == "R003"
    assert reglages.pause[roux].duree == 15 and reglages.pause[roux].source.est_standard


def test_deux_reglages_qui_se_recoupent_font_echouer_la_resolution(etab):
    etab.regle(TypeRegle.PAUSE_MINIMALE, groupe_portee(etab, 2), {"duree": 30})
    etab.regle(TypeRegle.PAUSE_MINIMALE, groupe_portee(etab, 2), {"duree": 45})
    with pytest.raises(ErreurDonneesInvalides, match="R001, R002"):
        _ = etab.instantane().reglages


def test_cibles_resolues_sous_groupes_et_groupes_lies(etab):
    sans_trou = etab.regle(TypeRegle.SANS_TROU, groupe_portee(etab, 2), {}, Niveau.FACULTATIF)
    creneaux = etab.regle(
        TypeRegle.CRENEAUX_INTERDITS,
        groupe_portee(etab, 2),
        {"jours": ["lundi"], "debut": "08:30", "fin": "10:30"},
    )
    salles = etab.regle(
        TypeRegle.SALLES, enseignant_portee(etab, 2), {"mode": "interdire", "salles": ["L101"]}
    )
    ordre = etab.regle(
        TypeRegle.ORDRE_SEMAINE, Portee(NaturePortee.ETABLISSEMENT), {}, Niveau.FACULTATIF
    )
    etab.regle(
        TypeRegle.CAMPUS,
        Portee(NaturePortee.CAMPUS, 2, "ISSY", "Issy"),
        {"mode": "imposer", "campus": ["ISSY"]},
        etat=EtatRegle.PROPOSEE,
    )
    instantane = etab.instantane()
    par_code = {r.code: r for r in instantane.regles}

    # Emploi du temps : les feuilles du sous-arbre de la classe (G1a, G1b).
    assert par_code[sans_trou.code].personnes == {
        Personne(NaturePersonne.GROUPE, 3),
        Personne(NaturePersonne.GROUPE, 4),
    }
    assert par_code[sans_trou.code].seances == frozenset()
    # Séance : les séances d'un groupe lié (ascendants et descendants de la classe).
    assert par_code[creneaux.code].seances == {"1-1", "2-1", "3-1", "6-1"}
    assert par_code[salles.code].seances == {"3-1", "4-1", "5-1"}
    assert par_code[salles.code].valeurs == {"mode": "interdire", "salles": (2,)}
    # Relation : toutes les feuilles et leurs séances ; type de séance résolu en identifiant.
    assert par_code[ordre.code].valeurs == {"avant": 1, "apres": 2}
    assert par_code[ordre.code].personnes == {
        Personne(NaturePersonne.GROUPE, g) for g in (3, 4, 5, 6)
    }
    assert par_code[ordre.code].seances == {"1-1", "2-1", "3-1", "4-1", "5-1", "6-1"}
    # Une règle proposée n'est pas dans l'instantané.
    assert set(par_code) == {sans_trou.code, creneaux.code, salles.code, ordre.code}


def test_periode_d_une_regle(etab):
    annee = etab.regle(TypeRegle.PAUSE_MINIMALE, groupe_portee(etab, 6), {"duree": 30})
    datee = etab.regle(
        TypeRegle.PAUSE_MINIMALE,
        groupe_portee(etab, 5),
        {"duree": 45},
        date_debut=dt.date(2026, 10, 1),
        date_fin=dt.date(2026, 10, 20),
    )
    hors_periode = etab.regle(
        TypeRegle.PAUSE_MINIMALE,
        groupe_portee(etab, 2),
        {"duree": 60},
        date_debut=dt.date(2027, 2, 1),
        date_fin=dt.date(2027, 3, 1),
    )
    instantane = etab.instantane()
    par_code = {r.code: r for r in instantane.regles}

    assert par_code[annee.code].semaines == frozenset(range(1, 16))
    assert par_code[datee.code].semaines == frozenset({4, 5, 6, 7})  # du 1er au 20 octobre
    assert hors_periode.code not in par_code  # aucune semaine du semestre
    assert datee.couvre(dt.date(2026, 10, 20)) and not datee.couvre(dt.date(2026, 10, 21))
    assert datee.periode_texte() == "du 01/10/2026 au 20/10/2026"
    assert annee.periode_texte() == "toute l'année"


def test_emplois_definition_unique(etab):
    instantane = etab.instantane()
    emplois = {e.personne: e for e in instantane.emplois}
    g1a = Personne(NaturePersonne.GROUPE, 3)
    assert instantane.groupes_feuilles == (3, 4, 5, 6)
    assert emplois[g1a].lignee == (3, 2, 1)
    assert emplois[g1a].seances == {
        "1-1",
        "2-1",
        "3-1",
        "6-1",
    }  # les siennes et celles de sa lignée
    assert emplois[Personne(NaturePersonne.GROUPE, 5)].seances == {"1-1", "4-1", "6-1"}
    assert emplois[Personne(NaturePersonne.ENSEIGNANT, 2)].seances == {"3-1", "4-1", "5-1"}
    assert Personne(NaturePersonne.GROUPE, 1) not in emplois  # la promotion n'est pas une feuille
    assert instantane.personnes_de("2-1") == (
        g1a,
        Personne(NaturePersonne.GROUPE, 4),
        Personne(NaturePersonne.ENSEIGNANT, 1),
    )
    assert instantane.feuilles(2) == (3, 4) and instantane.feuilles(6) == (6,)
    assert instantane.ascendants(3) == (2, 1) and instantane.descendants(1) == frozenset(
        {2, 3, 4, 5}
    )


def test_parametre_jours_travailles_change_le_standard(etab):
    etab.parametres = Parametres().avec(jours_travailles=(0, 1, 2, 3, 4, 5))
    instantane = etab.instantane()
    assert instantane.reglages.jours_de("2-1").standard == frozenset(range(6))
    assert instantane.reglages.jours_de("2-1").entorses(SAMEDI) == ()
    assert phrase(
        TypeRegle.JOURS_TRAVAILLES,
        groupe_portee(etab, 1),
        {"jours": LUNDI_A_VENDREDI},
        etab.parametres,
    ) == ("La promotion A1 ne travaille pas le samedi.")
    assert [e.value for e in EtatRegle] == ["proposee", "active", "inactive"]
    assert [o.value for o in OrigineRegle] == ["saisie", "import", "assistant", "base", "reprise"]
