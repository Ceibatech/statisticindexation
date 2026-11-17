"""Graphiques PNG intégrés au mail (cid:). Une seule échelle par graphique, une seule série colorée,
la référence en encre secondaire (même charte que numerisation-stats)."""
from __future__ import annotations

import io
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parent.parent / "data" / ".matplotlib"))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

SERIE = "#2a78d6"
SERIE_PALE = "#9fc3ee"
REF = "#52514e"
TXT, TXT2, GRILLE, FOND = "#0b0b0b", "#52514e", "#e6e5e0", "#ffffff"

for f in ("Inter", "DejaVu Sans"):
    if any(f == x.name for x in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = f
        break
plt.rcParams.update({"font.size": 10, "axes.edgecolor": GRILLE, "axes.labelcolor": TXT2, "xtick.color": TXT2,
                     "ytick.color": TXT2, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.spines.left": False, "figure.facecolor": FOND, "axes.facecolor": FOND})


def _fig(h=2.6):
    fig, ax = plt.subplots(figsize=(6.4, h), dpi=200)
    ax.grid(axis="y", color=GRILLE, linewidth=.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    return fig, ax


def _png(fig) -> bytes:
    b = io.BytesIO()
    fig.tight_layout()
    fig.savefig(b, format="png", facecolor=FOND)
    plt.close(fig)
    return b.getvalue()


def _sep(n):
    return f"{int(round(n)):,}".replace(",", " ")


def _legende(ax):
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=9, labelcolor=TXT2,
              handlelength=1.6, borderaxespad=0)


def horaire(heures, auj, ref, coupure_h: int, lib_ref: str) -> bytes:
    """Validations par heure, journée en cours vs moyenne de référence à la même heure."""
    fig, ax = _fig()
    x = list(range(len(heures)))
    vis = [v if h <= coupure_h else 0 for h, v in zip(heures, auj)]
    ax.bar(x, vis, width=.62, color=SERIE, label="Aujourd'hui", zorder=2)
    if any(ref):
        ax.plot(x, ref, color=REF, linewidth=2, marker="o", markersize=4, label=lib_ref, zorder=3)
    ax.set_xticks(x, [f"{h}h" for h in heures])
    ax.set_ylabel("Pièces indexées / heure")
    ax.yaxis.set_major_formatter(lambda v, _: _sep(v))
    ax.set_ylim(0, max(max(vis or [0]), max(ref or [0]), 1) * 1.15)
    _legende(ax)
    return _png(fig)


def quotidien(jours, valeurs, objectif, ylabel="Pièces indexées / jour") -> bytes:
    fig, ax = _fig()
    x = list(range(len(jours)))
    ax.bar(x, valeurs, width=.62, color=[SERIE_PALE] * (len(x) - 1) + [SERIE], zorder=2)
    haut = max(max(valeurs or [0]), objectif or 0, 1)
    ax.set_ylim(0, haut * 1.15)
    if objectif:
        ax.axhline(objectif, color=REF, linewidth=1.5, linestyle=(0, (4, 3)), zorder=3)
        ax.text(-.4, objectif, f"Objectif {_sep(objectif)}", color=TXT2, fontsize=8.5, ha="left", va="bottom")
    ax.set_xticks(x, [j.strftime("%d/%m") for j in jours], rotation=0 if len(x) <= 12 else 90, fontsize=8.5)
    if x:
        ax.text(x[-1], valeurs[-1], _sep(valeurs[-1]), ha="center", va="bottom", fontsize=9, color=TXT, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.yaxis.set_major_formatter(lambda v, _: _sep(v))
    return _png(fig)


def stock(jours, valeurs) -> bytes:
    """Pièces à indexer en fin de journée (une seule échelle : pièces)."""
    fig, ax = _fig(2.3)
    x = list(range(len(jours)))
    ax.fill_between(x, 0, valeurs, color=SERIE, alpha=.12, linewidth=0, zorder=1)
    ax.plot(x, valeurs, color=SERIE, linewidth=2, zorder=3)
    ax.set_ylim(0, max(max(valeurs or [0]), 1) * 1.2)
    if x:
        ax.text(x[-1], valeurs[-1], f" {_sep(valeurs[-1])}", ha="left", va="center", fontsize=9, color=TXT, fontweight="bold")
    pas = max(1, len(x) // 10)
    ax.set_xticks(x[::pas], [j.strftime("%d/%m") for j in jours[::pas]], fontsize=8.5)
    ax.set_xlim(-.5, len(x) - .5 + max(1.5, len(x) * .06))
    ax.set_ylabel("Pièces à indexer (soir)")
    ax.yaxis.set_major_formatter(lambda v, _: _sep(v))
    return _png(fig)


def distribution_temps(temps_s, mediane, cible) -> bytes:
    """Histogramme des temps d'indexation mesurés (minutes), médiane et cible repérées."""
    fig, ax = _fig(2.4)
    t = [v / 60 for v in temps_s]
    borne = max(1.0, sorted(t)[int(len(t) * .98)] if len(t) > 10 else max(t or [1]))
    bins = [i * borne / 24 for i in range(25)]
    hauts, _, _ = ax.hist([min(v, borne) for v in t], bins=bins, color=SERIE, rwidth=.86, zorder=2)
    ax.set_ylim(0, max(hauts) * 1.3 if len(hauts) else 1)
    if mediane:
        ax.axvline(mediane / 60, color=TXT, linewidth=1.6, zorder=3)
        ax.text(mediane / 60, ax.get_ylim()[1] * .99, f" médiane {mediane / 60:.1f} min", fontsize=8.5, color=TXT, va="top")
    if cible:
        ax.axvline(cible / 60, color=REF, linewidth=1.4, linestyle=(0, (4, 3)), zorder=3)
        cote = "right" if mediane and cible < mediane else "left"
        ax.text(cible / 60, ax.get_ylim()[1] * .86, (f"cible {cible / 60:.1f} min " if cote == "right" else f" cible {cible / 60:.1f} min"),
                fontsize=8.5, color=TXT2, va="top", ha=cote)
    ax.set_xlabel("Minutes par pièce (dernière classe : au-delà)")
    ax.set_ylabel("Pièces")
    return _png(fig)
