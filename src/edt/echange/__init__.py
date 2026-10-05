"""Échange de données par fichiers : gabarit, imports, exports.

Ce paquet est la bibliothèque du service `edt.services.echange` : le web et
la ligne de commande n'appellent que le service, jamais ce paquet
directement.

Organisation
------------
Modules purs (sans base) :

- `rapport` : signalements, bilans et rapport d'un import ;
- `valeurs` : lecture tolérante et écriture d'une cellule (dates, heures,
  durées, booléens, codes, listes) ;
- `format` : LA description déclarative du format d'échange (feuilles,
  colonnes, identifiants métier) dont découlent le gabarit, l'export et la
  validation ;
- `lecture` : fichiers envoyés -> tableaux (CSV, XLSX, ZIP de CSV), avec
  détection de l'encodage et du séparateur ;
- `ecriture` : tableaux -> classeur XLSX ou archive ZIP de CSV ;
- `seances` : tableau intermédiaire des séances d'un emploi du temps
  existant et lecteur du tableau plat ;
- `ical` : adaptateur iCal (lecture) et écriture iCal.

Modules liés à la base (couche service) :

- `transaction` : simulation sans écriture (point de sauvegarde annulé) ;
- `donnees` : import et export des données de l'établissement ;
- `reconstitution` : import d'un emploi du temps existant (trois modes) ;
- `export_edt` : export d'une version d'emploi du temps (CSV, iCal).

Le format est décrit pour l'utilisateur dans `docs/import-export.md`.
"""
