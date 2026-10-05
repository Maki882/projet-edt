"""Catalogue de l'établissement fictif : sites, salles, programme des matières, parcours, langues.

Contenu **fixe** (aucun tirage ici) : le générateur y puise selon la
configuration et la graine. Tout est fictif mais plausible pour une école
d'ingénieurs du numérique de type ISEP :

- trois sites : Notre-Dame-des-Champs (salles `N...`), Issy-les-Moulineaux
  (salles `L...`) et un établissement voisin de NDC où l'école loue quelques
  salles (`Stan301`...) ;
- un programme par année et par semestre : chaque module (matière, code du
  type « II.1102 ») décrit ses enseignements (CM, TD, TP, projet, langue),
  leur cible (promotion, classe, demi-groupe, groupe transverse), leur volume
  et leurs exigences de salle.

Codes des matières : `<département>.<année><semestre><numéro sur 2 chiffres>`,
par exemple « IM.1101 » = mathématiques, 1re année, semestre 1, module 1.
"""

from __future__ import annotations

from dataclasses import dataclass

H = 60

# --------------------------------------------------------------------------- #
# Sites et salles
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Site:
    """Un site d'enseignement : code, nom, préfixe des salles, bâtiments."""

    code: str
    nom: str
    prefixe: str
    batiments: tuple[str, ...]


SITES: dict[str, Site] = {
    "NDC": Site("NDC", "Notre-Dame-des-Champs (Paris)", "N", ("Bâtiment principal", "Bâtiment cour")),
    "ISSY": Site("ISSY", "Issy-les-Moulineaux", "L", ("Bâtiment L",)),
    "STAN": Site(
        "STAN",
        "Établissement voisin de Notre-Dame-des-Champs (salles louées)",
        "Stan",
        ("Bâtiment voisin",),
    ),
}

TRAJETS: dict[tuple[str, str], int] = {
    ("NDC", "ISSY"): 60,
    ("NDC", "STAN"): 10,
    ("ISSY", "STAN"): 65,
}
"""Temps de trajet porte à porte entre sites, en minutes (symétrique)."""

CAMPUS_PROMOTION: dict[int, str] = {1: "NDC", 2: "NDC", 3: "ISSY", 4: "ISSY", 5: "ISSY"}
"""Site principal de chaque année : cycle intégré à NDC, cycle ingénieur à Issy."""

ORDRE_SITES: dict[str, tuple[str, ...]] = {
    "NDC": ("NDC", "STAN", "ISSY"),
    "ISSY": ("ISSY", "NDC", "STAN"),
}
"""Ordre de préférence des sites pour le témoin, selon le site principal de la promotion."""

TYPES_SALLE_AJOUTES: tuple[tuple[str, str], ...] = (
    ("LANGUES", "Salle de langues"),
    ("PROJET", "Salle de projet"),
)
"""Types de salle ajoutés à la liste par défaut (`edt.db.initialisation.TYPES_SALLE`)."""


@dataclass(frozen=True)
class GenreSalle:
    """Genre de salle : type, équipements, capacités possibles, étages possibles.

    `option` est un équipement présent dans une salle sur trois du genre.
    """

    type_salle: str
    equipements: tuple[str, ...]
    capacites: tuple[int, ...]
    etages: tuple[int, ...]
    option: str | None = None


GENRES_SALLE: dict[str, GenreSalle] = {
    "amphis": GenreSalle("AMPHI", ("VIDEOPROJ",), (), (0,), option="VISIO"),
    "cours": GenreSalle("COURS", ("VIDEOPROJ",), (24, 36, 40, 40, 44, 48), (1, 2, 3, 4)),
    "informatique": GenreSalle("INFO", ("ORDINATEURS", "VIDEOPROJ"), (20, 24, 24, 36), (2, 3)),
    "electronique": GenreSalle(
        "TP", ("ELECTRONIQUE", "ORDINATEURS", "VIDEOPROJ"), (20, 24), (0, 1)
    ),
    "physique": GenreSalle("LABO", ("LABORATOIRE", "VIDEOPROJ"), (20, 24), (0, 1)),
    "langues": GenreSalle("LANGUES", ("VIDEOPROJ",), (36, 40), (2, 3), option="VISIO"),
    "projet": GenreSalle("PROJET", ("VIDEOPROJ", "VISIO"), (36, 40, 48), (3, 4)),
}
"""Genres de salle, dans l'ordre de création. Les clés sont les champs de `SallesSite`."""

ETAGES_STAN = (3, 2)
"""Étages des salles louées (codes « Stan301 », « Stan302 »... puis « Stan201 »...)."""

PROFILS_SALLE: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "amphi": (("AMPHI",), ("VIDEOPROJ",)),
    "defaut": ((), ()),
    "cours": (("COURS",), ("VIDEOPROJ",)),
    "informatique": (("INFO",), ("ORDINATEURS",)),
    "electronique": (("TP",), ("ELECTRONIQUE",)),
    "physique": (("LABO",), ("LABORATOIRE",)),
    "langue": (("LANGUES", "COURS"), ("VIDEOPROJ",)),
    "projet": (("PROJET", "COURS"), ("VIDEOPROJ",)),
    "epreuve": (("AMPHI", "COURS"), ()),
    "visio": (("AMPHI",), ("VISIO",)),
}
"""Exigences de salle d'un besoin : (types acceptés, équipements requis).

`defaut` ne précise rien : le type de salle par défaut du type de séance s'applique.
"""

# --------------------------------------------------------------------------- #
# Disciplines, langues, parcours
# --------------------------------------------------------------------------- #

DISCIPLINES: dict[str, str] = {
    "IM": "mathematiques",
    "II": "informatique",
    "IE": "electronique",
    "IP": "physique",
    "IS": "signal",
    "IR": "reseaux",
    "IG": "gestion",
    "IH": "humanites",
    "IL": "anglais",
}
"""Discipline (vivier d'enseignants) de chaque département."""


@dataclass(frozen=True)
class Langue:
    """Langue vivante 2 : code de groupe, libellé, part des étudiants qui la choisissent."""

    code: str
    libelle: str
    discipline: str
    part: float


LANGUES_LV2: tuple[Langue, ...] = (
    Langue("ESP", "Espagnol", "espagnol", 0.5),
    Langue("ALL", "Allemand", "allemand", 0.3),
    Langue("CHI", "Chinois", "chinois", 0.2),
)
ANNEES_LV2 = (1, 2, 3)
"""Années qui suivent une LV2 (les années 4 et 5 suivent un parcours à la place)."""


@dataclass(frozen=True)
class Parcours:
    """Parcours d'approfondissement de fin de cursus."""

    code: str
    libelle: str
    departement: str


PARCOURS: tuple[Parcours, ...] = (
    Parcours("CYB", "Cybersécurité", "IR"),
    Parcours("IA", "Intelligence artificielle", "II"),
    Parcours("EMB", "Systèmes embarqués", "IE"),
    Parcours("RT", "Réseaux et télécommunications", "IR"),
    Parcours("LOG", "Ingénierie logicielle", "II"),
    Parcours("SAN", "Numérique et santé", "IS"),
)
ANNEES_PARCOURS = (4, 5)
TAILLE_GROUPE_TRANSVERSE = 34
"""Effectif visé d'un groupe transverse (niveau d'anglais, groupe de LV2, parcours)."""

VAGUES_ANGLAIS: dict[int, int] = {2: 2}
"""Années dont l'anglais est réparti en plusieurs vagues (sinon une seule barrette)."""

ANNEES_SPORT = (1, 2)
"""Années qui ont le mercredi après-midi libéré pour le sport."""

ANNEES_DST: dict[int, tuple[int, int]] = {1: (3, 13 * H + 45), 2: (0, 8 * H)}
"""DST hebdomadaires à créneau imposé : (jour, début). 2e année : hors de la grille."""

DUREE_DST = 3 * H
DUREE_DST_TIERS_TEMPS = 4 * H

EPREUVES_PARTIEL: tuple[tuple[int, int], ...] = ((8 * H + 30, 2 * H), (14 * H, 3 * H))
"""Demi-journées d'épreuve des partiels : (début imposé, durée). Le tiers-temps a un tiers de plus."""

# --------------------------------------------------------------------------- #
# Programme
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Enseignement:
    """Un enseignement d'un module, décliné en un besoin par groupe cible.

    - `cible` : « promotion », « classe », « demi_groupe », « anglais »,
      « lv2 », « parcours » ou « option » ;
    - `total` : nombre de séances en mode `total` (0 = mode `par_semaine`) ;
    - `fenetre` : semaines (relatives au semestre, bornes incluses) de la
      fenêtre du besoin ; `None` = tout le semestre ;
    - `alternance` : pour une cible « demi_groupe », le premier demi-groupe
      d'une classe a la première moitié de la fenêtre, le second l'autre
      (semaines disjointes : ils peuvent partager salle, créneau et enseignant) ;
    - `campus` : site imposé (si le site existe dans le jeu).
    """

    type_seance: str
    cible: str
    profil: str
    nombre: int = 1
    duree: int = 2 * H
    total: int = 0
    max_par_semaine: int = 1
    fenetre: tuple[int, int] | None = None
    enseignants: int = 1
    alternance: bool = False
    campus: str | None = None


@dataclass(frozen=True)
class Module:
    """Un module (une matière) : département, libellé, enseignements.

    `filiere` : « classique » (classes ordinaires), « apprentis » (classes
    d'apprentis) ou « commun » (toute la promotion). `partiel` : le module
    a une épreuve écrite aux partiels (années 3 à 5).
    """

    departement: str
    libelle: str
    enseignements: tuple[Enseignement, ...]
    filiere: str = "classique"
    partiel: bool = False


def cm(cible: str = "promotion", profil: str = "amphi", **options) -> Enseignement:
    """Cours magistral (2 h par semaine par défaut)."""
    return Enseignement("CM", cible, profil, **options)


def td(cible: str = "classe", profil: str = "cours", **options) -> Enseignement:
    """Travaux dirigés par classe (2 h par semaine par défaut)."""
    return Enseignement("TD", cible, profil, **options)


def tp(profil: str, **options) -> Enseignement:
    """Travaux pratiques par demi-groupe."""
    return Enseignement("TP", "demi_groupe", profil, **options)


def projet(duree: int = 2 * H, **options) -> Enseignement:
    """Séance de projet par classe."""
    return Enseignement("PROJET", "classe", "projet", duree=duree, **options)


def langue(cible: str = "anglais", **options) -> Enseignement:
    """Cours de langue (par niveau d'anglais, par groupe de LV2 ou par classe)."""
    return Enseignement("LANGUE", cible, "langue", **options)


PREMIERE_MOITIE = (1, 9)
SECONDE_MOITIE = (10, 19)
FENETRE_TP = (2, 19)

MODULES: dict[tuple[int, int], tuple[Module, ...]] = {
    (1, 1): (
        Module("IM", "Analyse 1", (cm(), td())),
        Module("II", "Algorithmique et programmation 1", (cm(), tp("informatique"))),
        Module("IL", "Anglais", (langue(),)),
        Module("IG", "Projet d'initiation à l'ingénierie", (projet(),)),
        Module(
            "IE",
            "Électronique numérique 1",
            (tp("electronique", duree=4 * H, total=6, fenetre=FENETRE_TP, alternance=True),),
        ),
        Module("IM", "Algèbre linéaire 1", (cm(), td(profil="defaut"))),
        Module(
            "IP",
            "Mécanique du point",
            (td(), tp("physique", total=5, fenetre=FENETRE_TP, alternance=True)),
        ),
        Module("IL", "Langue vivante 2", (langue("lv2"),)),
        Module("IH", "Expression écrite et orale", (td(total=6, fenetre=PREMIERE_MOITIE),)),
        Module(
            "IH", "Culture scientifique et numérique", (td(total=6, fenetre=SECONDE_MOITIE),)
        ),
    ),
    (1, 2): (
        Module("IM", "Analyse 2", (cm(), td())),
        Module("II", "Programmation orientée objet", (cm(), tp("informatique"))),
        Module("IL", "Anglais", (langue(),)),
        Module("IG", "Projet : objet connecté", (projet(enseignants=2),)),
        Module(
            "IE",
            "Électronique analogique",
            (tp("electronique", duree=4 * H, total=6, fenetre=FENETRE_TP, alternance=True),),
        ),
        Module("IM", "Probabilités", (cm(profil="defaut"), td())),
        Module(
            "IP",
            "Électromagnétisme",
            (td(), tp("physique", total=5, fenetre=FENETRE_TP, alternance=True)),
        ),
        Module("IL", "Langue vivante 2", (langue("lv2"),)),
        Module("IH", "Économie de l'entreprise", (td(total=6, fenetre=PREMIERE_MOITIE),)),
        Module("IH", "Méthodologie documentaire", (td(total=5, fenetre=SECONDE_MOITIE),)),
    ),
    (2, 1): (
        Module("IM", "Analyse 3", (cm(), td())),
        Module("II", "Structures de données", (cm(), tp("informatique"))),
        Module("IL", "Anglais", (langue(),)),
        Module("IG", "Projet de robotique", (projet(enseignants=2),)),
        Module(
            "IE",
            "Systèmes logiques",
            (tp("electronique", duree=4 * H, total=6, fenetre=FENETRE_TP, alternance=True),),
        ),
        Module("IM", "Statistiques", (cm(), td())),
        Module(
            "IP",
            "Ondes et optique",
            (td(), tp("physique", total=5, fenetre=FENETRE_TP, alternance=True)),
        ),
        Module("IL", "Langue vivante 2", (langue("lv2"),)),
        Module(
            "IH", "Histoire des sciences et des techniques", (td(total=6, fenetre=PREMIERE_MOITIE),)
        ),
        Module(
            "II",
            "Méthodes numériques",
            (tp("informatique", total=5, fenetre=SECONDE_MOITIE),),
        ),
    ),
    (2, 2): (
        Module("IM", "Mathématiques pour l'ingénieur", (cm(), td())),
        Module("II", "Bases de données", (cm(), tp("informatique"))),
        Module("IL", "Anglais", (langue(),)),
        Module("IG", "Projet de robotique 2", (projet(duree=4 * H),)),
        Module(
            "IE",
            "Électronique des capteurs",
            (tp("electronique", duree=4 * H, total=6, fenetre=FENETRE_TP, alternance=True),),
        ),
        Module("II", "Architecture des ordinateurs", (cm(), td())),
        Module(
            "IP",
            "Thermodynamique",
            (td(), tp("physique", total=5, fenetre=FENETRE_TP, alternance=True)),
        ),
        Module("IL", "Langue vivante 2", (langue("lv2"),)),
        Module("IH", "Droit du numérique", (td(total=6, fenetre=PREMIERE_MOITIE),)),
        Module("IH", "Communication professionnelle", (td(total=5, fenetre=SECONDE_MOITIE),)),
    ),
    (3, 1): (
        Module("IS", "Traitement du signal", (cm(), td()), partiel=True),
        Module("IR", "Réseaux informatiques", (cm(), tp("informatique")), partiel=True),
        Module("IL", "Anglais", (langue(),)),
        Module("IG", "Projet d'ingénierie", (projet(duree=4 * H),)),
        Module(
            "IE",
            "Électronique embarquée",
            (
                tp(
                    "electronique",
                    duree=4 * H,
                    total=6,
                    fenetre=FENETRE_TP,
                    alternance=True,
                    campus="ISSY",
                ),
            ),
        ),
        Module("IM", "Recherche opérationnelle", (cm(), td()), partiel=True),
        Module("IL", "Langue vivante 2", (langue("lv2"),)),
        Module("II", "Génie logiciel", (cm(), tp("informatique", total=6, fenetre=(2, 12)))),
        Module(
            "IG",
            "Management des organisations",
            (cm(total=6, fenetre=PREMIERE_MOITIE),),
            filiere="commun",
        ),
        Module("IR", "Fondamentaux des réseaux", (cm(), td()), filiere="apprentis", partiel=True),
        Module(
            "II",
            "Programmation avancée",
            (tp("informatique", duree=4 * H),),
            filiere="apprentis",
            partiel=True,
        ),
        Module("IL", "Anglais professionnel", (langue("classe"),), filiere="apprentis"),
        Module("IM", "Mathématiques appliquées", (td(),), filiere="apprentis"),
        Module("IG", "Projet en alternance", (projet(duree=4 * H),), filiere="apprentis"),
    ),
    (3, 2): (
        Module("IS", "Communications numériques", (cm(), td()), partiel=True),
        Module("II", "Développement web", (cm(), tp("informatique")), partiel=True),
        Module("IL", "Anglais", (langue(),)),
        Module("IG", "Projet d'ingénierie 2", (projet(duree=4 * H),)),
        Module(
            "IE",
            "Microcontrôleurs",
            (
                tp(
                    "electronique",
                    duree=4 * H,
                    total=6,
                    fenetre=FENETRE_TP,
                    alternance=True,
                    campus="ISSY",
                ),
            ),
        ),
        Module("IM", "Probabilités et processus aléatoires", (cm(), td()), partiel=True),
        Module("IL", "Langue vivante 2", (langue("lv2"),)),
        Module(
            "II",
            "Bases de données avancées",
            (tp("informatique", total=6, fenetre=SECONDE_MOITIE),),
        ),
        Module(
            "IG", "Gestion de projet", (cm(total=6, fenetre=PREMIERE_MOITIE),), filiere="commun"
        ),
        Module("II", "Systèmes d'exploitation", (cm(), td()), filiere="apprentis", partiel=True),
        Module(
            "II",
            "Développement mobile",
            (tp("informatique", duree=4 * H),),
            filiere="apprentis",
            partiel=True,
        ),
        Module("IL", "Anglais professionnel", (langue("classe"),), filiere="apprentis"),
        Module("IM", "Statistiques appliquées", (td(),), filiere="apprentis"),
        Module("IG", "Projet en alternance 2", (projet(duree=4 * H),), filiere="apprentis"),
    ),
    (4, 1): (
        Module("IR", "Sécurité des systèmes", (cm(), td()), partiel=True),
        Module("II", "Apprentissage automatique", (cm(), tp("informatique")), partiel=True),
        Module("IL", "Anglais", (langue(),)),
        Module("IG", "Projet de 4e année", (projet(duree=4 * H, enseignants=2),)),
        Module("IR", "Réseaux mobiles", (cm(), td()), partiel=True),
        Module(
            "IS",
            "Parcours d'approfondissement",
            (td("parcours"), td("parcours")),
        ),
        Module(
            "II", "Cloud et virtualisation", (tp("informatique", total=6, fenetre=SECONDE_MOITIE),)
        ),
        Module(
            "IH",
            "Éthique et responsabilité de l'ingénieur",
            (cm(profil="visio", total=6, fenetre=PREMIERE_MOITIE),),
            filiere="commun",
        ),
        Module(
            "IR",
            "Architecture des réseaux d'entreprise",
            (cm(), td()),
            filiere="apprentis",
            partiel=True,
        ),
        Module(
            "IR",
            "Sécurité opérationnelle",
            (tp("informatique", duree=4 * H),),
            filiere="apprentis",
            partiel=True,
        ),
        Module("IL", "Anglais professionnel", (langue("classe"),), filiere="apprentis"),
        Module("IG", "Management d'équipe", (td(),), filiere="apprentis"),
        Module("IG", "Projet en alternance", (projet(duree=4 * H),), filiere="apprentis"),
    ),
    (4, 2): (
        Module("II", "Systèmes distribués", (cm(), td()), partiel=True),
        Module("II", "Vision par ordinateur", (cm(), tp("informatique")), partiel=True),
        Module("IL", "Anglais", (langue(),)),
        Module("IG", "Projet de 4e année 2", (projet(duree=4 * H),)),
        Module(
            "IE",
            "Internet des objets",
            (
                tp(
                    "electronique",
                    duree=4 * H,
                    total=5,
                    fenetre=(2, 14),
                    alternance=True,
                    campus="ISSY",
                ),
            ),
        ),
        Module("IS", "Parcours d'approfondissement", (td("parcours"), td("parcours"))),
        Module(
            "IH", "Droit des contrats", (cm(total=5, fenetre=PREMIERE_MOITIE),), filiere="commun"
        ),
        Module(
            "II",
            "Urbanisation des systèmes d'information",
            (cm(), td()),
            filiere="apprentis",
            partiel=True,
        ),
        Module(
            "II",
            "Intégration et déploiement continus",
            (tp("informatique", duree=4 * H),),
            filiere="apprentis",
            partiel=True,
        ),
        Module("IL", "Anglais professionnel", (langue("classe"),), filiere="apprentis"),
        Module("IG", "Gestion financière", (td(),), filiere="apprentis"),
        Module("IG", "Projet en alternance 2", (projet(duree=4 * H),), filiere="apprentis"),
    ),
    (5, 1): (
        Module("IG", "Gestion de projet avancée", (cm(), td()), partiel=True),
        Module("IL", "Anglais : préparation aux certifications", (langue(),)),
        Module("IG", "Projet de fin d'études", (projet(duree=4 * H, enseignants=2),)),
        Module(
            "IS",
            "Parcours d'approfondissement",
            (td("parcours"), td("parcours"), td("parcours", profil="projet")),
        ),
        Module("IH", "Conduite du changement", (cm(), td()), partiel=True),
        Module(
            "IH",
            "Éthique du numérique",
            (cm(profil="visio", total=6, fenetre=PREMIERE_MOITIE),),
            filiere="commun",
        ),
        Module("IG", "Entrepreneuriat et innovation", (td("option"),)),
        Module("IG", "Pilotage de projets", (cm(), td()), filiere="apprentis", partiel=True),
        Module("IL", "Anglais professionnel", (langue("classe"),), filiere="apprentis"),
        Module("IG", "Innovation et entreprise", (td(),), filiere="apprentis", partiel=True),
        Module(
            "IG",
            "Projet de fin d'études en alternance",
            (projet(duree=4 * H),),
            filiere="apprentis",
        ),
    ),
    (5, 2): (
        Module("IH", "Droit du travail", (cm(), td()), partiel=True),
        Module("IL", "Anglais : préparation aux certifications", (langue(),)),
        Module("IG", "Projet de fin d'études 2", (projet(duree=4 * H),)),
        Module("IS", "Parcours d'approfondissement", (td("parcours"), td("parcours"))),
        Module("IG", "Management de l'innovation", (cm(), td()), partiel=True),
        Module(
            "IH",
            "Responsabilité sociétale des entreprises",
            (cm(total=5, fenetre=PREMIERE_MOITIE),),
            filiere="commun",
        ),
        Module("IG", "Entrepreneuriat et innovation 2", (td("option"),)),
        Module("IH", "Droit social", (cm(), td()), filiere="apprentis", partiel=True),
        Module("IL", "Anglais professionnel", (langue("classe"),), filiere="apprentis"),
        Module("IG", "Stratégie d'entreprise", (td(),), filiere="apprentis", partiel=True),
        Module(
            "IG",
            "Projet de fin d'études en alternance 2",
            (projet(duree=4 * H),),
            filiere="apprentis",
        ),
    ),
}
"""Programme par (année, semestre). L'ordre fixe le numéro du module dans son code."""

NUMERO_DST = 90
NUMERO_VIE_ECOLE = 95
NUMERO_PARCOURS = 60
"""Numéros réservés : DST (« IX.1190 »), vie de l'école, matières de parcours (61 à 66)."""

# --------------------------------------------------------------------------- #
# Motifs
# --------------------------------------------------------------------------- #

MOTIFS_INDISPO_ENSEIGNANT: tuple[str, ...] = (
    "Jury de concours",
    "Colloque scientifique",
    "Formation pédagogique",
    "Soutenance de thèse (jury)",
    "Mission à l'étranger",
)
MOTIF_VACATAIRE = "Activité professionnelle principale (vacataire)"
MOTIF_EXTERIEUR = "Intervenant extérieur : présent un jour par semaine"
MOTIF_SPORT = "Activités sportives et associatives"
MOTIF_PARTIELS_SALLE = "Réservée par le service des examens (partiels)"
MOTIF_TRAVAUX = "Travaux de rénovation"
MOTIF_REUNION = "Réunion pédagogique hebdomadaire"
