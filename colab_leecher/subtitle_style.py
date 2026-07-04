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
    fontname: str = "Trebuchet MS"       # police claire, lisible, pas trop "carrée"
    fontsize: int = 20
    primary_colour: str = "&H00FFFFFF"   # blanc pur (format ASS: &HAABBGGRR)
    secondary_colour: str = "&H000000FF"
    outline_colour: str = "&H00000000"   # contour noir
    back_colour: str = "&H80000000"      # ombre semi-transparente (alpha 80 = ~50%)
    bold: int = 0
    italic: int = 0
    border_style: int = 1                # 1 = contour + ombre, 3 = boîte pleine
    outline: float = 1.8                 # contour fin mais visible
    shadow: float = 0.8                  # légère ombre portée, pas agressive
    alignment: int = 2                   # 2 = bas centré (numpad ASS)
    margin_l: int = 20
    margin_r: int = 20
    margin_v: int = 24


# Style par défaut : rendu "anime hardsub classique" avec ombre bien présente
DEFAULT_HARDSUB_STYLE = AssStyle()

# Résolution de référence du script — les valeurs ci-dessus (fontsize, outline,
# shadow, margins) sont calibrées pour du 1920x1080. ASS scale automatiquement
# le rendu à partir de PlayResX/PlayResY, donc on les force nous-mêmes plutôt
# que de garder ceux (parfois différents) du fichier .ass d'origine.
PLAY_RES_X = 1920
PLAY_RES_Y = 1080


def _ass_style_line(style: AssStyle, name: str = "Default") -> str:
    """Construit la ligne 'Style:' au format ASS v4+."""
    fields = [
        name, style.fontname, str(style.fontsize),
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

    out_lines: list[str] = []
    in_styles_section = False
    styles_written = False

    for line in lines:
        stripped = line.strip()

        if stripped.lower() == "[v4+ styles]" or stripped.lower() == "[v4 styles]":
            in_styles_section = True
            out_lines.append("[V4+ Styles]\n")
            out_lines.append(_STYLE_FORMAT_HEADER + "\n")
            out_lines.append(_ass_style_line(style) + "\n")
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
                final_lines.append(_ass_style_line(style) + "\n\n")
                inserted = True
            final_lines.append(line)
        out_lines = final_lines

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.writelines(out_lines)

    if work_path != subtitle_path and ospath.exists(work_path):
        import os
        os.remove(work_path)

    return output_path
