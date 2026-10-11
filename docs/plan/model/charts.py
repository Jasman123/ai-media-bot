import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter

import bizmodel as bm

# usage: python3 charts.py [output_dir]   (default: the folder above this file, i.e. docs/plan)
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..")
os.makedirs(OUT, exist_ok=True)

# palette (reference palette, light mode; slots 1-5 validated with scripts/validate_palette.js)
SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
BLUE, ORANGE, AQUA, YELLOW, MAGENTA = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "font.size": 10, "axes.titlesize": 11, "text.parse_math": False,
})

usd = FuncFormatter(lambda v, _: ("-" if v < 0 else "") + f"${abs(v):,.0f}")


def style(ax, grid_axis="y"):
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.spines["bottom"].set_linewidth(1)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(length=0, labelsize=9)


def title(fig, text, sub):
    fig.text(0.012, 0.975, text, fontsize=14, fontweight="bold", color=INK, va="top")
    fig.text(0.012, 0.925, sub, fontsize=10, color=INK2, va="top")


def dot(ax, x, y, color):
    ax.plot([x], [y], marker="o", markersize=10, color=color, markeredgecolor=SURFACE,
            markeredgewidth=2, zorder=5, linestyle="none")


def save(fig, name):
    p = os.path.join(OUT, name)
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print("wrote", p)


# ------------------------------------------------------------------ chart 1: where the launch budget goes
b = bm.initial_breakdown(0)
total = sum(b.values())
items = sorted(b.items(), key=lambda kv: kv[1], reverse=True)
fig, ax = plt.subplots(figsize=(10, 4.6))
fig.subplots_adjust(top=0.80, left=0.24, right=0.97, bottom=0.10)
ys = list(range(len(items)))[::-1]
for y, (k, v) in zip(ys, items):
    ax.barh(y, v, height=0.5, color=BLUE)
    ax.text(v + total * 0.012, y,
            f"${v:,.0f}  ·  Rp{bm.rp_m(v):.1f}M  ·  {v / total:.0%}",
            va="center", fontsize=9.5, color=INK)
ax.set_yticks(ys)
ax.set_yticklabels([k for k, _ in items], fontsize=10, color=INK)
ax.set_xlim(0, max(b.values()) * 1.55)
ax.xaxis.set_major_formatter(usd)
style(ax, "x")
ax.spines["left"].set_visible(True); ax.spines["left"].set_color(AXIS)
ax.spines["bottom"].set_visible(False)
ax.tick_params(axis="x", labelsize=9)
title(fig, f"Where the ≈${total:,.0f} (Rp{bm.rp_m(total):.1f}M) launch budget goes",
      f"Likely case. Range ${bm.initial_total(1):,.0f}–{bm.initial_total(2):,.0f} "
      f"(Rp{bm.rp_m(bm.initial_total(1)):.0f}–{bm.rp_m(bm.initial_total(2)):.0f}M). Excludes your own time.")
save(fig, "01-launch-budget.png")

# ------------------------------------------------------------------ chart 2: break-even (cost-volume-profit)
fig, axes = plt.subplots(1, 3, figsize=(15, 5.2))
fig.subplots_adjust(top=0.78, left=0.055, right=0.985, bottom=0.2, wspace=0.22)
F = bm.FIXED_MONTHLY
xmax = {"worst": 6000, "avg": 6000, "best": 600}
for ax, (key, s) in zip(axes, bm.SCEN.items()):
    u = bm.unit_econ(s)
    xs = [xmax[key] * i / 200 for i in range(201)]
    rev = [n * u["rev_per_user"] for n in xs]
    var = [r * (1 - u["cm"]) + s["free"] * n for r, n in zip(rev, xs)]
    tot_cost = [F + v for v in var]
    ax.fill_between(xs, [F] * len(xs), tot_cost, color=AQUA, alpha=0.16, linewidth=0)
    ax.plot(xs, [F] * len(xs), color=ORANGE, linewidth=2, solid_capstyle="round")
    ax.plot(xs, tot_cost, color=INK2, linewidth=2, solid_capstyle="round")
    ax.plot(xs, rev, color=BLUE, linewidth=2, solid_capstyle="round")
    ax.set_xlim(0, xmax[key])
    ax.set_ylim(0, max(max(rev), max(tot_cost)) * 1.08)
    ax.yaxis.set_major_formatter(usd)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_xlabel("New users per month (steady state)", fontsize=9)
    style(ax)
    ax.set_title(s["label"], loc="left", fontsize=11, fontweight="bold", color=INK, pad=22)
    ax.text(0, 1.02,
            f"Net revenue per new user ${u['rev_per_user']:.3f}/mo · variable cost "
            f"${u['rev_per_user'] * (1 - u['cm']) + s['free']:.3f}/mo",
            transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom")
    if u["users_bep"]:
        n = u["users_bep"]
        y = F + (n * u["rev_per_user"] * (1 - u["cm"]) + s["free"] * n)
        dot(ax, n, y, INK)
        ax.annotate(f"Break-even ≈ {n:,.0f} new users/mo\n≈ {u['payers_bep']:,.0f} active payers",
                    xy=(n, y), xytext=(xmax[key] * 0.04, max(max(rev), max(tot_cost)) * 0.72),
                    fontsize=9.5, color=INK, arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=1))
    else:
        ax.text(xmax[key] * 0.03, max(tot_cost) * 0.88, "Never breaks even:\neach new user costs more\nthan they bring in",
                fontsize=9.5, color=INK)
    if key == "worst":
        ax.text(xmax[key] * 0.985, rev[-1] - 6, "Revenue ", color=INK2, fontsize=8.5, ha="right", va="top")
        ax.text(xmax[key] * 0.985, F + 6, "Fixed ", color=INK2, fontsize=8.5, ha="right", va="bottom")
    else:
        ax.text(xmax[key] * 0.985, rev[-1], "Revenue ", color=INK2, fontsize=8.5, ha="right", va="bottom")
        ax.text(xmax[key] * 0.985, F + 6, "Fixed ", color=INK2, fontsize=8.5, ha="right", va="bottom")
handles = [Line2D([0], [0], color=BLUE, lw=2, label="Net revenue"),
           Line2D([0], [0], color=INK2, lw=2, label="Total cost (fixed + variable)"),
           Line2D([0], [0], color=ORANGE, lw=2, label=f"Fixed cost (${F:,.0f}/month)"),
           Patch(facecolor=AQUA, alpha=0.3, label="Variable cost: provider, failed jobs, LLM, cash-out, free tier")]
fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.0),
           labelcolor=INK2)
title(fig, "Break-even: monthly revenue vs fixed and variable cost",
      "Steady state, $ per month. Where the blue line crosses the grey line you stop losing money. "
      "Note the x-axis differs per panel (best case: 0–600).")
save(fig, "02-breakeven-cvp.png")

# ------------------------------------------------------------------ chart 3: monthly revenue vs expenses (24 months)
fig, axes = plt.subplots(1, 3, figsize=(15, 5.4))
fig.subplots_adjust(top=0.78, left=0.055, right=0.985, bottom=0.2, wspace=0.22)
for ax, (key, s) in zip(axes, bm.SCEN.items()):
    rows, sm = bm.run(s, "bootstrap", months=24)
    ms = [r["m"] for r in rows]
    layers = [("fixed", ORANGE), ("variable", AQUA), ("free", YELLOW), ("mkt", MAGENTA)]
    bottom = [0.0] * len(rows)
    for name, col in layers:
        vals = [r[name] for r in rows]
        ax.bar(ms, vals, bottom=bottom, width=0.72, color=col, edgecolor=SURFACE, linewidth=1.2)
        bottom = [b0 + v for b0, v in zip(bottom, vals)]
    rev = [r["R"] for r in rows]
    ax.plot(ms, rev, color=BLUE, linewidth=2, solid_capstyle="round", zorder=4)
    ax.set_xlim(0.2, 24.8)
    ax.set_ylim(0, max(max(bottom), max(rev)) * 1.12)
    ax.set_xticks([1, 3, 6, 9, 12, 18, 24])
    ax.set_xlabel("Month after launch", fontsize=9)
    ax.yaxis.set_major_formatter(usd)
    style(ax)
    ax.set_title(s["label"], loc="left", fontsize=11, fontweight="bold", color=INK, pad=22)
    r12 = rows[11]
    exp12 = r12["fixed"] + r12["variable"] + r12["free"] + r12["mkt"]
    ax.text(0, 1.02, f"Month 12: revenue ${r12['R']:,.0f} · expenses ${exp12:,.0f} · profit "
                     f"{'-' if r12['profit'] < 0 else '+'}${abs(r12['profit']):,.0f}",
            transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom")
    if sm["bep"]:
        m = sm["bep"]
        dot(ax, m, rows[m - 1]["R"], BLUE)
        ax.annotate(f"Profitable from month {m}:\nrevenue is above all expenses", xy=(m, rows[m - 1]["R"]),
                    xytext=(1.2, max(max(bottom), max(rev)) * 0.93), fontsize=9.5, color=INK,
                    arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=1))
    else:
        note = "Revenue stays below expenses"
        if key == "worst":
            note += "\n(blue line along the bottom: about $1/month)"
        ax.text(12.5, max(bottom) * 0.62, note, fontsize=9.5, color=INK, ha="center")
handles = [Patch(facecolor=ORANGE, label="Fixed (server, bookkeeping, domain)"),
           Patch(facecolor=AQUA, label="Variable (provider, failed jobs, LLM, cash-out)"),
           Patch(facecolor=YELLOW, label="Free-tier credits"),
           Patch(facecolor=MAGENTA, label="Launch marketing ($300, month 1)"),
           Line2D([0], [0], color=BLUE, lw=2, label="Net revenue")]
fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, fontsize=9, bbox_to_anchor=(0.5, 0.0),
           labelcolor=INK2)
title(fig, "Month by month: revenue vs expenses (24 months)",
      "Baseline plan: $300 launch seeding, then no paid ads. Each panel has its own y-axis. "
      "Where the blue line rises above the bars you are in surplus.")
save(fig, "03-monthly-revenue-vs-expenses.png")

# ------------------------------------------------------------------ chart 4: cumulative cash (J-curve)
fig, ax = plt.subplots(figsize=(11, 5.6))
fig.subplots_adjust(top=0.82, left=0.08, right=0.80, bottom=0.14)
start = -bm.initial_cash_before_launch_marketing(0)
series = {}
for key in ("worst", "avg", "best"):
    rows, sm = bm.run(bm.SCEN[key], "bootstrap", months=24)
    series[key] = ([0] + [r["m"] for r in rows], [start] + [r["cum"] for r in rows], sm, rows)
styles = {"best": (BLUE, 2.4), "avg": (INK2, 2), "worst": (MUTED, 2)}
names = {"best": "Best", "avg": "Average", "worst": "Worst"}
for key in ("worst", "avg", "best"):
    xs, ys, sm, rows = series[key]
    col, lw = styles[key]
    ax.plot(xs, ys, color=col, linewidth=lw, solid_capstyle="round", zorder=3)
    v = ys[-1]
    off = {"best": 0, "avg": 1500, "worst": -1500}[key]
    ax.annotate(f"{names[key]}  {'-' if v < 0 else '+'}${abs(v) / 1000:.1f}k  "
                f"({'-' if v < 0 else '+'}Rp{abs(bm.rp_m(v)):.0f}M)",
                xy=(24, v), xytext=(24.5, v + off), fontsize=9.5, color=INK, va="center",
                arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=1, shrinkA=0, shrinkB=0) if off else None)
ax.axhline(0, color=MUTED, linewidth=1, zorder=2)
xs, ys, sm, rows = series["best"]
pb = sm["payback"]
dot(ax, pb, ys[pb], BLUE)
ax.annotate(f"Best case pays back the\ninvestment in month {pb}", xy=(pb, ys[pb]), xytext=(pb - 8.2, 5200),
            fontsize=9.5, color=INK, arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=1))
xs, ys, sm, rows = series["worst"]
dot(ax, 3, ys[3], MUTED)
ax.annotate(f"Stop rule: if gates fail after month 3,\ntotal loss ≈ ${abs(ys[3]):,.0f} (Rp{abs(bm.rp_m(ys[3])):.0f}M)",
            xy=(3, ys[3]), xytext=(4.5, -6200), fontsize=9.5, color=INK,
            arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=1))
dot(ax, 0, start, INK)
ax.annotate(f"Start: ${abs(start):,.0f} spent\nbefore launch", xy=(0, start), xytext=(0.6, -900),
            fontsize=9, color=INK2)
ax.set_xlim(0, 24)
ax.set_ylim(-7200, 14500)
ax.set_xticks([0, 3, 6, 9, 12, 15, 18, 21, 24])
ax.set_xlabel("Month after launch", fontsize=9)
ax.set_ylabel("Cumulative cash, USD", fontsize=9)
ax.yaxis.set_major_formatter(usd)
style(ax)
handles = [Line2D([0], [0], color=BLUE, lw=2.4, label="Best: strong traction"),
           Line2D([0], [0], color=INK2, lw=2, label="Average: modest traction"),
           Line2D([0], [0], color=MUTED, lw=2, label="Worst: no traction")]
ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=9, labelcolor=INK2)
title(fig, "Cumulative cash: when you get your money back",
      "Baseline plan (launch seeding only, no paid ads). Stars income arrives about a month late (21-day hold).")
save(fig, "04-cumulative-cash.png")
