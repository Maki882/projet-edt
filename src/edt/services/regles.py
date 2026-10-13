"""Règles personnalisées : lecture, validation, écriture, propositions, explication.

C'est l'entrée unique des écrans, de l'import et de l'assistant
conversationnel (format JSON stable décrit dans `docs/regles-personnalisees.md`,
§ 4.8). Toute logique de règle (catalogue, validation, phrase) vient de
`edt.domaine.regles` ; ce module résout les cibles dans la base, attribue les
codes, refuse les doublons de réglage et journalise chaque écriture avec la
phrase de la règle.

Brouillon (entrée structurée, produit par un formulaire, une feuille importée
ou le modèle de langue) :

    {"type": "PAUSE_DEJEUNER",
     "portee": {"nature": "groupe", "cible": "A2-G3"},
     "parametres": {"mode": "libre", "debut": "11:45", "fin": "14:15", "duree": 75},
     "niveau": "important", "annee": "2026-2027", "periode": {"semestre": "S1"},
     "commentaire": "Demande de la RP"}

`valider` ne lit que la base ; `creer` écrit après la confirmation explicite
de l'utilisateur (c'est l'appelant qui la recueille). Les services ne valident
pas la transaction : l'appelant appelle `session.commit()`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.domaine.calendrier import annee_scolaire_de
from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import JOURS, NatureGroupe
from edt.domaine.heures import minutes_vers_texte
from edt.domaine.parametres import Parametres
from edt.domaine.regles import (
    REGLES_DE_BASE,
    DefinitionType,
    ErreurSaisie,
    EtatRegle,
    FamilleRegle,
    NaturePortee,
    OrigineRegle,
    Portee,
    Regle,
    TypeRegle,
    decrire_catalogue,
    definition_type,
    phrase,
    resoudre_regle,
    valider_parametres,
    valider_portee,
)
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services.parametres import lire_parametres

journal = logging.getLogger(__name__)

_MOTIF_CODE = re.compile(r"^R(\d+)$")


# --------------------------------------------------------------------------- #
# Résultats typés
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegleDetail:
    """Une règle enregistrée, avec sa phrase et l'année en clair (sérialisable)."""

    regle: Regle
    phrase: str
    annee_libelle: str

    @property
    def id(self) -> int:
        """Identifiant en base."""
        return self.regle.id or 0

    @property
    def code(self) -> str:
        """Code métier (« R007 »)."""
        return self.regle.code or ""

    @property
    def type(self) -> TypeRegle:
        """Type de la règle."""
        return self.regle.type

    @property
    def etat(self) -> EtatRegle:
        """État de la règle."""
        return self.regle.etat

    @property
    def niveau(self) -> Niveau:
        """Niveau de la règle."""
        return self.regle.niveau

    @property
    def portee(self) -> Portee:
        """Portée de la règle."""
        return self.regle.portee

    def vers_dict(self) -> dict[str, Any]:
        """Représentation JSON stable (celle que l'assistant et l'export reçoivent)."""
        r = self.regle
        periode: dict[str, Any] = {}
        if r.semestre_code:
            periode["semestre"] = r.semestre_code
        if r.date_debut:
            periode["date_debut"] = r.date_debut.isoformat()
        if r.date_fin:
            periode["date_fin"] = r.date_fin.isoformat()
        return {
            "id": r.id,
            "code": r.code,
            "type": r.type.value,
            "portee": {
                "nature": r.portee.nature.value,
                "cible": r.portee.cible_code,
                "libelle": r.portee.cible_libelle,
            },
            "parametres": dict(r.parametres),
            "niveau": r.niveau.value,
            "etat": r.etat.value,
            "annee": self.annee_libelle,
            "periode": periode,
            "origine": r.origine.value,
            "commentaire": r.commentaire,
            "phrase": self.phrase,
        }


@dataclass(frozen=True)
class Validation:
    """Résultat de `valider` : la règle canonique ou les erreurs par champ."""

    valide: bool
    regle: Regle | None
    phrase: str | None
    annee_libelle: str | None
    erreurs: dict[str, str] = field(default_factory=dict)

    def vers_dict(self) -> dict[str, Any]:
        """`{"valide", "regle", "phrase", "erreurs"}` (voir la conception, § 4.8)."""
        regle = (
            RegleDetail(self.regle, self.phrase or "", self.annee_libelle or "").vers_dict()
            if self.regle is not None
            else None
        )
        return {
            "valide": self.valide,
            "regle": regle,
            "phrase": self.phrase,
            "erreurs": self.erreurs,
        }


@dataclass(frozen=True)
class Proposition:
    """Résultat de `proposer_pour_annee`."""

    creees: tuple[RegleDetail, ...]
    deja_presentes: int
    a_revoir: tuple[str, ...]

    def vers_dict(self) -> dict[str, Any]:
        """Représentation JSON."""
        return {
            "creees": [r.vers_dict() for r in self.creees],
            "deja_presentes": self.deja_presentes,
            "a_revoir": list(self.a_revoir),
        }


@dataclass(frozen=True)
class EffetSemestre:
    """Ce qu'une règle touche dans un semestre."""

    semestre_code: str
    nb_seances: int
    nb_personnes: int
    nb_semaines: int


@dataclass(frozen=True)
class Explication:
    """Texte et structure expliquant une règle (`expliquer`)."""

    detail: RegleDetail
    texte: str
    effets: tuple[EffetSemestre, ...]
    remplace: tuple[str, ...]

    def vers_dict(self) -> dict[str, Any]:
        """Représentation JSON."""
        return {
            "regle": self.detail.vers_dict(),
            "texte": self.texte,
            "effets": [dataclasses.asdict(e) for e in self.effets],
            "remplace": list(self.remplace),
        }


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #


def decrire_types() -> dict[str, Any]:
    """Catalogue des types (la définition d'outil donnée au modèle de langue)."""
    return decrire_catalogue()


def lister(
    session: Session,
    annee_scolaire_id: int,
    *,
    etat: EtatRegle | str | None = None,
    type_regle: TypeRegle | str | None = None,
    niveau: Niveau | str | None = None,
    origine: OrigineRegle | str | None = None,
    groupe_id: int | None = None,
    enseignant_id: int | None = None,
    matiere_id: int | None = None,
) -> list[RegleDetail]:
    """Règles d'une année, par code, filtrées.

    `groupe_id` retient les règles qui visent ce groupe ou l'un de ses
    ascendants (règles héritées comprises).
    """
    requete = select(m.Regle).where(m.Regle.annee_scolaire_id == annee_scolaire_id)
    if etat is not None:
        requete = requete.where(m.Regle.etat == EtatRegle(etat))
    if type_regle is not None:
        requete = requete.where(m.Regle.type == TypeRegle(type_regle))
    if niveau is not None:
        requete = requete.where(m.Regle.niveau == Niveau(niveau))
    if origine is not None:
        requete = requete.where(m.Regle.origine == OrigineRegle(origine))
    if enseignant_id is not None:
        requete = requete.where(m.Regle.enseignant_id == enseignant_id)
    if matiere_id is not None:
        requete = requete.where(m.Regle.matiere_id == matiere_id)
    if groupe_id is not None:
        requete = requete.where(m.Regle.groupe_id.in_(_lignee(session, groupe_id)))
    lignes = session.scalars(requete).all()
    parametres = lire_parametres(session)
    details = [_detail(session, ligne, parametres) for ligne in lignes]
    return sorted(details, key=lambda d: _cle_tri(d.code))


def lire(session: Session, annee_scolaire_id: int, code: str) -> RegleDetail:
    """La règle `code` de l'année ; lève `ErreurIntrouvable` sinon."""
    return _detail(session, _ligne(session, annee_scolaire_id, code), lire_parametres(session))


def lire_par_id(session: Session, regle_id: int) -> RegleDetail:
    """La règle d'identifiant `regle_id` ; lève `ErreurIntrouvable` sinon."""
    ligne = session.get(m.Regle, regle_id)
    if ligne is None:
        raise ErreurIntrouvable(f"La règle {regle_id} n'existe pas.")
    return _detail(session, ligne, lire_parametres(session))


def regles_visant(session: Session, nature: NaturePortee | str, cible_id: int) -> list[RegleDetail]:
    """Règles (tous états, toutes années) dont la portée vise exactement cette cible.

    Sert à l'analyse de suppression d'une cible : la base refuse de la
    supprimer tant qu'une règle la vise.
    """
    colonne = _COLONNES_CIBLE[NaturePortee(nature)]
    if colonne is None:
        return []
    lignes = session.scalars(select(m.Regle).where(getattr(m.Regle, colonne) == cible_id)).all()
    parametres = lire_parametres(session)
    return [_detail(session, ligne, parametres) for ligne in lignes]


def charger_regles_actives(session: Session, annee_scolaire_id: int) -> list[Regle]:
    """Règles actives d'une année, dans le vocabulaire du domaine (pour l'instantané)."""
    lignes = session.scalars(
        select(m.Regle).where(
            m.Regle.annee_scolaire_id == annee_scolaire_id, m.Regle.etat == EtatRegle.ACTIVE
        )
    ).all()
    return sorted((_depuis_orm(ligne) for ligne in lignes), key=lambda r: _cle_tri(r.code or ""))


# --------------------------------------------------------------------------- #
# Validation et écriture
# --------------------------------------------------------------------------- #


def valider(session: Session, brouillon: Mapping[str, Any]) -> Validation:
    """Valide un brouillon sans rien écrire : règle canonique, phrase, ou erreurs par champ."""
    try:
        regle, annee = _construire(session, brouillon, existante=None)
    except ErreurSaisie as erreur:
        return Validation(False, None, None, None, erreur.erreurs)
    texte = phrase(regle.type, regle.portee, regle.parametres, lire_parametres(session))
    return Validation(True, regle, texte, annee.libelle, {})


def creer(
    session: Session,
    brouillon: Mapping[str, Any],
    origine: OrigineRegle | str = OrigineRegle.SAISIE,
    etat: EtatRegle | str = EtatRegle.ACTIVE,
) -> RegleDetail:
    """Crée une règle valide et renvoie son détail (code attribué si le brouillon n'en a pas).

    Lève `ErreurSaisie` (erreurs par champ) si le brouillon est invalide.
    """
    # L'état et l'origine demandés priment le brouillon : le contrôle des doublons
    # ne vaut que pour une règle créée active.
    brouillon = {**brouillon, "etat": EtatRegle(etat).value, "origine": OrigineRegle(origine).value}
    regle, annee = _construire(session, brouillon, existante=None)
    code = regle.code or _prochain_code(session, annee.id)
    if _ligne_ou_none(session, annee.id, code) is not None:
        raise ErreurSaisie({"code": f"La règle {code} existe déjà en {annee.libelle}."})
    ligne = m.Regle(annee_scolaire_id=annee.id, code=code)
    _vers_orm(ligne, regle)
    session.add(ligne)
    session.flush()
    detail = _detail(session, ligne, lire_parametres(session))
    journal.info(
        "Règle %s créée (%s, %s, %s) : %s",
        code,
        annee.libelle,
        regle.origine.value,
        regle.etat.value,
        detail.phrase,
    )
    return detail


def modifier(
    session: Session, annee_scolaire_id: int, code: str, changements: Mapping[str, Any]
) -> RegleDetail:
    """Modification partielle d'une règle, revalidée en entier."""
    ligne = _ligne(session, annee_scolaire_id, code)
    detail = _detail(session, ligne, lire_parametres(session))
    brouillon = detail.vers_dict()
    brouillon.pop("phrase")
    brouillon.pop("id")
    brouillon["portee"] = {
        "nature": brouillon["portee"]["nature"],
        "cible": brouillon["portee"]["cible"],
    }
    for cle, valeur in changements.items():
        if cle == "parametres" and isinstance(valeur, Mapping):
            brouillon["parametres"] = {**brouillon["parametres"], **valeur}
        elif cle == "portee" and isinstance(valeur, Mapping):
            brouillon["portee"] = {**brouillon["portee"], **valeur}
        else:
            brouillon[cle] = valeur
    brouillon["annee"] = detail.annee_libelle
    regle, _ = _construire(session, brouillon, existante=ligne)
    _vers_orm(ligne, regle)
    session.flush()
    nouveau = _detail(session, ligne, lire_parametres(session))
    journal.info("Règle %s modifiée : %s", code, nouveau.phrase)
    return nouveau


def supprimer(session: Session, annee_scolaire_id: int, code: str) -> None:
    """Supprime une règle."""
    ligne = _ligne(session, annee_scolaire_id, code)
    texte = _detail(session, ligne, lire_parametres(session)).phrase
    session.delete(ligne)
    session.flush()
    journal.info("Règle %s supprimée : %s", code, texte)


def changer_etat(
    session: Session, annee_scolaire_id: int, code: str, etat: EtatRegle | str
) -> RegleDetail:
    """Change l'état d'une règle (proposée, active, inactive) ; une activation est revalidée."""
    ligne = _ligne(session, annee_scolaire_id, code)
    nouvel_etat = EtatRegle(etat)
    if nouvel_etat is EtatRegle.ACTIVE:
        regle = _depuis_orm(ligne)
        _refuser_doublon(session, dataclasses.replace(regle, etat=EtatRegle.ACTIVE), ligne)
    ligne.etat = nouvel_etat
    session.flush()
    detail = _detail(session, ligne, lire_parametres(session))
    journal.info("Règle %s : état « %s » : %s", code, nouvel_etat.value, detail.phrase)
    return detail


def activer(session: Session, annee_scolaire_id: int, code: str) -> RegleDetail:
    """Rend une règle active (une règle proposée est ainsi confirmée)."""
    return changer_etat(session, annee_scolaire_id, code, EtatRegle.ACTIVE)


def desactiver(session: Session, annee_scolaire_id: int, code: str) -> RegleDetail:
    """Rend une règle inactive (sans effet, mais conservée)."""
    return changer_etat(session, annee_scolaire_id, code, EtatRegle.INACTIVE)


def proposer_pour_annee(session: Session, annee_scolaire_id: int) -> Proposition:
    """Propose à une année les règles de base et la reprise de l'année précédente.

    Tout est créé à l'état « proposée » (sans effet) ; rien n'est reproposé
    deux fois (règle de base déjà présente, règle déjà reprise). Une règle de
    l'année précédente dont la cible n'existe pas dans l'année est listée
    « à revoir ».
    """
    annee = _annee_par_id(session, annee_scolaire_id)
    parametres = lire_parametres(session)
    creees: list[RegleDetail] = []
    deja = 0
    a_revoir: list[str] = []

    presentes = session.scalars(select(m.Regle).where(m.Regle.annee_scolaire_id == annee.id)).all()
    types_de_base = {ligne.type for ligne in presentes if ligne.origine == OrigineRegle.BASE}
    for base in REGLES_DE_BASE:
        if base.type in types_de_base:
            deja += 1
            continue
        creees.append(
            creer(
                session,
                {
                    "type": base.type.value,
                    "portee": {"nature": NaturePortee.ETABLISSEMENT.value},
                    "parametres": base.parametres,
                    "niveau": base.niveau.value,
                    "annee": annee.libelle,
                    "commentaire": base.commentaire,
                },
                origine=OrigineRegle.BASE,
                etat=EtatRegle.PROPOSEE,
            )
        )

    precedente = session.scalar(
        select(m.AnneeScolaire)
        .where(m.AnneeScolaire.date_fin < annee.date_debut)
        .order_by(m.AnneeScolaire.date_fin.desc())
    )
    if precedente is not None:
        reprises = {ligne.reprise_de_id for ligne in presentes if ligne.reprise_de_id}
        anciennes = session.scalars(
            select(m.Regle).where(
                m.Regle.annee_scolaire_id == precedente.id, m.Regle.etat == EtatRegle.ACTIVE
            )
        ).all()
        decalage = annee.date_debut.year - precedente.date_debut.year
        for ancienne in sorted(anciennes, key=lambda r: _cle_tri(r.code)):
            if ancienne.id in reprises:
                deja += 1
                continue
            detail_ancien = _detail(session, ancienne, parametres)
            brouillon = _brouillon_de_reprise(session, annee, ancienne, detail_ancien, decalage)
            if brouillon is None:
                a_revoir.append(f"{ancienne.code} ({precedente.libelle}) : {detail_ancien.phrase}")
                continue
            try:
                detail = creer(
                    session, brouillon, origine=OrigineRegle.REPRISE, etat=EtatRegle.PROPOSEE
                )
            except ErreurSaisie as erreur:
                journal.warning(
                    "Règle %s de %s non reprise : %s", ancienne.code, precedente.libelle, erreur
                )
                a_revoir.append(f"{ancienne.code} ({precedente.libelle}) : {detail_ancien.phrase}")
                continue
            ligne = _ligne(session, annee.id, detail.code)
            ligne.reprise_de_id = ancienne.id
            session.flush()
            creees.append(_detail(session, ligne, parametres))
    journal.info(
        "Année %s : %d règle(s) proposée(s), %d déjà présente(s), %d à revoir",
        annee.libelle,
        len(creees),
        deja,
        len(a_revoir),
    )
    return Proposition(tuple(creees), deja, tuple(a_revoir))


def expliquer(session: Session, annee_scolaire_id: int, code: str) -> Explication:
    """Phrase, portée, période, niveau, et effets d'une règle sur chaque semestre concerné."""
    from edt.services.instantane import charger_instantane

    detail = lire(session, annee_scolaire_id, code)
    regle = detail.regle
    annee = _annee_par_id(session, annee_scolaire_id)
    effets: list[EffetSemestre] = []
    identifiants = _identifiants(session)
    for semestre in annee.semestres:
        if regle.semestre_id is not None and regle.semestre_id != semestre.id:
            continue
        instantane = charger_instantane(session, semestre.id)
        resolue = resoudre_regle(regle, instantane, identifiants)
        if not resolue.semaines:
            continue
        effets.append(
            EffetSemestre(
                semestre.code, len(resolue.seances), len(resolue.personnes), len(resolue.semaines)
            )
        )
    remplace = _remplace(regle, lire_parametres(session))
    morceaux = [
        f"{detail.phrase} (règle {detail.code}, {regle.niveau.libelle.lower()}, "
        f"{regle.etat.libelle.lower()}, {regle.periode_texte()})."
    ]
    for effet in effets:
        morceaux.append(
            f"{effet.semestre_code} : {effet.nb_seances} séance(s) et {effet.nb_personnes} "
            f"personne(s) concernée(s) sur {effet.nb_semaines} semaine(s)."
        )
    if remplace:
        morceaux.append("Remplace : " + " ; ".join(remplace) + ".")
    if regle.etat is not EtatRegle.ACTIVE:
        morceaux.append("Cette règle n'est pas active : elle n'a aucun effet.")
    return Explication(detail, " ".join(morceaux), tuple(effets), tuple(remplace))


# --------------------------------------------------------------------------- #
# Construction d'une règle depuis un brouillon
# --------------------------------------------------------------------------- #

_COLONNES_CIBLE: dict[NaturePortee, str | None] = {
    NaturePortee.ETABLISSEMENT: None,
    NaturePortee.GROUPE: "groupe_id",
    NaturePortee.ENSEIGNANT: "enseignant_id",
    NaturePortee.CAMPUS: "campus_id",
    NaturePortee.TYPE_SEANCE: "type_seance_id",
    NaturePortee.MATIERE: "matiere_id",
    NaturePortee.BESOIN: "besoin_id",
}


def _construire(
    session: Session, brouillon: Mapping[str, Any], existante: m.Regle | None
) -> tuple[Regle, m.AnneeScolaire]:
    """Brouillon → règle canonique et son année ; `ErreurSaisie` avec une erreur par champ."""
    erreurs: dict[str, str] = {}
    brouillon = dict(brouillon)

    definition: DefinitionType | None = None
    try:
        definition = definition_type(str(brouillon.get("type") or ""))
    except ErreurDonneesInvalides as erreur:
        erreurs["type"] = erreur.message

    annee = None
    try:
        annee = _annee(session, brouillon.get("annee"))
    except (ErreurDonneesInvalides, ErreurIntrouvable) as erreur:
        erreurs["annee"] = erreur.message

    portee: Portee | None = None
    brouillon_portee = brouillon.get("portee") or {}
    if isinstance(brouillon_portee, str):
        brouillon_portee = {"nature": brouillon_portee}
    try:
        nature = NaturePortee(str(brouillon_portee.get("nature") or ""))
    except ValueError:
        erreurs["portee.nature"] = "Portée inconnue ; attendu : " + ", ".join(
            p.value for p in NaturePortee
        )
        nature = None
    if nature is not None and definition is not None:
        try:
            valider_portee(definition.code, nature)
        except ErreurSaisie as erreur:
            erreurs.update(erreur.erreurs)
    if nature is not None and annee is not None and "portee.nature" not in erreurs:
        try:
            portee = _resoudre_cible(session, annee, nature, brouillon_portee.get("cible"))
        except ErreurSaisie as erreur:
            erreurs.update(erreur.erreurs)

    semestre_id = semestre_code = date_debut = date_fin = None
    periode = brouillon.get("periode") or {}
    if annee is not None:
        try:
            semestre_id, semestre_code, date_debut, date_fin = _periode(session, annee, periode)
        except ErreurSaisie as erreur:
            erreurs.update(erreur.erreurs)

    niveau = definition.niveau_defaut if definition is not None else Niveau.NECESSAIRE
    if brouillon.get("niveau"):
        try:
            niveau = Niveau(str(brouillon["niveau"]))
        except ValueError:
            erreurs["niveau"] = "Niveau inconnu ; attendu : " + ", ".join(n.value for n in Niveau)
    etat = EtatRegle.ACTIVE
    if brouillon.get("etat"):
        try:
            etat = EtatRegle(str(brouillon["etat"]))
        except ValueError:
            erreurs["etat"] = "État inconnu ; attendu : " + ", ".join(e.value for e in EtatRegle)

    parametres: dict[str, Any] = {}
    if definition is not None:
        try:
            parametres = valider_parametres(
                definition.code,
                brouillon.get("parametres") or {},
                lire_parametres(session),
                _codes_connus(session),
                nature,
            )
        except ErreurSaisie as erreur:
            erreurs.update(erreur.erreurs)

    code = brouillon.get("code")
    if code is not None and (not isinstance(code, str) or not code.strip()):
        code = None
    if erreurs or definition is None or annee is None or portee is None:
        raise ErreurSaisie(erreurs or {"regle": "Règle invalide."})

    regle = Regle(
        type=definition.code,
        portee=portee,
        parametres=parametres,
        niveau=niveau,
        etat=etat,
        id=existante.id if existante is not None else None,
        code=(code.strip() if isinstance(code, str) else None)
        or (existante.code if existante is not None else None),
        annee_scolaire_id=annee.id,
        semestre_id=semestre_id,
        semestre_code=semestre_code,
        date_debut=date_debut,
        date_fin=date_fin,
        origine=OrigineRegle(brouillon.get("origine") or OrigineRegle.SAISIE),
        commentaire=str(brouillon.get("commentaire") or ""),
        reprise_de_id=existante.reprise_de_id if existante is not None else None,
    )
    _refuser_doublon(session, regle, existante)
    return regle, annee


def _annee(session: Session, valeur: object) -> m.AnneeScolaire:
    if valeur is None or valeur == "":
        libelle = f"{annee_scolaire_de(dt.date.today())}-{annee_scolaire_de(dt.date.today()) + 1}"
        annee = session.scalar(select(m.AnneeScolaire).where(m.AnneeScolaire.libelle == libelle))
        if annee is None:
            annee = session.scalar(
                select(m.AnneeScolaire).order_by(m.AnneeScolaire.date_debut.desc())
            )
        if annee is None:
            raise ErreurIntrouvable("Aucune année scolaire n'existe : en créer une d'abord.")
        return annee
    if isinstance(valeur, int) and not isinstance(valeur, bool):
        return _annee_par_id(session, valeur)
    annee = session.scalar(
        select(m.AnneeScolaire).where(m.AnneeScolaire.libelle == str(valeur).strip())
    )
    if annee is None:
        raise ErreurIntrouvable(f"Année scolaire inconnue : « {valeur} ».")
    return annee


def _annee_par_id(session: Session, annee_scolaire_id: int) -> m.AnneeScolaire:
    annee = session.get(m.AnneeScolaire, annee_scolaire_id)
    if annee is None:
        raise ErreurIntrouvable(f"L'année scolaire {annee_scolaire_id} n'existe pas.")
    return annee


def _resoudre_cible(
    session: Session, annee: m.AnneeScolaire, nature: NaturePortee, cible: object
) -> Portee:
    """Portée complète (identifiant, code, libellé) depuis la nature et le code de la cible."""
    if nature is NaturePortee.ETABLISSEMENT:
        return Portee(nature)
    if cible is None or cible == "":
        raise ErreurSaisie({"portee.cible": f"Un {nature.libelle.lower()} est attendu (son code)."})
    par_id = isinstance(cible, int) and not isinstance(cible, bool)
    texte = str(cible).strip()
    if nature is NaturePortee.GROUPE:
        groupe = (
            session.get(m.Groupe, cible)
            if par_id
            else session.scalar(
                select(m.Groupe).where(
                    m.Groupe.annee_scolaire_id == annee.id, m.Groupe.code == texte
                )
            )
        )
        if groupe is None or groupe.annee_scolaire_id != annee.id:
            raise ErreurSaisie({"portee.cible": f"Groupe « {texte} » inconnu en {annee.libelle}."})
        return Portee(nature, groupe.id, groupe.code, groupe.libelle, NatureGroupe(groupe.nature))
    if nature is NaturePortee.ENSEIGNANT:
        enseignant = (
            session.get(m.Enseignant, cible)
            if par_id
            else session.scalar(select(m.Enseignant).where(m.Enseignant.identifiant == texte))
        )
        if enseignant is None:
            raise ErreurSaisie({"portee.cible": f"Enseignant « {texte} » inconnu (identifiant)."})
        return Portee(nature, enseignant.id, enseignant.identifiant, enseignant.nom_affiche)
    if nature is NaturePortee.CAMPUS:
        campus = (
            session.get(m.Campus, cible)
            if par_id
            else session.scalar(select(m.Campus).where(m.Campus.code == texte))
        )
        if campus is None:
            raise ErreurSaisie({"portee.cible": f"Campus « {texte} » inconnu."})
        return Portee(nature, campus.id, campus.code, campus.nom)
    if nature is NaturePortee.TYPE_SEANCE:
        type_seance = (
            session.get(m.TypeSeance, cible)
            if par_id
            else session.scalar(select(m.TypeSeance).where(m.TypeSeance.code == texte))
        )
        if type_seance is None:
            raise ErreurSaisie({"portee.cible": f"Type de séance « {texte} » inconnu."})
        return Portee(nature, type_seance.id, type_seance.code, type_seance.libelle)
    if nature is NaturePortee.MATIERE:
        if par_id:
            matiere = session.get(m.Matiere, cible)
        else:
            candidates = session.scalars(select(m.Matiere).where(m.Matiere.code == texte)).all()
            de_l_annee = [
                c
                for c in candidates
                if c.promotion_id is None or c.promotion.annee_scolaire_id == annee.id
            ]
            matiere = de_l_annee[0] if len(de_l_annee) == 1 else None
            if len(de_l_annee) > 1:
                raise ErreurSaisie(
                    {
                        "portee.cible": f"Plusieurs matières ont le code « {texte} » : donner son identifiant."
                    }
                )
        if matiere is None:
            raise ErreurSaisie(
                {"portee.cible": f"Matière « {texte} » inconnue en {annee.libelle}."}
            )
        return Portee(nature, matiere.id, matiere.code, matiere.libelle)
    besoin = (
        session.get(m.Besoin, cible) if par_id else _besoin_par_description(session, annee, texte)
    )
    if besoin is None or besoin.semestre.annee_scolaire_id != annee.id:
        raise ErreurSaisie(
            {
                "portee.cible": f"Besoin « {texte} » inconnu en {annee.libelle} (identifiant, ou "
                "« matière / type / groupes »)."
            }
        )
    libelle = f"{besoin.type_seance.code} {besoin.matiere.libelle} - " + ", ".join(
        sorted(g.code for g in besoin.groupes)
    )
    return Portee(nature, besoin.id, str(besoin.id), libelle)


def _besoin_par_description(
    session: Session, annee: m.AnneeScolaire, texte: str
) -> m.Besoin | None:
    """Un besoin désigné par « code matière / code type / codes des groupes »."""
    morceaux = [p.strip() for p in texte.split("/")]
    if len(morceaux) != 3:
        return texte.isdigit() and session.get(m.Besoin, int(texte)) or None
    code_matiere, code_type, groupes = morceaux
    codes_groupes = {g.strip() for g in groupes.split(",") if g.strip()}
    semestres = [s.id for s in annee.semestres]
    candidats = session.scalars(
        select(m.Besoin)
        .join(m.Matiere, m.Besoin.matiere_id == m.Matiere.id)
        .join(m.TypeSeance, m.Besoin.type_seance_id == m.TypeSeance.id)
        .where(
            m.Besoin.semestre_id.in_(semestres),
            m.Matiere.code == code_matiere,
            m.TypeSeance.code == code_type,
        )
    ).all()
    retenus = [b for b in candidats if {g.code for g in b.groupes} == codes_groupes]
    return retenus[0] if len(retenus) == 1 else None


def _periode(
    session: Session, annee: m.AnneeScolaire, periode: Mapping[str, Any]
) -> tuple[int | None, str | None, dt.date | None, dt.date | None]:
    erreurs: dict[str, str] = {}
    semestre_id = semestre_code = None
    code = periode.get("semestre")
    if code:
        semestre = next((s for s in annee.semestres if s.code == str(code).strip()), None)
        if semestre is None:
            erreurs["periode.semestre"] = (
                f"Semestre « {code} » inconnu en {annee.libelle} ; semestres : "
                + ", ".join(s.code for s in annee.semestres)
                + "."
            )
        else:
            semestre_id, semestre_code = semestre.id, semestre.code
    dates: dict[str, dt.date | None] = {}
    for cle in ("date_debut", "date_fin"):
        valeur = periode.get(cle)
        if valeur is None or valeur == "":
            dates[cle] = None
            continue
        try:
            date = valeur if isinstance(valeur, dt.date) else dt.date.fromisoformat(str(valeur))
        except ValueError:
            erreurs[f"periode.{cle}"] = f"Date invalide : « {valeur} » (attendu : AAAA-MM-JJ)."
            continue
        if not annee.date_debut <= date <= annee.date_fin:
            erreurs[f"periode.{cle}"] = (
                f"La date {date:%d/%m/%Y} est hors de l'année {annee.libelle}."
            )
        dates[cle] = date
    if semestre_id is not None and (dates.get("date_debut") or dates.get("date_fin")):
        erreurs["periode"] = "Choisir un semestre ou des dates, pas les deux."
    if (
        dates.get("date_debut")
        and dates.get("date_fin")
        and dates["date_fin"] < dates["date_debut"]
    ):
        erreurs["periode.date_fin"] = "La date de fin précède la date de début."
    if erreurs:
        raise ErreurSaisie(erreurs)
    return semestre_id, semestre_code, dates.get("date_debut"), dates.get("date_fin")


def _codes_connus(session: Session) -> dict[str, set[str]]:
    return {
        "campus": set(session.scalars(select(m.Campus.code))),
        "salle": set(session.scalars(select(m.Salle.code))),
        "type_seance": set(session.scalars(select(m.TypeSeance.code))),
    }


def _identifiants(session: Session) -> dict[str, dict[str, int]]:
    """Codes → identifiants des entités que les paramètres peuvent citer."""
    return {
        "campus": dict(session.execute(select(m.Campus.code, m.Campus.id)).all()),
        "salle": dict(session.execute(select(m.Salle.code, m.Salle.id)).all()),
        "type_seance": dict(session.execute(select(m.TypeSeance.code, m.TypeSeance.id)).all()),
    }


def _refuser_doublon(session: Session, regle: Regle, existante: m.Regle | None) -> None:
    """Deux règles actives d'un même type de réglage, même cible, périodes qui se recoupent."""
    if regle.etat is not EtatRegle.ACTIVE or regle.definition.famille is not FamilleRegle.REGLAGE:
        return
    requete = select(m.Regle).where(
        m.Regle.annee_scolaire_id == regle.annee_scolaire_id,
        m.Regle.type == regle.type,
        m.Regle.portee == regle.portee.nature,
        m.Regle.etat == EtatRegle.ACTIVE,
    )
    colonne = _COLONNES_CIBLE[regle.portee.nature]
    if colonne is not None:
        requete = requete.where(getattr(m.Regle, colonne) == regle.portee.cible_id)
    for autre in session.scalars(requete):
        if existante is not None and autre.id == existante.id:
            continue
        if _periodes_se_recoupent(session, regle, autre):
            raise ErreurSaisie(
                {
                    "portee": f"La règle {autre.code} règle déjà « {regle.type.libelle.lower()} » "
                    f"pour {regle.portee.designation} sur une période qui se recoupe : la modifier."
                }
            )


def _periodes_se_recoupent(session: Session, regle: Regle, autre: m.Regle) -> bool:
    def bornes(semestre_id, date_debut, date_fin) -> tuple[dt.date | None, dt.date | None]:
        if semestre_id is not None:
            semestre = session.get(m.Semestre, semestre_id)
            return (semestre.date_debut, semestre.date_fin) if semestre else (None, None)
        return date_debut, date_fin

    debut_a, fin_a = bornes(regle.semestre_id, regle.date_debut, regle.date_fin)
    debut_b, fin_b = bornes(autre.semestre_id, autre.date_debut, autre.date_fin)
    if (debut_a is None and fin_a is None) or (debut_b is None and fin_b is None):
        return True
    tot, tard = dt.date.min, dt.date.max
    return (debut_a or tot) <= (fin_b or tard) and (debut_b or tot) <= (fin_a or tard)


def _prochain_code(session: Session, annee_scolaire_id: int) -> str:
    codes = session.scalars(
        select(m.Regle.code).where(m.Regle.annee_scolaire_id == annee_scolaire_id)
    )
    numeros = [int(lu.group(1)) for code in codes if (lu := _MOTIF_CODE.match(code))]
    return f"R{(max(numeros, default=0) + 1):03d}"


def _cle_tri(code: str) -> tuple[int, str]:
    lu = _MOTIF_CODE.match(code or "")
    return (int(lu.group(1)) if lu else 10**9, code or "")


def _lignee(session: Session, groupe_id: int) -> list[int]:
    """Le groupe et ses ascendants (pour retrouver les règles héritées)."""
    lignee: list[int] = []
    courant = session.get(m.Groupe, groupe_id)
    while courant is not None and courant.id not in lignee:
        lignee.append(courant.id)
        courant = session.get(m.Groupe, courant.parent_id) if courant.parent_id else None
    return lignee or [groupe_id]


# --------------------------------------------------------------------------- #
# ORM <-> domaine
# --------------------------------------------------------------------------- #


def _portee_depuis_orm(ligne: m.Regle) -> Portee:
    nature = NaturePortee(ligne.portee)
    if nature is NaturePortee.ETABLISSEMENT:
        return Portee(nature)
    if nature is NaturePortee.GROUPE and ligne.groupe is not None:
        g = ligne.groupe
        return Portee(nature, g.id, g.code, g.libelle, NatureGroupe(g.nature))
    if nature is NaturePortee.ENSEIGNANT and ligne.enseignant is not None:
        e = ligne.enseignant
        return Portee(nature, e.id, e.identifiant, e.nom_affiche)
    if nature is NaturePortee.CAMPUS and ligne.campus is not None:
        return Portee(nature, ligne.campus.id, ligne.campus.code, ligne.campus.nom)
    if nature is NaturePortee.TYPE_SEANCE and ligne.type_seance is not None:
        t = ligne.type_seance
        return Portee(nature, t.id, t.code, t.libelle)
    if nature is NaturePortee.MATIERE and ligne.matiere is not None:
        return Portee(nature, ligne.matiere.id, ligne.matiere.code, ligne.matiere.libelle)
    if nature is NaturePortee.BESOIN and ligne.besoin is not None:
        b = ligne.besoin
        libelle = f"{b.type_seance.code} {b.matiere.libelle} - " + ", ".join(
            sorted(g.code for g in b.groupes)
        )
        return Portee(nature, b.id, str(b.id), libelle)
    return Portee(nature, ligne.cible_id, str(ligne.cible_id), f"{nature.libelle} {ligne.cible_id}")


def _depuis_orm(ligne: m.Regle) -> Regle:
    return Regle(
        type=TypeRegle(ligne.type),
        portee=_portee_depuis_orm(ligne),
        parametres=dict(ligne.parametres or {}),
        niveau=Niveau(ligne.niveau),
        etat=EtatRegle(ligne.etat),
        id=ligne.id,
        code=ligne.code,
        annee_scolaire_id=ligne.annee_scolaire_id,
        semestre_id=ligne.semestre_id,
        semestre_code=ligne.semestre.code if ligne.semestre is not None else None,
        date_debut=ligne.date_debut,
        date_fin=ligne.date_fin,
        origine=OrigineRegle(ligne.origine),
        commentaire=ligne.commentaire or "",
        reprise_de_id=ligne.reprise_de_id,
    )


def _vers_orm(ligne: m.Regle, regle: Regle) -> None:
    ligne.type = regle.type
    ligne.portee = regle.portee.nature
    for nature, colonne in _COLONNES_CIBLE.items():
        if colonne is not None:
            setattr(
                ligne, colonne, regle.portee.cible_id if nature is regle.portee.nature else None
            )
    ligne.parametres = dict(regle.parametres)
    ligne.niveau = regle.niveau
    ligne.etat = regle.etat
    ligne.semestre_id = regle.semestre_id
    ligne.date_debut = regle.date_debut
    ligne.date_fin = regle.date_fin
    ligne.origine = regle.origine
    ligne.commentaire = regle.commentaire or None
    if regle.reprise_de_id is not None:
        ligne.reprise_de_id = regle.reprise_de_id


def _detail(session: Session, ligne: m.Regle, parametres: Parametres) -> RegleDetail:
    regle = _depuis_orm(ligne)
    annee = ligne.annee.libelle if ligne.annee is not None else str(ligne.annee_scolaire_id)
    return RegleDetail(regle, phrase(regle.type, regle.portee, regle.parametres, parametres), annee)


def _ligne_ou_none(session: Session, annee_scolaire_id: int, code: str) -> m.Regle | None:
    return session.scalar(
        select(m.Regle).where(m.Regle.annee_scolaire_id == annee_scolaire_id, m.Regle.code == code)
    )


def _ligne(session: Session, annee_scolaire_id: int, code: str) -> m.Regle:
    ligne = _ligne_ou_none(session, annee_scolaire_id, code)
    if ligne is None:
        raise ErreurIntrouvable(f"La règle {code} n'existe pas dans cette année scolaire.")
    return ligne


def _brouillon_de_reprise(
    session: Session,
    annee: m.AnneeScolaire,
    ancienne: m.Regle,
    detail: RegleDetail,
    decalage: int,
) -> dict[str, Any] | None:
    """Brouillon de la règle reprise dans la nouvelle année, ou `None` si la cible manque."""
    regle = detail.regle
    nature = regle.portee.nature
    cible: Any = None
    if nature is NaturePortee.GROUPE:
        groupe = session.scalar(
            select(m.Groupe).where(
                m.Groupe.annee_scolaire_id == annee.id, m.Groupe.code == regle.portee.cible_code
            )
        )
        if groupe is None:
            return None
        cible = groupe.id
    elif nature is NaturePortee.MATIERE:
        ancienne_matiere = session.get(m.Matiere, regle.portee.cible_id)
        if ancienne_matiere is None:
            return None
        if ancienne_matiere.promotion_id is None:
            cible = ancienne_matiere.id
        else:
            promotion = session.scalar(
                select(m.Groupe).where(
                    m.Groupe.annee_scolaire_id == annee.id,
                    m.Groupe.code == ancienne_matiere.promotion.code,
                )
            )
            matiere = (
                session.scalar(
                    select(m.Matiere).where(
                        m.Matiere.code == ancienne_matiere.code,
                        m.Matiere.promotion_id == promotion.id,
                    )
                )
                if promotion is not None
                else None
            )
            if matiere is None:
                return None
            cible = matiere.id
    elif nature is NaturePortee.BESOIN:
        return None
    elif nature is not NaturePortee.ETABLISSEMENT:
        cible = regle.portee.cible_id

    def decaler(date: dt.date | None) -> str | None:
        if date is None:
            return None
        try:
            return date.replace(year=date.year + decalage).isoformat()
        except ValueError:  # 29 février
            return date.replace(year=date.year + decalage, day=28).isoformat()

    periode: dict[str, Any] = {}
    if regle.semestre_code:
        periode["semestre"] = regle.semestre_code
    else:
        periode["date_debut"] = decaler(regle.date_debut)
        periode["date_fin"] = decaler(regle.date_fin)
    return {
        "type": regle.type.value,
        "portee": {"nature": nature.value, "cible": cible},
        "parametres": dict(regle.parametres),
        "niveau": regle.niveau.value,
        "annee": annee.libelle,
        "periode": periode,
        "commentaire": regle.commentaire,
    }


def _remplace(regle: Regle, parametres: Parametres) -> list[str]:
    """Ce qu'une règle de réglage remplace : le standard de l'établissement."""
    if regle.definition.famille is not FamilleRegle.REGLAGE:
        return []
    if regle.type is TypeRegle.JOURS_TRAVAILLES:
        jours = ", ".join(JOURS[j] for j in parametres.jours_travailles)
        return [f"établissement : jours travaillés {jours}"]
    if regle.type is TypeRegle.PAUSE_DEJEUNER:
        if not parametres.dejeuner_actif:
            return ["établissement : pas de pause déjeuner"]
        return [
            f"établissement : déjeuner de {minutes_vers_texte(parametres.dejeuner_debut_min)} à "
            f"{minutes_vers_texte(parametres.dejeuner_fin_min)} ({parametres.dejeuner_mode})"
        ]
    return [f"établissement : pause de {parametres.pause_min} min entre deux séances"]
