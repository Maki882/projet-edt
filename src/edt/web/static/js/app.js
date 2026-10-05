/*
 * Comportements communs à tous les écrans (aucune dépendance autre que HTMX).
 * Tout fonctionne aussi sans JavaScript, en plus rustique.
 *
 * 1. Confirmation : tout formulaire portant `data-confirmation="..."` demande
 *    confirmation avant l'envoi (macro `ui.bouton_suppression`).
 * 2. Erreurs HTMX : quand une requête HTMX échoue, le serveur renvoie un
 *    fragment de message ; il est affiché dans la zone `#messages` au lieu
 *    d'être ignoré. Une panne de réseau affiche un message générique.
 * 3. Panneau Assistant : ouverture et fermeture (bouton, Échap), focus,
 *    état conservé d'une page à l'autre (stockage local du navigateur).
 * 4. Champs conditionnels : un champ portant `data-visible-si="nom:v1,v2"`
 *    n'est affiché (et envoyé) que si le champ `nom` du même formulaire vaut
 *    v1 ou v2.
 * 5. Choix multiples : filtre de la liste de cases à cocher (`.choix-multiple`).
 *
 * Les points 4 et 5 s'appliquent aussi aux fragments chargés par HTMX.
 */
(function () {
  "use strict";

  /* ---- 1. Confirmation -------------------------------------------------- */

  document.addEventListener("submit", function (evenement) {
    var formulaire = evenement.target;
    var question = formulaire.dataset ? formulaire.dataset.confirmation : null;
    if (question && !window.confirm(question)) {
      evenement.preventDefault();
      evenement.stopImmediatePropagation();
    }
  }, true);

  /* ---- 2. Erreurs HTMX -------------------------------------------------- */

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

  /* ---- 3. Panneau Assistant ---------------------------------------------- */

  var CLE_ASSISTANT = "edt.assistant";

  function memoriser(valeur) {
    try {
      window.localStorage.setItem(CLE_ASSISTANT, valeur);
    } catch (erreur) {
      // Stockage refusé (navigation privée...) : l'état n'est simplement pas conservé.
      console.info("Assistant : état non conservé (" + erreur.name + ").");
    }
  }

  function memorise() {
    try {
      return window.localStorage.getItem(CLE_ASSISTANT);
    } catch (erreur) {
      console.info("Assistant : état non relu (" + erreur.name + ").");
      return null;
    }
  }

  function initialiserAssistant() {
    var panneau = document.getElementById("assistant");
    var bouton = document.getElementById("bouton-assistant");
    if (!panneau || !bouton) {
      return;
    }

    function ouvrir(avecFocus) {
      panneau.classList.add("ouvert");
      bouton.setAttribute("aria-expanded", "true");
      memoriser("ouvert");
      if (avecFocus) {
        panneau.focus();
      }
    }

    function fermer(avecFocus) {
      panneau.classList.remove("ouvert");
      bouton.setAttribute("aria-expanded", "false");
      memoriser("ferme");
      if (window.location.hash === "#assistant") {
        history.replaceState(null, "", window.location.pathname + window.location.search);
      }
      if (avecFocus) {
        bouton.focus();
      }
    }

    function estOuvert() {
      return panneau.classList.contains("ouvert");
    }

    bouton.addEventListener("click", function (evenement) {
      evenement.preventDefault();
      if (estOuvert()) {
        fermer(true);
      } else {
        ouvrir(true);
      }
    });

    panneau.querySelectorAll("[data-fermer-assistant]").forEach(function (lien) {
      lien.addEventListener("click", function (evenement) {
        evenement.preventDefault();
        fermer(true);
      });
    });

    document.addEventListener("keydown", function (evenement) {
      if (evenement.key === "Escape" && estOuvert()) {
        fermer(true);
      }
    });

    // État conservé pendant la navigation : rouvert sans animation ni vol du focus.
    if (memorise() === "ouvert" || window.location.hash === "#assistant") {
      document.body.classList.add("sans-transition");
      ouvrir(false);
      window.requestAnimationFrame(function () {
        document.body.classList.remove("sans-transition");
      });
    }
  }

  /* ---- 4. Champs conditionnels ------------------------------------------ */

  function valeurDe(formulaire, nom) {
    var elements = formulaire.querySelectorAll('[name="' + nom + '"]');
    for (var i = 0; i < elements.length; i += 1) {
      var element = elements[i];
      if ((element.type === "radio" || element.type === "checkbox")) {
        if (element.checked) {
          return element.value;
        }
      } else {
        return element.value;
      }
    }
    return "";
  }

  function appliquerVisibilite(formulaire) {
    formulaire.querySelectorAll("[data-visible-si]").forEach(function (bloc) {
      var regle = bloc.getAttribute("data-visible-si").split(":");
      var valeurs = (regle[1] || "").split(",");
      var visible = valeurs.indexOf(valeurDe(formulaire, regle[0])) !== -1;
      bloc.hidden = !visible;
      // Un champ masqué n'est ni exigé ni envoyé : le serveur applique sa valeur par défaut.
      bloc.querySelectorAll("input, select, textarea").forEach(function (champ) {
        champ.disabled = !visible;
      });
    });
  }

  function initialiserConditions(racine) {
    var formulaires = [];
    racine.querySelectorAll("[data-visible-si]").forEach(function (bloc) {
      var formulaire = bloc.closest("form") || bloc.closest("[data-formulaire]");
      if (formulaire && formulaires.indexOf(formulaire) === -1) {
        formulaires.push(formulaire);
      }
    });
    formulaires.forEach(function (formulaire) {
      if (!formulaire.dataset.conditionsActives) {
        formulaire.dataset.conditionsActives = "1";
        formulaire.addEventListener("change", function () {
          appliquerVisibilite(formulaire);
        });
      }
      appliquerVisibilite(formulaire);
    });
  }

  /* ---- 5. Choix multiples ------------------------------------------------- */

  function sansAccents(texte) {
    return texte.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  function initialiserChoixMultiples(racine) {
    racine.querySelectorAll(".choix-multiple").forEach(function (bloc) {
      var filtre = bloc.querySelector(".choix-multiple-filtre");
      var resume = bloc.parentNode.querySelector(".choix-multiple-resume");
      if (!filtre || bloc.dataset.actif) {
        return;
      }
      bloc.dataset.actif = "1";
      filtre.hidden = false;
      var options = bloc.querySelectorAll("label");

      function majResume() {
        if (!resume) {
          return;
        }
        var coches = bloc.querySelectorAll("input:checked").length;
        resume.textContent = coches === 0 ? "Aucun choix." : coches + " choisi" + (coches > 1 ? "s" : "") + ".";
      }

      filtre.addEventListener("input", function () {
        var cherche = sansAccents(filtre.value.trim());
        options.forEach(function (option) {
          option.hidden = cherche !== "" && sansAccents(option.textContent).indexOf(cherche) === -1;
        });
      });
      // Entrée dans le filtre ne doit pas envoyer le formulaire.
      filtre.addEventListener("keydown", function (evenement) {
        if (evenement.key === "Enter") {
          evenement.preventDefault();
        }
      });
      bloc.addEventListener("change", majResume);
      majResume();
    });
  }

  /* ---- Démarrage ---------------------------------------------------------- */

  function initialiser(racine) {
    initialiserConditions(racine);
    initialiserChoixMultiples(racine);
  }

  document.addEventListener("htmx:load", function (evenement) {
    initialiser(evenement.target);
  });

  function demarrer() {
    initialiserAssistant();
    initialiser(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", demarrer);
  } else {
    demarrer();
  }
})();
