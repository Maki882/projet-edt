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
 *    note son échec dans la console sans bandeau. Le bandeau de panne de
 *    réseau s'efface dès qu'une requête HTMX aboutit de nouveau (suivi d'un
 *    calcul après un redémarrage du serveur). Un message ajouté hors de sa
 *    zone (`hx-swap-oob` sur `#messages`) est ramené à l'écran.
 * 3. Panneau Assistant : ouverture et fermeture (bouton, Échap), focus,
 *    état conservé d'une page à l'autre (stockage local du navigateur).
 * 4. Champs conditionnels : un champ portant `data-visible-si="nom:v1,v2"`
 *    n'est affiché (et envoyé) que si le champ `nom` du même formulaire vaut
 *    v1 ou v2 ; plusieurs conditions séparées par « ; » doivent toutes tenir
 *    (`portee:etablissement;p_mode:libre`).
 * 5. Choix multiples : filtre de la liste de cases à cocher (`.choix-multiple`).
 * 6. Envoi : les boutons d'envoi d'un formulaire `method="post"` envoyé par le
 *    navigateur sont désactivés pendant l'envoi (un double clic n'envoie
 *    qu'une fois), puis réactivés au retour sur la page (`pageshow`) ou après
 *    `DELAI_REACTIVATION_MS` si la page reste affichée. Un formulaire portant
 *    `data-attente="..."` affiche en plus ce texte sur ses boutons, jusqu'à la
 *    page suivante (opérations longues). Les boutons rattachés par l'attribut
 *    `form=`, placés hors du formulaire, sont compris. Les envois HTMX ne sont
 *    pas concernés (HTMX les gère lui-même).
 * 7. Blocs repliables : un `<details id="...">` ouvert le reste quand HTMX
 *    remplace la zone qui le contient.
 * 8. Arbres repliables : un conteneur `data-arbre="clé"` mémorise ses nœuds
 *    ouverts (`<details data-noeud="...">`) d'une page à l'autre ; les boutons
 *    `data-arbre-action="deplier|replier"` et `data-arbre-cible="id"` ouvrent
 *    ou ferment tout (masqués sans JavaScript).
 * 9. Champ invalide replié : quand le navigateur refuse l'envoi d'un formulaire
 *    pour un champ invalide placé dans un `<details>` fermé, ce bloc (et ceux
 *    qui le contiennent) s'ouvre pour que la bulle du navigateur soit visible.
 * 10. Tableaux défilants : après un remplacement HTMX, un tableau qui montre
 *    une ligne de confirmation de suppression (`tr.ligne-suppression`) revient
 *    à gauche, pour que la question et ses boutons soient lisibles.
 * 11. Volets mobiles : les commandes du menu et des filtres replient leur
 *    contenu sur les petits écrans. Sans JavaScript, il reste accessible.
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

  /* ---- 6. Boutons désactivés pendant un envoi ------------------------------ */

  var DELAI_REACTIVATION_MS = 10000;

  function boutonsEnvoi(formulaire) {
    // `elements` comprend les boutons rattachés par `form=`, placés hors du formulaire.
    return Array.prototype.filter.call(formulaire.elements || [], function (element) {
      return element.tagName === "BUTTON" && element.type === "submit";
    });
  }

  function reactiver(boutons) {
    Array.prototype.forEach.call(boutons, function (bouton) {
      if (!bouton.hasAttribute("data-envoi-en-cours")) {
        return;
      }
      bouton.disabled = false;
      bouton.removeAttribute("data-envoi-en-cours");
      if (bouton.hasAttribute("data-texte-avant-envoi")) {
        bouton.textContent = bouton.getAttribute("data-texte-avant-envoi");
        bouton.removeAttribute("data-texte-avant-envoi");
      }
    });
  }

  document.addEventListener("submit", function (evenement) {
    var formulaire = evenement.target;
    if (evenement.defaultPrevented || !formulaire.getAttribute) {
      return; // refusé (confirmation) ou pris en charge par HTMX
    }
    var texte = formulaire.dataset.attente || null;
    var methode = (formulaire.getAttribute("method") || "get").toLowerCase();
    if (!texte && methode !== "post") {
      return;
    }
    var boutons = boutonsEnvoi(formulaire);
    // Désactivés après la construction des données envoyées : l'envoi n'est pas gêné.
    window.setTimeout(function () {
      boutons.forEach(function (bouton) {
        bouton.disabled = true;
        bouton.setAttribute("data-envoi-en-cours", "");
        if (texte) {
          bouton.setAttribute("data-texte-avant-envoi", bouton.textContent);
          bouton.textContent = texte;
        }
      });
    }, 0);
    if (!texte) {
      // Page restée affichée (téléchargement, envoi interrompu) : pas de bouton mort.
      window.setTimeout(function () {
        reactiver(boutons);
      }, DELAI_REACTIVATION_MS);
    }
  });

  // Retour arrière : le navigateur peut réafficher la page telle qu'elle était à l'envoi.
  window.addEventListener("pageshow", function () {
    reactiver(document.querySelectorAll("[data-envoi-en-cours]"));
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

  function messageErreur(texte, reseau) {
    var bloc = document.createElement("div");
    bloc.className = "message message-erreur";
    bloc.setAttribute("role", "alert");
    if (reseau) {
      bloc.setAttribute("data-erreur-reseau", "");
    }
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
    afficherMessage(messageErreur("Le serveur ne répond pas. Vérifier la connexion puis réessayer.", true));
  });

  // Le serveur répond de nouveau : le bandeau de panne de réseau n'a plus lieu d'être.
  document.addEventListener("htmx:afterRequest", function (evenement) {
    if (!evenement.detail.successful) {
      return;
    }
    var zone = document.getElementById("messages");
    var bandeau = zone ? zone.querySelector("[data-erreur-reseau]") : null;
    if (bandeau) {
      bandeau.remove();
    }
  });

  // Message ajouté hors de sa zone (`hx-swap-oob`) : ramené à l'écran s'il est hors de vue.
  document.addEventListener("htmx:oobAfterSwap", function (evenement) {
    // La zone a pu être remplacée : celle du document, pas celle de l'événement.
    var zone = document.getElementById("messages");
    if (evenement.target && evenement.target.id === "messages" && zone && zone.textContent.trim()) {
      zone.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
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
      if (evenement.key === "Escape" && !evenement.defaultPrevented && estOuvert()) {
        fermer(true);
      }
    });

    // État conservé pendant la navigation, sur un grand écran seulement : au téléphone, le
    // panneau couvre toute la page et ne se rouvre pas de lui-même d'une page à l'autre.
    // Rouvert sans animation ni vol du focus.
    var grandEcran = !window.matchMedia("(max-width: 58rem)").matches;
    if ((memorise() === "ouvert" && grandEcran) || window.location.hash === "#assistant") {
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

  /* ---- 10. Ligne de confirmation lisible dans un tableau défilant ----------- */

  document.addEventListener("htmx:afterSettle", function () {
    document.querySelectorAll("tr.ligne-suppression").forEach(function (ligne) {
      var cadre = ligne.closest(".tableau-defilant");
      if (cadre) {
        cadre.scrollLeft = 0;
      }
    });
  });

  /* ---- 9. Champ invalide dans un bloc replié -------------------------------- */

  // « invalid » ne remonte pas : écouté en phase de capture.
  document.addEventListener("invalid", function (evenement) {
    var bloc = evenement.target.closest ? evenement.target.closest("details") : null;
    while (bloc) {
      bloc.open = true;
      bloc = bloc.parentElement ? bloc.parentElement.closest("details") : null;
    }
  }, true);

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

  function initialiserVoletsMobiles() {
    document.querySelectorAll("[data-volet-mobile]").forEach(function (bouton) {
      var volet = document.getElementById(bouton.getAttribute("aria-controls"));
      if (!volet) {
        return;
      }

      bouton.addEventListener("click", function () {
        bouton.setAttribute("aria-expanded", bouton.getAttribute("aria-expanded") === "false");
      });

      function fermerAvecEchap(evenement) {
        if (evenement.key === "Escape" && bouton.offsetWidth && bouton.getAttribute("aria-expanded") === "true") {
          evenement.preventDefault();
          bouton.setAttribute("aria-expanded", "false");
          bouton.focus();
        }
      }

      bouton.addEventListener("keydown", fermerAvecEchap);
      volet.addEventListener("keydown", fermerAvecEchap);
      bouton.hidden = false;
    });
  }

  function demarrer() {
    initialiserAssistant();
    initialiserVoletsMobiles();
    montrerPageActive();
    initialiser(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", demarrer);
  } else {
    demarrer();
  }
})();
