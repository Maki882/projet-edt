"""Produit les PDF des quatre livrets du cours à partir de leur texte Markdown.

Usage, depuis la racine du dépôt, avec l'interpréteur de l'application :

    python docs/livrets/produire_pdf.py                      # les quatre livrets
    python docs/livrets/produire_pdf.py 3-guide-utilisateur  # un seul livret
    python docs/livrets/produire_pdf.py --chrome /chemin/vers/chrome

Sortie : `docs/livrets/pdf/<livret>.pdf`, remplacé à chaque exécution.

Étapes :

1. Markdown vers HTML avec `markdown-it-py` (installée avec l'application, par
   `typer` et `rich`), tableaux compris. Chaque titre reçoit l'identifiant que
   GitHub lui donne : les liens de la table des matières (`#1-...`) restent
   valides dans le PDF.
2. Mise en page d'impression : page de garde seule sur sa page, un saut de page
   à chaque ligne `---` du Markdown, titres jamais laissés seuls en bas de page,
   images et tableaux limités à la largeur de la page, une image suivie de sa
   légende (`*Figure N. ...*`) gardée sur la même page, numéro de page en pied
   (sauf sur la page de garde).
3. Impression par Chrome (ou Chromium) sans interface : `--print-to-pdf`.

Les liens vers un autre livret pointent vers son PDF ; les liens vers d'autres
fichiers du dépôt deviennent du texte simple (ils n'ont pas de sens hors du dépôt).
Le HTML intermédiaire est écrit dans un dossier temporaire, effacé à la fin.

Dépendances : `markdown-it-py` et un navigateur Chrome ou Chromium. Chrome est
cherché dans `--chrome`, puis dans la variable `EDT_CHROME`, puis aux
emplacements habituels ; s'il est introuvable, le script s'arrête et dit quoi faire.
Code de sortie : 0 si tous les PDF sont produits, 1 sinon.
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

journal = logging.getLogger("produire_pdf")

DOSSIER = Path(__file__).resolve().parent
SORTIE = DOSSIER / "pdf"
LIVRETS = (
    "1-specifications-fonctionnelles",
    "2-specifications-techniques-et-organisation",
    "3-guide-utilisateur",
    "4-cahier-de-recette",
)
DELAI_CHROME_S = 180
SIGNATURE_PNG = bytes.fromhex("89504e470d0a1a0a")
LARGEUR_ECRAN_PX = 1366
"""Largeur des captures d'écran : une capture de cette largeur occupe toute la largeur du texte."""
LARGEUR_TEXTE_MM = 176
"""Largeur utile d'une page A4 avec les marges de `STYLE` (210 - 2 x 17 mm)."""
HAUTEUR_FIGURE_MM = 90
"""Hauteur maximale d'une capture : une page en garde deux, ou une avec son texte."""
PART_FLOTTANTE = 0.6
"""Une capture moins large que cette part de `LARGEUR_ECRAN_PX` (un panneau seul) se place à
droite du texte au lieu de l'interrompre."""
EMPLACEMENTS_CHROME = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
NOMS_CHROME = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")

STYLE = """
@page {
  size: A4;
  margin: 18mm 17mm 20mm 17mm;
  @bottom-center { content: counter(page) " / " counter(pages); font: 9pt Arial, sans-serif; color: #555; }
}
@page :first { @bottom-center { content: none; } }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Segoe UI", "DejaVu Sans", Arial, sans-serif; font-size: 10pt; line-height: 1.42;
       color: #1a1a1a; margin: 0; }
h1, h2, h3, h4 { color: #16325c; line-height: 1.25; break-after: avoid; break-inside: avoid; }
h1 { font-size: 24pt; margin: 0 0 6pt; }
h2 { font-size: 16pt; margin: 18pt 0 8pt; padding-bottom: 3pt; border-bottom: 1px solid #c8d0dc; }
h3 { font-size: 12.5pt; margin: 14pt 0 6pt; }
h4 { font-size: 11pt; margin: 12pt 0 4pt; }
p, li { orphans: 3; widows: 3; }
p { margin: 0 0 7pt; }
ul, ol { margin: 0 0 8pt; padding-left: 18pt; }
li { margin-bottom: 2pt; }
a { color: #1a4a8a; text-decoration: none; }
code { font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 9pt; background: #f1f3f6;
       padding: 0 2pt; border-radius: 2pt; overflow-wrap: break-word; }
pre { background: #f1f3f6; padding: 6pt 8pt; font-size: 8.5pt; line-height: 1.35; white-space: pre-wrap;
      overflow-wrap: anywhere; break-inside: avoid; border-radius: 3pt; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 9pt; }
thead { display: table-header-group; }
th, td { border: 1px solid #c3c9d2; padding: 3pt 5pt; vertical-align: top; text-align: left;
         overflow-wrap: break-word; hyphens: auto; }
th { background: #eef1f5; }
tr { break-inside: avoid; }
blockquote { margin: 8pt 0; padding: 6pt 10pt; border-left: 3px solid #8a9bb3; background: #f5f7fa; }
blockquote p:last-child { margin-bottom: 0; }
img { max-width: 100%; }
figure { margin: 8pt 0 12pt; text-align: center; break-inside: avoid; }
figure img { max-width: 100%; box-sizing: border-box; border: 1px solid #c3c9d2; }
figcaption { font-size: 9pt; font-style: italic; color: #333; margin-top: 4pt; }
figure.etroite { float: right; margin: 2pt 0 8pt 12pt; }
h2, h3 { clear: both; }
.saut-de-page { break-after: page; height: 0; }
.garde { padding-top: 55mm; }
.garde h1 { font-size: 28pt; }
.garde table { width: auto; margin: 18mm 0 12mm; font-size: 11pt; }
.garde td { border: none; border-bottom: 1px solid #d5dbe3; padding: 4pt 14pt 4pt 0; }
.garde td:first-child { color: #555; }
.sommaire ul { list-style: none; padding-left: 0; }
.sommaire ul ul { padding-left: 16pt; }
.sommaire li { margin-bottom: 3pt; }
.lien-depot { color: inherit; }
"""


def identifiant_github(texte: str, deja: dict[str, int]) -> str:
    """Identifiant d'ancre d'un titre, calculé comme GitHub (doublons suffixés -1, -2...)."""
    base = re.sub(r"[^\w\- ]", "", texte.strip().lower()).replace(" ", "-")
    nombre = deja.get(base, 0)
    deja[base] = nombre + 1
    return base if nombre == 0 else f"{base}-{nombre}"


def mise_a_l_echelle(image: Path) -> tuple[float, bool] | None:
    """Largeur d'impression (mm) d'une capture PNG et si elle se place à droite du texte.

    Une capture large de `LARGEUR_ECRAN_PX` occupe toute la largeur du texte ; une capture
    plus étroite (un panneau seul) garde la même échelle, au lieu d'être agrandie, et se place
    à droite du texte ; une capture trop haute est réduite à `HAUTEUR_FIGURE_MM`. None pour
    une image qui n'est pas un PNG (schéma SVG) : elle prend la largeur du texte.
    """
    with image.open("rb") as fichier:
        entete = fichier.read(24)
    if image.suffix.lower() != ".png" or entete[:8] != SIGNATURE_PNG:
        return None
    largeur_px = int.from_bytes(entete[16:20], "big")
    hauteur_px = int.from_bytes(entete[20:24], "big")
    largeur = LARGEUR_TEXTE_MM * min(1.0, largeur_px / LARGEUR_ECRAN_PX)
    hauteur = largeur * hauteur_px / largeur_px
    if hauteur > HAUTEUR_FIGURE_MM:
        largeur *= HAUTEUR_FIGURE_MM / hauteur
    return largeur, largeur_px < PART_FLOTTANTE * LARGEUR_ECRAN_PX


def texte_brut(inline: Token) -> str:
    """Texte affiché d'un titre (sans le balisage d'emphase ni les accents graves)."""
    return "".join(
        enfant.content for enfant in inline.children or [] if enfant.type in ("text", "code_inline")
    )


def adapter_liens(inline: Token) -> None:
    """Lien vers un autre livret : vers son PDF ; vers un autre fichier du dépôt : texte simple."""
    pile: list[Token] = []
    for enfant in inline.children or []:
        if enfant.type == "link_open":
            cible = str(enfant.attrs.get("href", ""))
            if cible.startswith(("#", "http://", "https://", "mailto:")):
                pile.append(enfant)
                continue
            fichier = cible.split("#", 1)[0]
            if "/" not in fichier and fichier.removesuffix(".md") in LIVRETS:
                enfant.attrs["href"] = fichier.removesuffix(".md") + ".pdf"
            else:
                enfant.tag = "span"
                enfant.attrs = {"class": "lien-depot", "title": cible}
            pile.append(enfant)
        elif enfant.type == "link_close" and pile:
            ouvrant = pile.pop()
            enfant.tag = ouvrant.tag


def figure(correspondance: re.Match[str]) -> str:
    """Une image seule dans son paragraphe, avec sa légende éventuelle : un bloc `<figure>`.

    Une image étroite (classe `etroite`) donne une figure de sa largeur, à droite du texte.
    """
    image, legende = correspondance.group(1), correspondance.group(2)
    attributs = ""
    if 'class="etroite"' in image:
        largeur = re.search(r"width: ([\d.]+mm)", image)
        attributs = ' class="etroite"' + (f' style="width: {largeur.group(1)}"' if largeur else "")
    texte = f"<figcaption>{legende}</figcaption>" if legende else ""
    return f"<figure{attributs}>{image}{texte}</figure>"


def vers_html(markdown: str, titre: str) -> str:
    """Document HTML complet, prêt pour l'impression, d'un livret écrit en Markdown."""
    analyseur = MarkdownIt("commonmark", {"html": False}).enable("table")
    jetons = analyseur.parse(markdown)
    deja: dict[str, int] = {}
    for position, jeton in enumerate(jetons):
        if jeton.type == "heading_open":
            jeton.attrs["id"] = identifiant_github(texte_brut(jetons[position + 1]), deja)
        elif jeton.type == "inline":
            adapter_liens(jeton)
            for enfant in jeton.children or []:
                if enfant.type == "image":
                    source = str(enfant.attrs.get("src", ""))
                    if not source.startswith(("http://", "https://", "file:")):
                        chemin = (DOSSIER / source).resolve()
                        if not chemin.is_file():
                            raise FileNotFoundError(f"Image introuvable : {source} ({titre}).")
                        enfant.attrs["src"] = chemin.as_uri()
                        echelle = mise_a_l_echelle(chemin)
                        if echelle is not None:
                            enfant.attrs["style"] = f"width: {echelle[0]:.1f}mm"
                            if echelle[1]:
                                enfant.attrs["class"] = "etroite"
    corps = analyseur.renderer.render(jetons, analyseur.options, {})

    # Une image seule dans son paragraphe, suivie de sa légende « *Figure N. ...* » : une figure.
    corps = re.sub(
        r"<p>(<img [^>]*>)</p>(?:\s*<p><em>(Figure .*?)</em></p>)?", figure, corps, flags=re.S
    )
    # Tableau de page de garde à en-tête vide (« | | | ») : sans ligne d'en-tête.
    corps = re.sub(r"<thead>\s*<tr>\s*(<th></th>\s*)+</tr>\s*</thead>", "", corps)
    # Page de garde, sommaire, puis un saut de page à chaque « --- ».
    morceaux = re.split(r"<hr\s*/?>", corps)
    if len(morceaux) >= 2:
        morceaux[0] = f'<section class="garde">{morceaux[0]}</section>'
    if len(morceaux) >= 3 and "Table des matières" in morceaux[1]:
        morceaux[1] = f'<section class="sommaire">{morceaux[1]}</section>'
    corps = '<div class="saut-de-page"></div>'.join(morceaux)
    return (
        '<!doctype html>\n<html lang="fr"><head><meta charset="utf-8">'
        f"<title>{html.escape(titre)}</title><style>{STYLE}</style></head>"
        f"<body>{corps}</body></html>\n"
    )


def trouver_chrome(indique: str | None) -> str:
    """Chemin de Chrome ou Chromium ; `FileNotFoundError` avec le remède s'il est absent."""
    for candidat in (indique, os.environ.get("EDT_CHROME")):
        if candidat:
            if Path(candidat).is_file() or shutil.which(candidat):
                return candidat
            raise FileNotFoundError(f"Navigateur introuvable : {candidat}.")
    for chemin in EMPLACEMENTS_CHROME:
        if Path(chemin).is_file():
            return chemin
    for nom in NOMS_CHROME:
        trouve = shutil.which(nom)
        if trouve:
            return trouve
    raise FileNotFoundError(
        "Chrome ou Chromium introuvable : l'installer, ou donner son chemin par --chrome "
        "ou par la variable EDT_CHROME."
    )


def imprimer(chrome: str, page_html: Path, pdf: Path, profil: Path) -> None:
    """Imprime `page_html` en PDF avec Chrome sans interface ; lève `RuntimeError` en cas d'échec."""
    pdf.unlink(missing_ok=True)
    commande = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--allow-file-access-from-files",
        f"--user-data-dir={profil}",
        "--no-pdf-header-footer",
        "--virtual-time-budget=15000",
        f"--print-to-pdf={pdf}",
        page_html.as_uri(),
    ]
    journal.debug("Commande : %s", " ".join(commande))
    try:
        resultat = subprocess.run(
            commande, capture_output=True, text=True, timeout=DELAI_CHROME_S, check=False
        )
    except subprocess.TimeoutExpired as erreur:
        raise RuntimeError(f"Chrome n'a pas répondu en {DELAI_CHROME_S} s.") from erreur
    if not pdf.is_file() or pdf.stat().st_size == 0:
        raise RuntimeError(
            f"Chrome n'a pas produit {pdf.name} (code {resultat.returncode}) : "
            f"{resultat.stderr.strip()[-400:] or 'aucun message'}"
        )


def nombre_de_pages(pdf: Path) -> int | None:
    """Nombre de pages lu dans le PDF (objets « /Type /Page ») ; None s'il est illisible."""
    contenu = pdf.read_bytes()
    pages = len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", contenu))
    return pages or None


def produire(noms: list[str], chrome: str) -> int:
    """Produit les PDF demandés ; renvoie le nombre d'échecs."""
    SORTIE.mkdir(exist_ok=True)
    echecs = 0
    with tempfile.TemporaryDirectory(prefix="livrets-pdf-") as dossier:
        temporaire = Path(dossier)
        for nom in noms:
            source = DOSSIER / f"{nom}.md"
            pdf = SORTIE / f"{nom}.pdf"
            debut = time.monotonic()
            try:
                markdown = source.read_text(encoding="utf-8")
                titre = next(
                    (
                        ligne[2:].strip()
                        for ligne in markdown.splitlines()
                        if ligne.startswith("# ")
                    ),
                    nom,
                )
                page_html = temporaire / f"{nom}.html"
                page_html.write_text(vers_html(markdown, titre), encoding="utf-8")
                imprimer(chrome, page_html, pdf, temporaire / "profil-chrome")
            except (OSError, RuntimeError) as erreur:
                echecs += 1
                journal.error("Échec pour %s : %s", nom, erreur)
                continue
            pages = nombre_de_pages(pdf)
            journal.info(
                "%s : %s pages, %d Ko, en %.1f s",
                pdf.relative_to(DOSSIER.parent.parent),
                pages if pages is not None else "?",
                pdf.stat().st_size // 1024,
                time.monotonic() - debut,
            )
    return echecs


def main(arguments: list[str] | None = None) -> int:
    parametres = argparse.ArgumentParser(description="Produit les PDF des livrets du cours.")
    parametres.add_argument(
        "livrets",
        nargs="*",
        metavar="LIVRET",
        help="nom d'un livret sans extension (défaut : les quatre) : " + ", ".join(LIVRETS),
    )
    parametres.add_argument("--chrome", help="chemin de Chrome ou Chromium")
    parametres.add_argument("--detail", action="store_true", help="journal détaillé")
    options = parametres.parse_args(arguments)
    logging.basicConfig(
        level=logging.DEBUG if options.detail else logging.INFO, format="%(levelname)s %(message)s"
    )
    inconnus = [nom for nom in options.livrets if nom not in LIVRETS]
    if inconnus:
        journal.error(
            "Livret inconnu : %s. Choisir parmi : %s.", ", ".join(inconnus), ", ".join(LIVRETS)
        )
        return 1
    try:
        chrome = trouver_chrome(options.chrome)
    except FileNotFoundError as erreur:
        journal.error("%s", erreur)
        return 1
    journal.info("Navigateur : %s", chrome)
    echecs = produire(list(options.livrets) or list(LIVRETS), chrome)
    if echecs:
        journal.error("%d livret(s) non produit(s).", echecs)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
