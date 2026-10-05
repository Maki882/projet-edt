"""Domaine : logique pure, sans base de données ni solveur.

- `enumerations` : valeurs fermées partagées par toutes les couches ;
- `heures` : conversions entre minutes depuis minuit et texte (« 8 h 30 ») ;
- `calendrier` : semaines d'un semestre, jours fériés français ;
- `contraintes` : catalogue des contraintes (codes stables) ;
- `parametres` : réglages globaux typés et leurs valeurs par défaut ;
- `instantane` : représentation figée d'un semestre à planifier (contrat
  commun au vérificateur et au solveur) ;
- `depliage` : passage des besoins aux séances hebdomadaires à placer.
"""
