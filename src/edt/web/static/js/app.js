/*
 * Comportements communs à tous les écrans (aucune dépendance autre que HTMX).
 *
 * 1. Confirmation : tout formulaire portant `data-confirmation="..."` demande
 *    confirmation avant l'envoi (macro `ui.bouton_suppression`).
 * 2. Erreurs HTMX : quand une requête HTMX échoue, le serveur renvoie un
 *    fragment de message ; il est affiché dans la zone `#messages` au lieu
 *    d'être ignoré. Une panne de réseau affiche un message générique.
 */
(function () {
  "use strict";

  document.addEventListener("submit", function (evenement) {
    var formulaire = evenement.target;
    var question = formulaire.dataset ? formulaire.dataset.confirmation : null;
    if (question && !window.confirm(question)) {
      evenement.preventDefault();
    }
  });

  function afficherMessage(html) {
    var zone = document.getElementById("messages");
    if (!zone) {
      return;
    }
    zone.innerHTML = html;
    zone.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function messageErreur(texte) {
    var bloc = document.createElement("div");
    bloc.className = "message message-erreur";
    bloc.setAttribute("role", "alert");
    var paragraphe = document.createElement("p");
    paragraphe.textContent = texte;
    bloc.appendChild(paragraphe);
    return bloc.outerHTML;
  }

  document.addEventListener("htmx:responseError", function (evenement) {
    var reponse = evenement.detail.xhr;
    var type = reponse.getResponseHeader("Content-Type") || "";
    if (type.indexOf("text/html") === 0 && reponse.responseText) {
      afficherMessage(reponse.responseText);
    } else {
      afficherMessage(messageErreur("L'opération a échoué (code " + reponse.status + ")."));
    }
  });

  document.addEventListener("htmx:sendError", function () {
    afficherMessage(messageErreur("Le serveur ne répond pas. Vérifier la connexion puis réessayer."));
  });
})();
