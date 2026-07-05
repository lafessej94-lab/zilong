"""
Pré-stylage des sous-titres avant envoi à FreeConvert.

FreeConvert ne propose pas d'option "force_style" comme ffmpeg (contrairement
à CloudConvert où on peut injecter n'importe quelle commande ffmpeg). Leur API
se contente de brûler le fichier .ass/.srt tel quel, avec le style déjà écrit
dedans.

Solution : on réécrit nous-mêmes le bloc [V4+ Styles] du fichier .ass avant
de l'envoyer — FreeConvert applique alors CE style au moment du burn.

Si le fichier source est un .srt (pas de style), on le convertit d'abord en
.ass via ffmpeg pour obtenir un header standard, qu'on écrase ensuite.
"""
import subprocess
from dataclasses import dataclass, replace
from os import path as ospath


@dataclass(frozen=True)
class AssStyle:
    fontname: str = "Trebuchet MS"
    fontsize: int = 23
    primary_colour: str = "&H00FFFFFF"   # blanc pur (format ASS: &HAABBGGRR)
    secondary_colour: str = "&H000000FF"
    outline_colour: str = "&H00000000"   # contour noir
    back_colour: str = "&H00000000"      # pas d'ombre (Shadow=0 ci-dessous)
    bold: int = -1                       # -1 = gras activé en ASS (0 = désactivé)
    italic: int = 0
    border_style: int = 1                # 1 = contour + ombre, 3 = boîte pleine
    outline: float = 2.5
    shadow: float = 0
    alignment: int = 2                   # 2 = bas centré (numpad ASS)
    margin_l: int = 20
    margin_r: int = 20
    margin_v: int = 20


# Style par défaut : repris tel quel du style "Default" fourni par l'utilisateur
DEFAULT_HARDSUB_STYLE = AssStyle()

# Résolution de référence du script — DOIT matcher celle du fichier source
# (640x360), sinon la taille de police ne sera pas à l'échelle correcte une
# fois le style appliqué à une vraie vidéo 1080p (ASS scale le rendu selon
# le ratio actual_resolution / PlayRes).
PLAY_RES_X = 640
PLAY_RES_Y = 360


def _ass_style_line(style: AssStyle, name: str = "Default") -> str:
    """Construit la ligne 'Style:' au format ASS v4+."""
    # Certains moteurs de burn-in "simplifiés" (dont FreeConvert) ignorent le
    # flag Bold du style et se contentent de chercher la police par son nom
    # exact. On ajoute donc "Bold" au nom de la police en plus du flag —
    # double sécurité qui ne casse rien pour les moteurs qui respectent le
    # flag normalement (testé/confirmé : forcer le nom donne le même rendu
    # gras qu'un vrai Bold=-1, indépendamment du flag).
    fontname = f"{style.fontname} Bold" if style.bold else style.fontname
    fields = [
        name, fontname, str(style.fontsize),
        style.primary_colour, style.secondary_colour,
        style.outline_colour, style.back_colour,
        str(style.bold), str(style.italic),
        "0", "0",              # Underline, StrikeOut
        "100", "100",          # ScaleX, ScaleY
        "0", "0",               # Spacing, Angle
        str(style.border_style), str(style.outline), str(style.shadow),
        str(style.alignment),
        str(style.margin_l), str(style.margin_r), str(style.margin_v),
        "1",                    # Encoding
    ]
    return "Style: " + ",".join(fields)


_STYLE_FORMAT_HEADER = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding"
)


def _srt_to_ass(srt_path: str, ass_path: str) -> None:
    """Convertit un .srt en .ass basique via ffmpeg (header par défaut, sera écrasé après)."""
    cmd = ["ffmpeg", "-y", "-i", srt_path, ass_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if result.returncode != 0 or not ospath.exists(ass_path):
        raise RuntimeError(f"Échec conversion srt->ass: {result.stderr.decode(errors='ignore')[:300]}")


def apply_hardsub_style(
    subtitle_path: str,
    output_path: str,
    style: AssStyle = DEFAULT_HARDSUB_STYLE,
) -> str:
    """
    Force le style de rendu d'un sous-titre (.srt ou .ass) et écrit le résultat
    en .ass prêt à être envoyé à FreeConvert pour le hardsub.

    Tous les styles nommés trouvés dans le fichier source (Default, Italique,
    Sign, etc.) reçoivent le MÊME style uniforme (celui passé en paramètre) —
    ça évite qu'une ligne de dialogue référencant un style autre que "Default"
    (ex: un style "Italique" du fichier d'origine) ne tombe sur un style
    manquant ou conserve un rendu non désiré (italique, police différente...).

    Retourne le chemin du fichier .ass stylé (= output_path).
    """
    ext = ospath.splitext(subtitle_path)[1].lower()
    work_path = subtitle_path

    if ext == ".srt":
        tmp_ass = output_path + ".tmp.ass"
        _srt_to_ass(subtitle_path, tmp_ass)
        work_path = tmp_ass
    elif ext not in (".ass", ".ssa"):
        raise ValueError(f"Format de sous-titre non supporté: {ext}")

    with open(work_path, "r", encoding="utf-8-sig", errors="replace") as fh:
        lines = fh.readlines()

    # 1er passage : on récupère les noms de tous les styles définis dans le
    # fichier source, pour pouvoir leur appliquer à tous notre style uniforme.
    style_names: list[str] = []
    in_styles_scan = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() in ("[v4+ styles]", "[v4 styles]"):
            in_styles_scan = True
            continue
        if in_styles_scan:
            if stripped.startswith("["):
                in_styles_scan = False
                continue
            if stripped.lower().startswith("style:"):
                name = stripped.split(":", 1)[1].split(",", 1)[0].strip()
                if name and name not in style_names:
                    style_names.append(name)
    if not style_names:
        style_names = ["Default"]

    # 2e passage : on reconstruit le fichier en remplaçant tout le bloc de
    # styles par une ligne "Style:" par nom trouvé, toutes identiques (notre
    # style forcé).
    out_lines: list[str] = []
    in_styles_section = False
    styles_written = False

    for line in lines:
        stripped = line.strip()

        if stripped.lower() in ("[v4+ styles]", "[v4 styles]"):
            in_styles_section = True
            out_lines.append("[V4+ Styles]\n")
            out_lines.append(_STYLE_FORMAT_HEADER + "\n")
            for name in style_names:
                out_lines.append(_ass_style_line(style, name=name) + "\n")
            styles_written = True
            continue

        if in_styles_section:
            # On saute tout l'ancien bloc de styles (Format: + toutes les Style:)
            if stripped.startswith("[") and stripped.lower() not in ("[v4+ styles]", "[v4 styles]"):
                in_styles_section = False
                out_lines.append(line)
            # sinon on ignore la ligne (ancien Format:/Style:)
            continue

        out_lines.append(line)

    if not styles_written:
        # Pas de section styles trouvée (rare) -> on l'ajoute avant [Events]
        final_lines: list[str] = []
        inserted = False
        for line in out_lines:
            if line.strip().lower() == "[events]" and not inserted:
                final_lines.append("[V4+ Styles]\n")
                final_lines.append(_STYLE_FORMAT_HEADER + "\n")
                for name in style_names:
                    final_lines.append(_ass_style_line(style, name=name) + "\n")
                final_lines.append("\n")
                inserted = True
            final_lines.append(line)
        out_lines = final_lines

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.writelines(out_lines)

    if work_path != subtitle_path and ospath.exists(work_path):
        import os
        os.remove(work_path)

    return output_path
