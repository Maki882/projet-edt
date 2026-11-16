"""Aides partagées des tests d'export d'emploi du temps (unitaires et intégration).

- `exporter_edt(version_id, format_, couche, **filtre)` : l'export d'une version
  par le service, dans sa propre session ;
- `lignes_csv(fichier)` : les lignes d'un export CSV (UTF-8 avec BOM, point-virgule).
"""

from __future__ import annotations

import csv
import io

from edt.db.moteur import session_base
from edt.services import echange as service


def exporter_edt(version_id: int, format_: str = "csv", couche: str = "hebdo", **filtre):
    with session_base() as session:
        return service.exporter_emploi_du_temps(session, version_id, format_, couche, **filtre)


def lignes_csv(fichier) -> list[dict[str, str]]:
    texte = fichier.contenu.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(texte), delimiter=";"))
