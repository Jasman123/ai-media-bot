"""Bottom-up business-case model for ai-media-bot (all inputs are assumptions; see plan.md section 11)."""

NET_PER_STAR = 0.013      # USD per Star earned (Bot Developer ToS 6.2.4)
IDR = 17917               # IDR per USD, JISDOR 6 Oct 2026 (news portal)

# ---------------------------------------------------------------- one-time costs (USD): (likely, low, high)
LEGAL = {
    "ToS + privacy + refund policy in Bahasa (lawyer draft/review)": (700, 350, 1200),
    "PDP / PSE registration check and advice":                        (200, 100, 400),
    "Accountant consult (Stars/Gram income, tax regime)":             (100, 50, 200),
}
INFRA_PRE = {   # 2 months of build + beta, before revenue
    "VPS, 2 months (DigitalOcean 2 vCPU/4 GB at $24)":  (48.0, 15.5, 96.0),
    "Server backups, 2 months (20% of server)":          (9.6, 0.0, 19.2),
    "Domain, 1 year":                                    (12.0, 10.0, 20.0),
    "Monitoring / storage / error tracking (free tiers)": (0.0, 0.0, 52.0),
}
PROVIDER_PRE = {
    "Dev and QA generations (~300 images, ~25 clips)":   (15.0, 10.0, 40.0),
    "Sample outputs for marketing templates":            (10.0, 5.0, 25.0),
    "Prepaid balance for beta + first months":           (175.0, 85.0, 335.0),
}
TOOLING = {"AI coding assistant, ~2 months build (your actual plan price)": (200.0, 0.0, 400.0)}
TESTS = {"Purchase tests R8/R9 (spread cost)": (10.0, 10.0, 30.0)}
MARKETING_LAUNCH = {
    "5 nano creators, small cash (about Rp1.5M)": 84.0,
    "Short-video and design tools, 2 months":     30.0,
    "Giveaway and workshop prizes":               50.0,
    "Small paid test (Meta / Telegram boost)":    100.0,
    "Reserve":                                    36.0,
}
MARKETING_LAUNCH_RANGE = (300.0, 150.0, 500.0)
CONTINGENCY = 0.15
PROVIDER_FLOAT = PROVIDER_PRE["Prepaid balance for beta + first months"][0]


def tot(d, i):
    return sum(v[i] for v in d.values())


def initial_breakdown(i):
    """i: 0 likely, 1 low, 2 high. Returns dict of category -> USD."""
    legal = tot(LEGAL, i); infra = tot(INFRA_PRE, i); prov = tot(PROVIDER_PRE, i)
    tool = tot(TOOLING, i); tests = tot(TESTS, i); mkt = MARKETING_LAUNCH_RANGE[i]
    prov_sunk = PROVIDER_PRE["Dev and QA generations (~300 images, ~25 clips)"][i] + \
        PROVIDER_PRE["Sample outputs for marketing templates"][i]
    cont = CONTINGENCY * (legal + infra + prov_sunk + tool + tests)
    return {"Legal and compliance": legal, "Launch marketing": mkt, "Provider API (prepaid)": prov,
            "AI coding tools": tool, "Contingency (15%)": cont, "Infrastructure (2 months)": infra,
            "Test purchases": tests}


def initial_total(i):
    return sum(initial_breakdown(i).values())


def initial_cash_before_launch_marketing(i):
    b = initial_breakdown(i)
    return sum(b.values()) - b["Launch marketing"]


# ---------------------------------------------------------------- recurring fixed costs (USD / month)
FIXED_LINES = {   # (likely, low, high)
    "Server (DigitalOcean 2 vCPU/4 GB; Postgres + Redis on the same box)": (24.0, 7.7, 24.0),
    "Server backups (20%)":                                                (4.8, 0.0, 4.8),
    "Storage, error tracking, uptime (R2 + Sentry free tier)":             (1.0, 0.0, 29.0),
    "Domain (amortised)":                                                  (1.0, 0.8, 1.7),
    "Bookkeeping (Rp750k; range Rp500k-1.5M)":                             (41.9, 27.9, 83.7),
}
FIXED_HIGH_EXTRA = 15.0   # e.g. a managed database in the high case
FIXED_MONTHLY = sum(v[0] for v in FIXED_LINES.values())
FIXED_LOW = sum(v[1] for v in FIXED_LINES.values())
FIXED_HIGH = sum(v[2] for v in FIXED_LINES.values()) + FIXED_HIGH_EXTRA

SCEN = {
    "worst": dict(label="Worst: no traction", org0=50, g=0.00, cac=1.20, conv=0.008, arppu=80, r=0.30,
                  p=0.50, leak=0.08, llm=0.03, fric=0.04, free=0.05),
    "avg":   dict(label="Average: modest traction", org0=400, g=0.06, cac=0.60, conv=0.020, arppu=200, r=0.50,
                  p=0.385, leak=0.05, llm=0.02, fric=0.03, free=0.03),
    "best":  dict(label="Best: strong traction", org0=1000, g=0.10, cac=0.40, conv=0.040, arppu=400, r=0.65,
                  p=0.33, leak=0.03, llm=0.02, fric=0.02, free=0.03),
}
PLANS = {"bootstrap": (300, 0), "lean": (300, 150), "standard": (800, 400), "aggressive": (2000, 1000)}


def cm_of(s):
    return 1 - s["p"] - s["leak"] - s["llm"] - s["fric"]


def run(s, plan="bootstrap", months=36, fixed=None, init_i=0):
    fixed = FIXED_MONTHLY if fixed is None else fixed
    launch, monthly = PLANS[plan]
    cm = cm_of(s)
    float_left = PROVIDER_FLOAT
    cum = -initial_cash_before_launch_marketing(init_i)
    org = s["org0"]; active_prev = 0.0; R_prev = 0.0
    rows = []; peak = -cum; bep = None; payback = None
    for m in range(1, months + 1):
        mkt = monthly + (launch if m == 1 else 0)
        if m > 1:
            org *= 1 + (s["g"] if m <= 12 else s["g"] / 3)
        paid = mkt / s["cac"]
        users = org + paid
        newp = s["conv"] * users
        active = newp + s["r"] * active_prev
        R = active * s["arppu"] * NET_PER_STAR
        var_cost = (s["p"] + s["leak"] + s["llm"] + s["fric"]) * R
        free_cost = s["free"] * users
        profit = R - fixed - var_cost - free_cost - mkt
        # cash: receipts lag one month (21-day hold); cash-out friction taken on receipts
        receipts = R_prev * (1 - s["fric"])
        prov_costs = (s["p"] + s["leak"]) * R + free_cost
        from_float = min(float_left, prov_costs); float_left -= from_float
        cash_out = (prov_costs - from_float) + s["llm"] * R + fixed + mkt
        cf = receipts - cash_out
        cum += cf; peak = max(peak, -cum)
        if bep is None and profit >= 0: bep = m
        if payback is None and cum >= 0: payback = m
        rows.append(dict(m=m, users=users, payers=active, R=R, fixed=fixed,
                         variable=var_cost, free=free_cost, mkt=mkt, profit=profit, cf=cf, cum=cum))
        active_prev, R_prev = active, R
    return rows, dict(cm=cm, bep=bep, payback=payback, peak=peak)


def unit_econ(s, fixed=None):
    fixed = FIXED_MONTHLY if fixed is None else fixed
    cm = cm_of(s)
    arppu_usd = s["arppu"] * NET_PER_STAR
    life = 1 / (1 - s["r"])
    contrib_pm = arppu_usd * cm
    ltv_payer = contrib_pm * life
    per_new = ltv_payer * s["conv"] - s["free"]               # value of one new user = max affordable CAC
    payers_per_user = s["conv"] / (1 - s["r"])                # steady-state active payers per monthly new user
    rev_per_user = payers_per_user * arppu_usd                # net revenue per new user per month
    users_bep = fixed / per_new if per_new > 0 else None
    users_500 = (fixed + 500) / per_new if per_new > 0 else None
    return dict(cm=cm, arppu_usd=arppu_usd, life=life, contrib_pm=contrib_pm, ltv_payer=ltv_payer,
                per_new=per_new, payers_per_user=payers_per_user, rev_per_user=rev_per_user,
                users_bep=users_bep, payers_bep=(users_bep * payers_per_user if users_bep else None),
                users_500=users_500, payers_500=(users_500 * payers_per_user if users_500 else None))


def rp_m(usd):
    return usd * IDR / 1e6


if __name__ == "__main__":
    print("INITIAL likely / low / high:")
    for i, nm in enumerate(("likely", "low", "high")):
        b = initial_breakdown(i)
        print(f"  {nm}: total ${initial_total(i):,.0f} (Rp {rp_m(initial_total(i)):.1f}M) ->",
              {k: round(v) for k, v in b.items()})
    print(f"  likely: before launch marketing ${initial_cash_before_launch_marketing(0):,.0f}")
    print(f"FIXED monthly likely/low/high: {FIXED_MONTHLY:.1f} / {FIXED_LOW:.1f} / {FIXED_HIGH:.1f} USD "
          f"(Rp {FIXED_MONTHLY*IDR/1e6:.2f}M / {FIXED_LOW*IDR/1e6:.2f}M / {FIXED_HIGH*IDR/1e6:.2f}M)")
    print("\nUNIT ECON")
    for k, s in SCEN.items():
        u = unit_econ(s)
        f = lambda v, fmt: ("never" if v is None else format(v, fmt))
        print(f"{k:6} CM={u['cm']:.1%} contrib/payer-mo=${u['contrib_pm']:.2f} LTV/payer=${u['ltv_payer']:.2f} "
              f"per_new=${u['per_new']:.4f} rev/new-user/mo=${u['rev_per_user']:.3f} "
              f"BEP users={f(u['users_bep'], ',.0f')} payers={f(u['payers_bep'], ',.0f')} | "
              f"$500: users={f(u['users_500'], ',.0f')} payers={f(u['payers_500'], ',.0f')}")
    print("\nRESULTS bootstrap (and plans)")
    for k, s in SCEN.items():
        for plan in PLANS:
            rows, sm = run(s, plan)
            g = lambda m: rows[m - 1]
            print(f"{k:6}/{plan:10} users12={sum(x['users'] for x in rows[:12]):7,.0f} "
                  f"payers12={g(12)['payers']:5.0f} R12=${g(12)['R']:6,.0f} profit12=${g(12)['profit']:6,.0f} "
                  f"profit24=${g(24)['profit']:6,.0f} profit36=${g(36)['profit']:6,.0f} "
                  f"cum12=${g(12)['cum']:7,.0f} cum24=${g(24)['cum']:7,.0f} cum36=${g(36)['cum']:7,.0f} "
                  f"peak=${sm['peak']:6,.0f} BEP={sm['bep']} payback={sm['payback']}")
    rows, _ = run(SCEN["worst"], "bootstrap")
    print("\nWorst kill gate:", {n: round(rows[n - 1]["cum"]) for n in (2, 3, 4, 6)})
    print("\nSENSITIVITY avg/bootstrap (profit m24, BEP):")
    base = SCEN["avg"]
    def tw(**kw):
        s = dict(base); s.update(kw); r, sm = run(s, "bootstrap")
        return round(r[23]["profit"]), sm["bep"]
    print(" base", tw())
    for lab, kw in [("conv 1%", dict(conv=.01)), ("conv 3%", dict(conv=.03)), ("conv 4%", dict(conv=.04)),
                    ("arppu 100", dict(arppu=100)), ("arppu 300", dict(arppu=300)), ("arppu 400", dict(arppu=400)),
                    ("ret 30%", dict(r=.30)), ("ret 65%", dict(r=.65)),
                    ("org 200", dict(org0=200)), ("org 800", dict(org0=800)), ("org 1500", dict(org0=1500)),
                    ("g 0%", dict(g=0.0)), ("g 10%", dict(g=0.10)),
                    ("markup 2.0", dict(p=.5)), ("markup 3.0", dict(p=.33)),
                    ("free .06", dict(free=.06)), ("free .015", dict(free=.015))]:
        print(f" {lab:10}", tw(**kw))
