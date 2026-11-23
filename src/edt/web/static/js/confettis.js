/*
 * Confettis des onglets de tests (aucune dépendance, chargé en `defer` par tests/page.html).
 *
 * Le serveur pose `data-confettis` sur le bloc de suivi (#suivi-tests) dans la seule
 * réponse qui voit une exécution finir sans échec (templates/tests/_suivi.html). Quand
 * HTMX insère ce bloc (`htmx:load`), le script retire le repère et lance une pluie de
 * confettis d'environ 2,5 s sur un canevas plein écran qui ne capte aucun clic, puis
 * retire le canevas. Avec « réduire les animations » (prefers-reduced-motion), rien ne
 * bouge : pas de confettis. Rien à l'impression (règle `.confettis` de tests.css).
 */
(function () {
  "use strict";

  var DUREE_MS = 2500;
  var NOMBRE = 150;
  var COULEURS = ["#e63946", "#f4a261", "#e9c46a", "#2a9d8f", "#457b9d", "#9b5de5"];

  function mouvementReduit() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function lancerConfettis() {
    var canevas = document.createElement("canvas");
    var contexte = canevas.getContext && canevas.getContext("2d");
    if (!contexte) {
      console.warn("Confettis : canevas 2D indisponible dans ce navigateur.");
      return;
    }
    canevas.className = "confettis";
    canevas.setAttribute("aria-hidden", "true");
    canevas.style.cssText =
      "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:9999";
    var largeur = (canevas.width = window.innerWidth);
    var hauteur = (canevas.height = window.innerHeight);
    document.body.appendChild(canevas);

    var morceaux = [];
    for (var i = 0; i < NOMBRE; i++) {
      morceaux.push({
        x: Math.random() * largeur,
        y: -Math.random() * hauteur * 0.5,
        vx: (Math.random() - 0.5) * 4,
        vy: 2 + Math.random() * 4,
        angle: Math.random() * Math.PI,
        rotation: (Math.random() - 0.5) * 0.3,
        taille: 6 + Math.random() * 6,
        couleur: COULEURS[i % COULEURS.length],
      });
    }

    var debut = null;
    function image(instant) {
      if (debut === null) {
        debut = instant;
      }
      var ecoule = instant - debut;
      if (ecoule >= DUREE_MS) {
        canevas.remove();
        return;
      }
      contexte.clearRect(0, 0, largeur, hauteur);
      // Fondu sur la dernière demi-seconde.
      contexte.globalAlpha = Math.min(1, (DUREE_MS - ecoule) / 500);
      morceaux.forEach(function (m) {
        m.x += m.vx;
        m.y += m.vy;
        m.vy += 0.05;
        m.angle += m.rotation;
        contexte.save();
        contexte.translate(m.x, m.y);
        contexte.rotate(m.angle);
        contexte.fillStyle = m.couleur;
        contexte.fillRect(-m.taille / 2, -m.taille / 4, m.taille, m.taille / 2);
        contexte.restore();
      });
      window.requestAnimationFrame(image);
    }
    window.requestAnimationFrame(image);
  }

  document.addEventListener("htmx:load", function (evenement) {
    var racine = evenement.target;
    var bloc = racine.matches && racine.matches("[data-confettis]")
      ? racine
      : racine.querySelector && racine.querySelector("[data-confettis]");
    if (!bloc) {
      return;
    }
    bloc.removeAttribute("data-confettis"); // une seule fois pour ce bloc
    if (!mouvementReduit()) {
      lancerConfettis();
    }
  });
})();
