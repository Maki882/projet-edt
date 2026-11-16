/*
 * Conversation du panneau Assistant (aucune dépendance autre que HTMX).
 * L'ouverture et la fermeture du panneau restent dans app.js (section 3).
 *
 * 1. Chargement : la conversation est demandée au serveur (événement
 *    `edt:assistant-ouvert` sur #assistant-conversation) la première fois que
 *    le panneau s'ouvre, quel que soit l'ordre d'exécution des scripts.
 * 2. Saisie : Ctrl (ou Cmd) + Entrée envoie ; la zone est vidée quand le
 *    serveur a accepté le message (en-tête `X-Assistant-Accepte`) ; un clic
 *    sur un exemple ou sur « Renvoyer ma demande » remplit la zone.
 * 3. Attente : tant qu'une réponse est en préparation, le fil est marqué
 *    `aria-busy` et la saisie désactivée ; l'interrogation du serveur
 *    s'arrête au bout de `data-limite-ms`, avec un message.
 * 4. Fil : toujours défilé en bas après un ajout ; après Confirmer ou
 *    Refuser, le focus passe au compte rendu (le bouton cliqué a disparu).
 */
(function () {
  "use strict";

  var EVENEMENT_OUVERTURE = "edt:assistant-ouvert";

  function conversation() {
    return document.getElementById("assistant-conversation");
  }

  function zoneSaisie() {
    return document.getElementById("assistant-question");
  }

  /* ---- 1. Chargement à la première ouverture ------------------------------ */

  function signalerOuverture() {
    var cible = conversation();
    if (cible) {
      // Laisse HTMX finir de traiter la page avant de déclencher la requête.
      window.setTimeout(function () {
        cible.dispatchEvent(new CustomEvent(EVENEMENT_OUVERTURE));
      }, 0);
    }
  }

  function surveillerPanneau() {
    var panneau = document.getElementById("assistant");
    if (!panneau) {
      return;
    }
    var observateur = new MutationObserver(function () {
      if (panneau.classList.contains("ouvert")) {
        signalerOuverture();
      }
    });
    observateur.observe(panneau, { attributes: true, attributeFilter: ["class"] });
    if (panneau.classList.contains("ouvert") || window.location.hash === "#assistant") {
      signalerOuverture();
    }
  }

  /* ---- 2. Saisie ----------------------------------------------------------- */

  function remplir(texte, envoyer) {
    var zone = zoneSaisie();
    if (!zone || zone.disabled) {
      return;
    }
    zone.value = texte;
    zone.focus();
    if (envoyer && zone.form && zone.form.requestSubmit) {
      zone.form.requestSubmit();
    }
  }

  document.addEventListener("keydown", function (evenement) {
    var zone = evenement.target;
    if (zone && zone.id === "assistant-question" && evenement.key === "Enter"
        && (evenement.ctrlKey || evenement.metaKey)) {
      evenement.preventDefault();
      if (zone.form && zone.value.trim() && zone.form.requestSubmit) {
        zone.form.requestSubmit();
      }
    }
  });

  document.addEventListener("click", function (evenement) {
    var element = evenement.target.closest ? evenement.target.closest("[data-exemple], [data-renvoyer]") : null;
    if (!element || !element.closest("#assistant, .page-assistant")) {
      return;
    }
    evenement.preventDefault();
    if (element.hasAttribute("data-exemple")) {
      remplir(element.getAttribute("data-exemple"), false);
    } else {
      remplir(element.getAttribute("data-renvoyer"), true);
    }
  });

  document.addEventListener("htmx:afterRequest", function (evenement) {
    var xhr = evenement.detail.xhr;
    if (xhr && xhr.getResponseHeader && xhr.getResponseHeader("X-Assistant-Accepte") === "oui") {
      var zone = zoneSaisie();
      if (zone) {
        zone.value = "";
      }
    }
  });

  /* ---- 3. Attente ---------------------------------------------------------- */

  function mettreAJourAttente() {
    var fil = document.getElementById("assistant-fil");
    if (!fil) {
      return;
    }
    var enAttente = fil.querySelector(".assistant-bulle-attente") !== null;
    fil.setAttribute("aria-busy", enAttente ? "true" : "false");
    var formulaire = document.querySelector(".assistant-saisie");
    if (formulaire) {
      formulaire.querySelectorAll("textarea, button").forEach(function (element) {
        element.disabled = enAttente || element.hasAttribute("data-toujours-desactive");
      });
    }
  }

  document.addEventListener("htmx:beforeRequest", function (evenement) {
    var element = evenement.detail.elt;
    if (!element || !element.classList || !element.classList.contains("assistant-bulle-attente")) {
      return;
    }
    if (!element.dataset.debut) {
      element.dataset.debut = String(Date.now());
    }
    var limite = parseInt(element.dataset.limiteMs || "0", 10);
    if (limite > 0 && Date.now() - parseInt(element.dataset.debut, 10) > limite) {
      evenement.preventDefault();
      element.classList.remove("assistant-bulle-attente");
      element.classList.add("assistant-bulle-erreur");
      element.removeAttribute("hx-trigger");
      element.textContent = "La réponse n'arrive pas : rechargez la page pour la voir, ou renvoyez votre demande.";
      if (window.htmx) {
        window.htmx.process(element);
      }
      mettreAJourAttente();
    }
  });

  /* ---- 4. Fil -------------------------------------------------------------- */

  function defilerEnBas() {
    var fil = document.getElementById("assistant-fil");
    if (fil) {
      fil.scrollTop = fil.scrollHeight;
    }
  }

  document.addEventListener("htmx:afterSettle", function (evenement) {
    var cible = evenement.detail.elt;
    if (!cible || !cible.closest || !cible.closest("#assistant, .page-assistant")) {
      return;
    }
    mettreAJourAttente();
    var compteRendu = cible.querySelector ? cible.querySelector("[data-focus]") : null;
    if (!compteRendu && cible.hasAttribute && cible.hasAttribute("data-focus")) {
      compteRendu = cible;
    }
    if (compteRendu) {
      compteRendu.focus();
    } else {
      defilerEnBas();
    }
  });

  function demarrer() {
    surveillerPanneau();
    mettreAJourAttente();
    defilerEnBas();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", demarrer);
  } else {
    demarrer();
  }
})();
