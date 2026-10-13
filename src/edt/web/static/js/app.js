/*
 * Comportements communs à tous les écrans (aucune dépendance autre que HTMX).
 * Tout fonctionne aussi sans JavaScript, en plus rustique.
 *
 * 1. Confirmation : tout formulaire portant `data-confirmation="..."` demande
 *    confirmation avant l'envoi (macro `ui.bouton_suppression`).
 * 2. Erreurs HTMX : quand une requête HTMX échoue, le serveur renvoie un
 *    fragment de message ; il est affiché dans la zone `#messages` au lieu
 *    d'être ignoré. Une panne de réseau affiche un message générique. Un
 *    élément accessoire marqué `data-erreur-silencieuse` (compteur d'onglet)
 *    note son échec dans la console sans bandeau.
 * 3. Panneau Assistant : ouverture et fermeture (bouton, Échap), focus,
 *    état conservé d'une page à l'autre (stockage local du navigateur).
 * 4. Champs conditionnels : un champ portant `data-visible-si="nom:v1,v2"`
 *    n'est affiché (et envoyé) que si le champ `nom` du même formulaire vaut
 *    v1 ou v2 ; plusieurs conditions séparées par « ; » doivent toutes tenir
 *    (`portee:etablissement;p_mode:libre`).
 * 5. Choix multiples : filtre de la liste de cases à cocher (`.choix-multiple`).
 * 6. Attente : un formulaire portant `data-attente="..."` désactive ses boutons
 *    pendant l'envoi et affiche ce texte (opérations longues).
 * 7. Blocs repliables : un `<details id="...">` ouvert le reste quand HTMX
 *    remplace la zone qui le contient.
 * 8. Arbres repliables : un conteneur `data-arbre="clé"` mémorise ses nœuds
 *    ouverts (`<details data-noeud="...">`) d'une page à l'autre ; les boutons
 *    `data-arbre-action="deplier|replier"` et `data-arbre-cible="id"` ouvrent
 *    ou ferment tout (masqués sans JavaScript).
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

  /* ---- 6. Attente pendant un envoi long ----------------------------------- */

  document.addEventListener("submit", function (evenement) {
    var formulaire = evenement.target;
    var texte = formulaire.dataset ? formulaire.dataset.attente : null;
    if (!texte || evenement.defaultPrevented) {
      return;
    }
    // Désactivés après la construction des données envoyées : l'envoi n'est pas gêné.
    window.setTimeout(function () {
      formulaire.querySelectorAll("button[type=submit]").forEach(function (bouton) {
        bouton.disabled = true;
        bouton.textContent = texte;
      });
    }, 0);
  });

  /* ---- 7. Blocs repliables conservés pendant un remplacement HTMX ------------ */

  var blocsOuverts = [];

  document.addEventListener("htmx:beforeSwap", function (evenement) {
    var cible = evenement.detail.target;
    blocsOuverts = [];
    if (cible && cible.querySelectorAll) {
      cible.querySelectorAll("details[id][open]").forEach(function (bloc) {
        blocsOuverts.push(bloc.id);
      });
    }
  });

  document.addEventListener("htmx:afterSwap", function () {
    blocsOuverts.forEach(function (ident) {
      var bloc = document.getElementById(ident);
      if (bloc) {
        bloc.open = true;
      }
    });
    blocsOuverts = [];
  });

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
    var source = evenement.detail.elt;
    if (source && source.closest && source.closest("[data-erreur-silencieuse]")) {
      // Élément accessoire (compteur d'onglet) : l'erreur, déjà journalisée par le
      // serveur, est notée dans la console sans bandeau d'erreur sur la page.
      console.warn("Élément accessoire non chargé (code " + reponse.status + ") : " + evenement.detail.pathInfo.requestPath);
      return;
    }
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
      var visible = bloc.getAttribute("data-visible-si").split(";").every(function (condition) {
        var regle = condition.split(":");
        var valeurs = (regle[1] || "").split(",");
        return valeurs.indexOf(valeurDe(formulaire, regle[0])) !== -1;
      });
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

  /* ---- 8. Arbres repliables ------------------------------------------------- */

  function cleArbre(arbre) {
    return "edt.arbre." + arbre.getAttribute("data-arbre");
  }

  function memoriserArbre(arbre) {
    var ouverts = [];
    arbre.querySelectorAll("details[data-noeud][open]").forEach(function (noeud) {
      ouverts.push(noeud.getAttribute("data-noeud"));
    });
    try {
      window.localStorage.setItem(cleArbre(arbre), JSON.stringify(ouverts));
    } catch (erreur) {
      console.info("Arbre : état non conservé (" + erreur.name + ").");
    }
  }

  function initialiserArbres(racine) {
    racine.querySelectorAll("[data-arbre]").forEach(function (arbre) {
      if (arbre.dataset.arbreActif) {
        return;
      }
      arbre.dataset.arbreActif = "1";
      var ouverts = null;
      try {
        ouverts = JSON.parse(window.localStorage.getItem(cleArbre(arbre)) || "null");
      } catch (erreur) {
        console.info("Arbre : état non relu (" + erreur.name + ").");
      }
      if (Array.isArray(ouverts)) {
        arbre.querySelectorAll("details[data-noeud]").forEach(function (noeud) {
          noeud.open = ouverts.indexOf(noeud.getAttribute("data-noeud")) !== -1;
        });
      }
      // « toggle » ne remonte pas : écouté en phase de capture.
      arbre.addEventListener("toggle", function () {
        memoriserArbre(arbre);
      }, true);
    });
    racine.querySelectorAll("[data-arbre-action]").forEach(function (bouton) {
      bouton.hidden = false;
      if (bouton.dataset.arbreActif) {
        return;
      }
      bouton.dataset.arbreActif = "1";
      bouton.addEventListener("click", function () {
        var arbre = document.getElementById(bouton.getAttribute("data-arbre-cible"));
        if (!arbre) {
          return;
        }
        var ouvrir = bouton.getAttribute("data-arbre-action") === "deplier";
        arbre.querySelectorAll("details[data-noeud]").forEach(function (noeud) {
          noeud.open = ouvrir;
        });
        memoriserArbre(arbre);
      });
    });
  }

  /* ---- Démarrage ---------------------------------------------------------- */

  function initialiser(racine) {
    initialiserConditions(racine);
    initialiserChoixMultiples(racine);
    initialiserArbres(racine);
  }

  document.addEventListener("htmx:load", function (evenement) {
    initialiser(evenement.target);
  });

  function montrerPageActive() {
    // Sur un petit écran, la sous-navigation défile : la page affichée y est rendue visible.
    var actif = document.querySelector(".sous-navigation a.actif");
    var bande = actif ? actif.closest(".sous-navigation") : null;
    if (bande && bande.scrollWidth > bande.clientWidth) {
      bande.scrollLeft = actif.offsetLeft - bande.clientWidth / 2 + actif.offsetWidth / 2;
    }
  }

  function demarrer() {
    initialiserAssistant();
    montrerPageActive();
    initialiser(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", demarrer);
  } else {
    demarrer();
  }
})();
