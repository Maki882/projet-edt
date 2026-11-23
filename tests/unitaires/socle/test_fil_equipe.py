"""Fil des messages d'une équipe d'agents : relevé dans les transcriptions, ordre, erreurs.

L'outil `.claude/skills/equipe-messagerie/fil.py` reconstitue les échanges d'une équipe à partir
des transcriptions de ses agents. Ces tests l'exercent sur de petites transcriptions écrites
ici, de la même forme que les vraies.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

CHEMIN = Path(__file__).resolve().parents[3] / ".claude" / "skills" / "equipe-messagerie" / "fil.py"
_spec = importlib.util.spec_from_file_location("fil_equipe", CHEMIN)
fil_equipe = importlib.util.module_from_spec(_spec)
# Les classes de données du module se résolvent par `sys.modules` : il y est inscrit avant
# son exécution.
sys.modules["fil_equipe"] = fil_equipe
_spec.loader.exec_module(fil_equipe)


def _envoi(heure: str, destinataire: str, texte: str) -> dict:
    """Ligne de transcription d'un agent qui envoie un message."""
    appel = {
        "type": "tool_use",
        "name": "SendMessage",
        "input": {"to": destinataire, "message": texte},
    }
    return {"type": "assistant", "timestamp": heure, "message": {"content": [appel]}}


def _du_pilote(heure: str, texte: str) -> dict:
    """Ligne de transcription d'un agent qui reçoit un message de la conversation principale."""
    contenu = (
        "The coordinator sent a message while you were working:\n"
        + texte
        + "\n\nAddress this before completing your current task."
    )
    return {"type": "user", "timestamp": heure, "message": {"content": contenu}}


def _transcription(dossier: Path, nom: str, lignes: list[dict | str]) -> Path:
    fichier = dossier / f"{nom}.jsonl"
    texte = "\n".join(
        ligne if isinstance(ligne, str) else json.dumps(ligne, ensure_ascii=False)
        for ligne in lignes
    )
    fichier.write_text(texte + "\n", encoding="utf-8")
    return fichier


@pytest.fixture
def equipe(tmp_path) -> dict[str, tuple[str, Path]]:
    """Deux agents dont les messages se croisent : le fil ne suit pas l'ordre des fichiers."""
    tache = _envoi(
        "2026-11-23T10:00:05.000Z", "id-calcul", "[tâche] T1 : écrire le service\nDétails."
    )
    chef = _transcription(
        tmp_path,
        "chef",
        [
            _du_pilote("2026-11-23T10:00:00.000Z", "[décision] plan approuvé"),
            tache,
            # Les vraies transcriptions recopient chaque appel dans une ligne technique, sans
            # horodatage : elle ne doit ni compter une seconde fois, ni faire échouer le relevé.
            {"type": "api-request-blob", "message": tache["message"]},
        ],
    )
    calcul = _transcription(
        tmp_path,
        "calcul",
        [
            _envoi("2026-11-23T09:59:00.000Z", "id-chef", "[question] quel périmètre ?"),
            _envoi("2026-11-23T10:02:00.000Z", "id-chef", "[livraison] T1 faite, commit abc1234"),
            _envoi("2026-11-23T10:02:30.000Z", "main", "[information] terminé"),
        ],
    )
    return {"chef": ("id-chef", chef), "calcul": ("id-calcul", calcul)}


def test_le_fil_range_les_messages_par_heure_et_nomme_les_membres(equipe):
    messages = fil_equipe.fil(equipe)
    assert [(m.de, m.a) for m in messages] == [
        ("calcul", "chef"),
        ("pilote", "chef"),
        ("chef", "calcul"),
        ("calcul", "chef"),
        ("calcul", "pilote"),
    ]
    assert messages[2].objet == "[tâche] T1 : écrire le service"


def test_le_message_du_pilote_est_rendu_sans_l_habillage_de_l_environnement(equipe):
    du_pilote = fil_equipe.fil(equipe)[1]
    assert du_pilote.de == "pilote" and du_pilote.texte == "[décision] plan approuvé"


def test_un_destinataire_hors_de_l_equipe_garde_son_identifiant(tmp_path):
    seul = _transcription(tmp_path, "seul", [_envoi("2026-11-23T09:00:00.000Z", "inconnu-42", "x")])
    assert fil_equipe.fil({"seul": ("id-seul", seul)})[0].a == "inconnu-42"


def test_le_document_liste_puis_detaille_chaque_message(equipe):
    document = fil_equipe.en_markdown(fil_equipe.fil(equipe))
    assert "5 messages" in document
    assert "| 3 | " in document and "| chef | calcul | [tâche] T1 : écrire le service |" in document
    assert "## 4. calcul à chef" in document and "commit abc1234" in document


def test_un_fil_vide_le_dit(tmp_path):
    vide = _transcription(tmp_path, "vide", [{"type": "assistant", "message": {"content": []}}])
    assert "Aucun message relevé" in fil_equipe.en_markdown(fil_equipe.fil({"a": ("id-a", vide)}))


@pytest.mark.parametrize(
    ("valeurs", "attendu"),
    [
        ([], "aucun membre"),
        (["chef=id"], "rôle=identifiant=transcription"),
        (["chef=id=absent.jsonl"], "transcription introuvable"),
    ],
)
def test_une_commande_mal_formee_est_refusee_avec_son_remede(valeurs, attendu):
    with pytest.raises(fil_equipe.ErreurFil, match=attendu):
        fil_equipe.lire_membres(valeurs)


def test_un_role_donne_deux_fois_est_refuse(equipe):
    chemin = equipe["chef"][1]
    with pytest.raises(fil_equipe.ErreurFil, match="deux fois"):
        fil_equipe.lire_membres([f"chef=a={chemin}", f"chef=b={chemin}"])


def test_un_message_sans_horodatage_est_une_erreur_qui_nomme_la_ligne(tmp_path):
    sans_heure = {
        "type": "assistant",
        "message": {"content": _envoi("x", "y", "z")["message"]["content"]},
    }
    fichier = _transcription(tmp_path, "casse", [sans_heure])
    with pytest.raises(fil_equipe.ErreurFil, match="ligne 1 : message sans horodatage"):
        fil_equipe.fil({"casse": ("id", fichier)})


def test_la_commande_ecrit_le_fil_et_rend_zero(equipe, tmp_path):
    sortie = tmp_path / "sortie" / "fil.md"
    arguments = [f"--membre={role}={ident}={chemin}" for role, (ident, chemin) in equipe.items()]
    assert fil_equipe.principal([*arguments, f"--sortie={sortie}"]) == 0
    assert sortie.read_text(encoding="utf-8").startswith("# Fil des messages de l'équipe")


def test_la_commande_rend_un_si_une_transcription_manque(tmp_path, caplog):
    assert fil_equipe.principal(["--membre", f"chef=id={tmp_path / 'absent.jsonl'}"]) == 1
    assert "transcription introuvable" in caplog.text
