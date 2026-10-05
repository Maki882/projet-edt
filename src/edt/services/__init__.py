"""Services : fonctions typées appelées par l'interface web et la ligne de commande.

Une fonctionnalité existe d'abord ici, comme fonction aux paramètres et
retours explicites, puis comme route web et comme commande. Conventions :

- un service reçoit la `Session` SQLAlchemy de l'appelant et **ne valide pas
  la transaction** (`session.flush()` au besoin) : c'est l'appelant qui
  appelle `session.commit()`. Exception : `edt.services.generation`, dont les
  fonctions ouvrent leurs propres sessions car un calcul vit plus longtemps
  qu'une requête ;
- un service lève une `ErreurEDT` (voir `edt.erreurs`) quand il refuse une
  opération, avec un message lisible ; il ne renvoie jamais un code d'erreur ;
- un service journalise ce qu'il fait d'important avec
  `logging.getLogger(__name__)`.
"""
