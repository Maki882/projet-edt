/*
 * Comportements des écrans de l'emploi du temps (aucune dépendance autre que HTMX,
 * chargé en `defer` après app.js). Tout passe par la délégation d'événements :
 * les fragments remplacés par HTMX (panneau, suivi d'un calcul) n'ont rien à réattacher.
 *
 * 1. Recherche rapide : un champ `data-filtre="<id du select>"` masque les options
 *    du sélecteur qui ne contiennent pas le texte saisi (sans tenir compte des accents).
 * 2. Envoi automatique : un `select[data-envoi-auto]` soumet son formulaire au
 *    changement ; `data-desactiver="<nom> <nom>"` retire d'abord ces champs de l'envoi
 *    (changer de semestre ne doit garder ni la version ni le groupe de l'ancien).
 * 3. Impression : un bouton `data-imprimer` ouvre l'impression du navigateur
 *    (c'est l'export PDF).
 * 4. Panneau latéral : un clic sur un bloc le met en évidence ; `data-fermer-panneau`
 *    ou la touche Échap vident le panneau.
 * Sans JavaScript, chaque action reste possible par un lien ou un formulaire ordinaire.
 */
(function () {
  "use strict";

  function normaliser(texte) {
    return (texte || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "");
  }

  document.addEventListener("input", function (evenement) {
    var champ = evenement.target;
    if (!champ.dataset || !champ.dataset.filtre) {
      return;
    }
    var liste = document.getElementById(champ.dataset.filtre);
    if (!liste) {
      return;
    }
    var recherche = normaliser(champ.value).trim();
    var premiereVisible = null;
    Array.prototype.forEach.call(liste.options, function (option) {
      var visible = !recherche || normaliser(option.textContent).indexOf(recherche) !== -1;
      option.hidden = !visible;
      option.disabled = !visible && !option.selected;
      if (visible && premiereVisible === null && option.value) {
        premiereVisible = option;
      }
    });
    Array.prototype.forEach.call(liste.querySelectorAll("optgroup"), function (groupe) {
      var visibles = groupe.querySelectorAll("option:not([hidden])").length;
      groupe.hidden = visibles === 0;
    });
    if (recherche && premiereVisible && liste.selectedOptions.length && liste.selectedOptions[0].hidden) {
      premiereVisible.selected = true;
    }
  });

  document.addEventListener("keydown", function (evenement) {
    var champ = evenement.target;
    if (evenement.key === "Enter" && champ.dataset && champ.dataset.filtre) {
      evenement.preventDefault();
      if (champ.form) {
        champ.form.requestSubmit ? champ.form.requestSubmit() : champ.form.submit();
      }
    }
    if (evenement.key === "Escape") {
      fermerPanneau();
    }
  });

  document.addEventListener("change", function (evenement) {
    var champ = evenement.target;
    if (!champ.matches || !champ.matches("select[data-envoi-auto]") || !champ.form) {
      return;
    }
    (champ.dataset.desactiver || "").split(" ").forEach(function (nom) {
      if (!nom) {
        return;
      }
      Array.prototype.forEach.call(champ.form.querySelectorAll("[name='" + nom + "']"), function (element) {
        element.disabled = true;
      });
    });
    champ.form.requestSubmit ? champ.form.requestSubmit() : champ.form.submit();
  });

  function fermerPanneau() {
    var panneau = document.getElementById("edt-panneau");
    if (panneau) {
      panneau.innerHTML = "";
    }
    Array.prototype.forEach.call(document.querySelectorAll(".edt-bloc-selection"), function (bloc) {
      bloc.classList.remove("edt-bloc-selection");
    });
  }

  document.addEventListener("click", function (evenement) {
    var cible = evenement.target.closest ? evenement.target.closest("[data-imprimer], [data-fermer-panneau], .edt-bloc") : null;
    if (!cible) {
      return;
    }
    if (cible.hasAttribute("data-imprimer")) {
      window.print();
      return;
    }
    if (cible.hasAttribute("data-fermer-panneau")) {
      evenement.preventDefault();
      fermerPanneau();
      return;
    }
    if (cible.classList.contains("edt-bloc") && window.htmx) {
      Array.prototype.forEach.call(document.querySelectorAll(".edt-bloc-selection"), function (bloc) {
        bloc.classList.remove("edt-bloc-selection");
      });
      cible.classList.add("edt-bloc-selection");
    }
  });
})();
