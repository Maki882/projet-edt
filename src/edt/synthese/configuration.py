"""Configurations du générateur de jeux fictifs : trois tailles nommées et le sur-mesure.

Une `ConfigurationJeu` décrit *ce que* contient l'établissement fictif
(promotions, classes, salles par site, densité, part de vacataires...). Le
*comment* (noms, codes, matières, placement du témoin) est dans
`edt.synthese.catalogue` et `edt.synthese.generateur`.

Les trois tailles :

- `PETIT` : une promotion de 1re année, deux classes, le semestre 1, une
  vingtaine de besoins (tests unitaires, génération quasi instantanée) ;
- `MOYEN` : une promotion complète de 3e année (classes, apprentis, groupes
  transverses, tiers-temps, partiels), les deux semestres ;
- `GRAND` : l'établissement complet (cinq années, trois sites), les deux
  semestres.

Pour un jeu sur mesure : `dataclasses.replace(GRAND, nom="...", ...)` ou une
`ConfigurationJeu(...)` complète.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from edt.erreurs import ErreurDonneesInvalides

SITES_CONNUS = ("NDC", "ISSY", "STAN")
"""Codes des sites que le catalogue sait décrire (voir `edt.synthese.catalogue.SITES`)."""

GRAINE_MAX = 2**31 - 1
"""Plus grande graine admise (la même borne que le formulaire web). Une graine
négative est refusée : `random.Random(-n)` donnerait exactement le jeu de la graine `n`."""

ANNEE_DEBUT_MIN, ANNEE_DEBUT_MAX = 2000, 2100
"""Années de début d'année scolaire admises (2026 pour 2026-2027)."""


def valider_graine(graine: object) -> int:
    """La graine si elle est un entier de 0 à `GRAINE_MAX`, sinon `ErreurDonneesInvalides`."""
    if isinstance(graine, bool) or not isinstance(graine, int) or not 0 <= graine <= GRAINE_MAX:
        raise ErreurDonneesInvalides(
            f"Graine invalide : {graine!r} (attendu : un entier de 0 à {GRAINE_MAX}). "
            "Une même graine redonne exactement le même jeu."
        )
    return graine


def valider_annee_debut(annee: object) -> int | None:
    """L'année de début si elle est vide ou dans les bornes, sinon `ErreurDonneesInvalides`."""
    if annee is None:
        return None
    if (
        isinstance(annee, bool)
        or not isinstance(annee, int)
        or not ANNEE_DEBUT_MIN <= annee <= ANNEE_DEBUT_MAX
    ):
        raise ErreurDonneesInvalides(
            f"Année de début invalide : {annee!r} (attendu : de {ANNEE_DEBUT_MIN} à "
            f"{ANNEE_DEBUT_MAX}, par exemple 2026 pour l'année 2026-2027)."
        )
    return annee


@dataclass(frozen=True)
class SallesSite:
    """Salles d'un site : capacités des amphithéâtres et nombre de salles par genre."""

    amphis: tuple[int, ...] = ()
    cours: int = 0
    informatique: int = 0
    electronique: int = 0
    physique: int = 0
    langues: int = 0
    projet: int = 0

    def __post_init__(self) -> None:
        erreurs = [
            f"capacité d'amphithéâtre invalide : {capacite!r} (attendu : un entier positif)"
            for capacite in self.amphis
            if isinstance(capacite, bool) or not isinstance(capacite, int) or capacite <= 0
        ]
        erreurs += [
            f"nombre de salles « {champ.name} » invalide : {getattr(self, champ.name)!r} "
            "(attendu : un entier positif ou nul)"
            for champ in fields(self)
            if champ.name != "amphis"
            and (
                isinstance(getattr(self, champ.name), bool)
                or not isinstance(getattr(self, champ.name), int)
                or getattr(self, champ.name) < 0
            )
        ]
        if erreurs:
            raise ErreurDonneesInvalides("Salles d'un site invalides.", erreurs)

    @property
    def total(self) -> int:
        """Nombre total de salles du site."""
        return len(self.amphis) + sum(
            getattr(self, champ.name) for champ in fields(self) if champ.name != "amphis"
        )


@dataclass(frozen=True)
class ConfigPromotion:
    """Une promotion : son année (1 à 5) et son nombre de classes.

    Les années 1 et 2 forment le cycle intégré (site de Notre-Dame-des-Champs),
    les années 3 à 5 le cycle ingénieur (Issy-les-Moulineaux), seul à
    accueillir des classes d'apprentis.
    """

    annee: int
    nb_classes: int
    nb_classes_apprentis: int = 0


@dataclass(frozen=True)
class ConfigurationJeu:
    """Paramètres d'un jeu de données fictif.

    - `promotions` : les promotions générées ;
    - `salles` : couples (code du site, salles du site) ; seuls les sites
      cités sont créés ;
    - `semestres` : codes des semestres garnis de besoins (« S1 », « S2 ») ;
    - `modules_max` : nombre maximal de modules du catalogue retenus par
      promotion et par semestre (`None` = tous) ;
    - `effectif_classe`, `effectif_classe_apprentis` : bornes du tirage des
      effectifs de classe ;
    - `part_tiers_temps` : part des étudiants d'une promotion au tiers-temps ;
    - `nb_groupes_pmr` : demi-groupes dont un étudiant requiert une salle
      accessible ;
    - `part_vacataires`, `part_exterieurs` : statuts tirés pour le vivier
      initial d'enseignants (les recrutements en cours de témoin sont permanents) ;
    - `part_maxima` : part des enseignants qui reçoivent des maxima horaires ;
    - `nb_absences_enseignants` : absences datées d'enseignants permanents
      (trois jours au plus, posées là où le témoin les laisse libres) ;
    - `nb_enseignants_fictifs` : besoins confiés à un enseignant « à recruter » ;
    - `annees_stage` : promotions dont le semestre 2 se termine par un stage ;
    - `epreuves_par_partiel` : épreuves écrites par session de partiels ;
    - `dst`, `partiels`, `evenements`, `etudiants` : interrupteurs des familles
      de données correspondantes.
    """

    nom: str
    promotions: tuple[ConfigPromotion, ...]
    salles: tuple[tuple[str, SallesSite], ...]
    semestres: tuple[str, ...] = ("S1", "S2")
    modules_max: int | None = None
    effectif_classe: tuple[int, int] = (30, 36)
    effectif_classe_apprentis: tuple[int, int] = (22, 28)
    part_tiers_temps: float = 0.03
    nb_groupes_pmr: int = 1
    part_vacataires: float = 0.25
    part_exterieurs: float = 0.1
    part_maxima: float = 0.2
    nb_absences_enseignants: int = 2
    nb_enseignants_fictifs: int = 0
    annees_stage: tuple[int, ...] = ()
    epreuves_par_partiel: int = 3
    dst: bool = True
    partiels: bool = True
    evenements: bool = True
    etudiants: bool = True

    def __post_init__(self) -> None:
        erreurs: list[str] = []
        annees = [promotion.annee for promotion in self.promotions]
        if not annees:
            erreurs.append("au moins une promotion est nécessaire")
        if len(set(annees)) != len(annees):
            erreurs.append(f"une année ne peut figurer qu'une fois (lu : {annees})")
        for promotion in self.promotions:
            if not 1 <= promotion.annee <= 5:
                erreurs.append(f"année de promotion hors de 1 à 5 : {promotion.annee}")
            if promotion.nb_classes < 1:
                erreurs.append(f"A{promotion.annee} : au moins une classe est nécessaire")
            if promotion.nb_classes_apprentis < 0 or (
                promotion.nb_classes_apprentis and promotion.annee < 3
            ):
                erreurs.append(
                    f"A{promotion.annee} : les classes d'apprentis n'existent qu'en cycle "
                    "ingénieur (années 3 à 5)"
                )
        sites = [code for code, _ in self.salles]
        inconnus = sorted(set(sites) - set(SITES_CONNUS))
        if inconnus:
            erreurs.append(f"sites inconnus : {inconnus} (connus : {list(SITES_CONNUS)})")
        if len(set(sites)) != len(sites):
            erreurs.append("un site ne peut figurer qu'une fois dans `salles`")
        if not any(salles.total for _, salles in self.salles):
            erreurs.append("au moins un site avec au moins une salle est nécessaire")
        if not self.semestres or set(self.semestres) - {"S1", "S2"}:
            erreurs.append(f"semestres attendus parmi S1 et S2 (lu : {self.semestres})")
        for nom in ("effectif_classe", "effectif_classe_apprentis"):
            bas, haut = getattr(self, nom)
            if not 1 <= bas <= haut:
                erreurs.append(f"{nom} : bornes invalides ({bas}, {haut})")
        for nom in (
            "part_tiers_temps",
            "part_vacataires",
            "part_exterieurs",
            "part_maxima",
        ):
            if not 0.0 <= getattr(self, nom) <= 1.0:
                erreurs.append(f"{nom} doit être compris entre 0 et 1")
        if self.part_vacataires + self.part_exterieurs > 1.0:
            erreurs.append("la part des vacataires et des extérieurs dépasse 1")
        if self.modules_max is not None and self.modules_max < 1:
            erreurs.append("modules_max doit valoir au moins 1 (ou None)")
        if min(self.nb_groupes_pmr, self.nb_enseignants_fictifs, self.nb_absences_enseignants) < 0:
            erreurs.append(
                "les nombres de groupes PMR, d'enseignants fictifs et d'absences sont positifs"
            )
        if self.epreuves_par_partiel < 1:
            erreurs.append("epreuves_par_partiel doit valoir au moins 1")
        if set(self.annees_stage) - set(annees):
            erreurs.append("annees_stage cite une promotion absente du jeu")
        if erreurs:
            raise ErreurDonneesInvalides(
                f"Configuration de jeu fictif « {self.nom} » invalide.", erreurs
            )

    @property
    def sites(self) -> tuple[str, ...]:
        """Codes des sites créés, dans l'ordre de la configuration."""
        return tuple(code for code, _ in self.salles)


PETIT = ConfigurationJeu(
    nom="petit",
    promotions=(ConfigPromotion(annee=1, nb_classes=2),),
    salles=(
        (
            "NDC",
            SallesSite(
                amphis=(120,),
                cours=5,
                informatique=2,
                electronique=2,
                physique=1,
                langues=1,
                projet=1,
            ),
        ),
        ("STAN", SallesSite(cours=1)),
    ),
    semestres=("S1",),
    modules_max=5,
    part_vacataires=0.3,
    part_exterieurs=0.0,
    epreuves_par_partiel=1,
    nb_absences_enseignants=1,
)
"""Une promotion de 1re année, deux classes, le semestre 1 : pour les tests unitaires."""

MOYEN = ConfigurationJeu(
    nom="moyen",
    promotions=(ConfigPromotion(annee=3, nb_classes=6, nb_classes_apprentis=2),),
    salles=(
        (
            "ISSY",
            SallesSite(
                amphis=(320, 150),
                cours=12,
                informatique=5,
                electronique=3,
                physique=1,
                langues=2,
                projet=2,
            ),
        ),
        ("NDC", SallesSite(amphis=(150,), cours=3, informatique=1)),
        ("STAN", SallesSite(cours=2)),
    ),
    nb_enseignants_fictifs=1,
    annees_stage=(3,),
)
"""Une promotion complète de 3e année (apprentis, transverses, partiels, stage), deux semestres."""

GRAND = ConfigurationJeu(
    nom="grand",
    promotions=(
        ConfigPromotion(annee=1, nb_classes=9),
        ConfigPromotion(annee=2, nb_classes=9),
        ConfigPromotion(annee=3, nb_classes=7, nb_classes_apprentis=2),
        ConfigPromotion(annee=4, nb_classes=8, nb_classes_apprentis=2),
        ConfigPromotion(annee=5, nb_classes=7, nb_classes_apprentis=2),
    ),
    salles=(
        (
            "NDC",
            SallesSite(
                amphis=(360, 220, 150),
                cours=20,
                informatique=8,
                electronique=6,
                physique=4,
                langues=4,
                projet=3,
            ),
        ),
        ("STAN", SallesSite(cours=4, langues=1)),
        (
            "ISSY",
            SallesSite(
                amphis=(320, 280, 150),
                cours=20,
                informatique=8,
                electronique=5,
                physique=2,
                langues=4,
                projet=4,
            ),
        ),
    ),
    nb_groupes_pmr=2,
    nb_enseignants_fictifs=2,
    nb_absences_enseignants=3,
    annees_stage=(4,),
)
"""L'établissement complet : cinq années, trois sites, deux semestres."""

CONFIGURATIONS: dict[str, ConfigurationJeu] = {
    configuration.nom: configuration for configuration in (PETIT, MOYEN, GRAND)
}
"""Les trois tailles nommées, par nom."""


def configuration_nommee(nom: str) -> ConfigurationJeu:
    """Configuration de la taille `nom` (« petit », « moyen », « grand »)."""
    try:
        return CONFIGURATIONS[nom]
    except KeyError:
        raise ErreurDonneesInvalides(
            f"Taille de jeu inconnue : {nom!r} (tailles disponibles : {', '.join(CONFIGURATIONS)})."
        ) from None
