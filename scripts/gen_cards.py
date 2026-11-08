#!/usr/bin/env python3
"""
Generate self-hosted profile cards (stats, streak, languages, activity) as SVG.

Runs inside GitHub Actions with GITHUB_TOKEN (or a PAT in METRICS_TOKEN for
private-repo languages). No third-party card service involved, so nothing
breaks when a public Vercel instance gets rate-limited or shut down.

Usage:
  GH_TOKEN=... python scripts/gen_cards.py --user prabhagaran --out assets
  python scripts/gen_cards.py --mock --out assets        # offline test render
"""
import argparse
import datetime as dt
import json
import os
import random
import sys
import urllib.request
from collections import defaultdict
from xml.sax.saxutils import escape

# ── palette (matches banner.svg) ────────────────────────────────────────────
BG0, BG1 = "#050b14", "#091628"
PANEL = "#0b1929"
GRID = "#00d4ff"
CYAN = "#00d4ff"
GOLD = "#ffd700"
PURPLE = "#bc8cff"
GREEN = "#3fb950"
ORANGE = "#ff7b59"
MUTED = "#6e7f96"
TEXT = "#c9d6e8"
EDGE = "#1e3a5f"
MONO = "Courier New, monospace"
SANS = "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
HEAT = ["#0f2033", "#0b4a63", "#0a7896", "#00a8cc", "#00d4ff"]

API = "https://api.github.com/graphql"


# ── data ────────────────────────────────────────────────────────────────────
def gql(token, query, variables=None):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"], indent=2))
    return body["data"]


Q_USER = """
query($login:String!){
  user(login:$login){
    createdAt
    followers{totalCount}
    pullRequests{totalCount}
    issues{totalCount}
    repositories(ownerAffiliations:OWNER, isFork:false, first:100,
                 orderBy:{field:PUSHED_AT, direction:DESC}){
      totalCount
      nodes{
        stargazerCount
        languages(first:10, orderBy:{field:SIZE, direction:DESC}){
          edges{size node{name color}}
        }
      }
    }
  }
}"""

Q_YEAR = """
query($login:String!, $from:DateTime!, $to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from, to:$to){
      totalCommitContributions
      restrictedContributionsCount
      contributionCalendar{
        totalContributions
        weeks{contributionDays{date contributionCount}}
      }
    }
  }
}"""


def fetch(token, login):
    u = gql(token, Q_USER, {"login": login})["user"]
    created = dt.datetime.fromisoformat(u["createdAt"].replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)

    days = {}
    commits_year = 0
    start = created
    while start < now:
        end = min(start + dt.timedelta(days=365), now)
        c = gql(token, Q_YEAR, {"login": login,
                                "from": start.isoformat(),
                                "to": end.isoformat()})["user"]["contributionsCollection"]
        for w in c["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                days[d["date"]] = d["contributionCount"]
        start = end

    # last-365-day commit count (calendar view GitHub shows on the profile)
    last = gql(token, Q_YEAR, {"login": login,
                              "from": (now - dt.timedelta(days=365)).isoformat(),
                              "to": now.isoformat()})["user"]["contributionsCollection"]
    commits_year = last["totalCommitContributions"] + last["restrictedContributionsCount"]

    langs = defaultdict(lambda: [0, MUTED])
    stars = 0
    for r in u["repositories"]["nodes"]:
        stars += r["stargazerCount"]
        for e in r["languages"]["edges"]:
            langs[e["node"]["name"]][0] += e["size"]
            if e["node"]["color"]:
                langs[e["node"]["name"]][1] = e["node"]["color"]

    return {
        "repos": u["repositories"]["totalCount"],
        "stars": stars,
        "followers": u["followers"]["totalCount"],
        "prs": u["pullRequests"]["totalCount"],
        "issues": u["issues"]["totalCount"],
        "commits_year": commits_year,
        "days": dict(sorted(days.items())),
        "langs": {k: tuple(v) for k, v in langs.items()},
    }


def mock():
    random.seed(7)
    today = dt.date.today()
    days = {}
    for i in range(900, -1, -1):
        d = today - dt.timedelta(days=i)
        days[d.isoformat()] = random.choice([0, 0, 0, 1, 2, 3, 5, 8, 12]) if i > 3 else 2
    return {
        "repos": 42, "stars": 37, "followers": 18, "prs": 23, "issues": 11,
        "commits_year": 612, "days": days,
        "langs": {"C": (920000, "#555555"), "C++": (610000, "#f34b7d"),
                  "Python": (330000, "#3572A5"), "HTML": (150000, "#e34c26"),
                  "Makefile": (40000, "#427819"), "Assembly": (22000, "#6E4C13"),
                  "Shell": (9000, "#89e051")},
    }


# ── analysis ────────────────────────────────────────────────────────────────
def streaks(days):
    dates = sorted(days)
    if not dates:
        return 0, 0, 0
    total = sum(days.values())
    longest = run = 0
    for d in dates:
        run = run + 1 if days[d] > 0 else 0
        longest = max(longest, run)
    # current streak: today may still be 0 without breaking it
    cur = 0
    rev = list(reversed(dates))
    if days[rev[0]] == 0:
        rev = rev[1:]
    for d in rev:
        if days[d] > 0:
            cur += 1
        else:
            break
    return total, cur, longest


def fmt(n):
    return f"{n/1000:.1f}k" if n >= 10000 else f"{n:,}"


# ── svg helpers ─────────────────────────────────────────────────────────────
def frame(w, h, title, body):
    grid = "".join(f'<line x1="0" y1="{y}" x2="{w}" y2="{y}"/>' for y in range(30, h, 30))
    grid += "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="{h}"/>' for x in range(30, w, 30))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="{BG0}"/><stop offset="100%" stop-color="{BG1}"/></linearGradient></defs>
<rect x="0.5" y="0.5" width="{w-1}" height="{h-1}" rx="8" fill="url(#bg)" stroke="{EDGE}"/>
<g opacity="0.07" stroke="{GRID}" stroke-width="0.5">{grid}</g>
<circle cx="20" cy="22" r="3" fill="{CYAN}"/>
<text x="32" y="27" font-family="{MONO}" font-size="13" font-weight="bold" fill="{CYAN}" letter-spacing="2">{escape(title)}</text>
<line x1="20" y1="40" x2="{w-20}" y2="40" stroke="{EDGE}"/>
{body}
</svg>
'''


def stats_card(s):
    total, cur, longest = streaks(s["days"])
    rows = [
        ("COMMITS · 12 MO", s["commits_year"], CYAN),
        ("PULL REQUESTS", s["prs"], PURPLE),
        ("ISSUES", s["issues"], ORANGE),
        ("REPOSITORIES", s["repos"], GREEN),
        ("STARS EARNED", s["stars"], GOLD),
    ]
    body = []
    y = 66
    for label, val, col in rows:
        body.append(f'<rect x="22" y="{y-9}" width="6" height="6" fill="{col}"/>'
                    f'<text x="38" y="{y-2}" font-family="{MONO}" font-size="11" fill="{MUTED}">{label}</text>'
                    f'<text x="250" y="{y-2}" text-anchor="end" font-family="{MONO}" font-size="13" font-weight="bold" fill="{TEXT}">{fmt(val)}</text>')
        y += 26
    # streak block on the right, styled as an IC with pins
    x0 = 285
    body.append(f'<rect x="{x0}" y="54" width="190" height="124" rx="3" fill="{PANEL}" stroke="{GOLD}" stroke-width="1.2"/>')
    for py in range(64, 175, 14):
        body.append(f'<rect x="{x0-5}" y="{py}" width="5" height="3" fill="{GOLD}"/>'
                    f'<rect x="{x0+190}" y="{py}" width="5" height="3" fill="{GOLD}"/>')
    body.append(
        f'<text x="{x0+95}" y="94" text-anchor="middle" font-family="{SANS}" font-size="34" font-weight="bold" fill="{GOLD}">{cur}</text>'
        f'<text x="{x0+95}" y="112" text-anchor="middle" font-family="{MONO}" font-size="10" fill="{MUTED}" letter-spacing="1">CURRENT STREAK · DAYS</text>'
        f'<line x1="{x0+15}" y1="124" x2="{x0+175}" y2="124" stroke="{EDGE}"/>'
        f'<text x="{x0+50}" y="148" text-anchor="middle" font-family="{MONO}" font-size="15" font-weight="bold" fill="{CYAN}">{longest}</text>'
        f'<text x="{x0+50}" y="164" text-anchor="middle" font-family="{MONO}" font-size="9" fill="{MUTED}">LONGEST</text>'
        f'<text x="{x0+140}" y="148" text-anchor="middle" font-family="{MONO}" font-size="15" font-weight="bold" fill="{CYAN}">{fmt(total)}</text>'
        f'<text x="{x0+140}" y="164" text-anchor="middle" font-family="{MONO}" font-size="9" fill="{MUTED}">ALL-TIME</text>')
    return frame(495, 195, "GITHUB STATS", "".join(body))


def langs_card(s, n=6):
    items = sorted(s["langs"].items(), key=lambda kv: -kv[1][0])
    tot = sum(v[0] for _, v in items) or 1
    top = items[:n]
    body = []
    # stacked bar = a "bus"
    x, bw = 22, 451
    body.append(f'<rect x="{x}" y="56" width="{bw}" height="10" rx="2" fill="{PANEL}"/>')
    for name, (size, col) in top:
        w = bw * size / tot
        body.append(f'<rect x="{x:.2f}" y="56" width="{max(w,1):.2f}" height="10" fill="{col}"/>')
        x += w
    # two-column legend
    for i, (name, (size, col)) in enumerate(top):
        cx = 22 + (i % 2) * 235
        cy = 96 + (i // 2) * 30
        pct = 100 * size / tot
        body.append(f'<rect x="{cx}" y="{cy-9}" width="10" height="10" rx="1" fill="{col}"/>'
                    f'<text x="{cx+18}" y="{cy}" font-family="{MONO}" font-size="12" fill="{TEXT}">{escape(name)}</text>'
                    f'<text x="{cx+210}" y="{cy}" text-anchor="end" font-family="{MONO}" font-size="12" fill="{MUTED}">{pct:.1f}%</text>')
    return frame(495, 195, "TOP LANGUAGES", "".join(body))


def activity_card(s):
    today = dt.date.today()
    start = today - dt.timedelta(days=today.weekday() + 1 + 52 * 7)  # Sunday, 53 cols
    days = s["days"]
    cell, gap = 12, 3
    x0, y0 = 46, 62
    last = [days.get((today - dt.timedelta(days=i)).isoformat(), 0) for i in range(365)]
    mx = max(last) or 1
    body = []
    months_done = set()
    d = start
    col = 0
    while d <= today:
        row = (d.weekday() + 1) % 7
        if row == 0 and d != start:
            col += 1
        v = days.get(d.isoformat(), 0)
        lvl = 0 if v == 0 else min(4, 1 + int(3 * v / mx + 0.5))
        x, y = x0 + col * (cell + gap), y0 + row * (cell + gap)
        body.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{HEAT[lvl]}"><title>{d}: {v}</title></rect>')
        if d.day <= 7 and row == 0 and (d.year, d.month) not in months_done:
            months_done.add((d.year, d.month))
            body.append(f'<text x="{x}" y="{y0-8}" font-family="{MONO}" font-size="10" fill="{MUTED}">{d.strftime("%b").upper()}</text>')
        d += dt.timedelta(days=1)
    for r, lab in ((1, "MON"), (3, "WED"), (5, "FRI")):
        body.append(f'<text x="{x0-8}" y="{y0 + r*(cell+gap) + 10}" text-anchor="end" font-family="{MONO}" font-size="9" fill="{MUTED}">{lab}</text>')
    total12 = sum(last)
    active = sum(1 for v in last if v)
    yb = y0 + 7 * (cell + gap) + 18
    body.append(f'<text x="{x0}" y="{yb}" font-family="{MONO}" font-size="11" fill="{MUTED}">'
                f'<tspan fill="{CYAN}" font-weight="bold">{total12:,}</tspan> contributions · '
                f'<tspan fill="{GOLD}" font-weight="bold">{active}</tspan> active days in the last 12 months</text>')
    lx = 900 - 22 - 5 * (cell + 2) - 70
    body.append(f'<text x="{lx}" y="{yb}" font-family="{MONO}" font-size="10" fill="{MUTED}">LESS</text>')
    for i, c in enumerate(HEAT):
        body.append(f'<rect x="{lx + 34 + i*(cell+2)}" y="{yb-10}" width="{cell}" height="{cell}" rx="2" fill="{c}"/>')
    body.append(f'<text x="{lx + 38 + 5*(cell+2)}" y="{yb}" font-family="{MONO}" font-size="10" fill="{MUTED}">MORE</text>')
    return frame(900, yb + 20, "CONTRIBUTION MATRIX · 52 WEEKS", "".join(body))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="prabhagaran")
    ap.add_argument("--out", default="assets")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()

    if a.mock:
        s = mock()
    else:
        tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not tok:
            sys.exit("GH_TOKEN not set")
        s = fetch(tok, a.user)

    os.makedirs(a.out, exist_ok=True)
    for name, svg in (("stats.svg", stats_card(s)),
                      ("languages.svg", langs_card(s)),
                      ("activity.svg", activity_card(s))):
        with open(os.path.join(a.out, name), "w", encoding="utf-8") as f:
            f.write(svg)
        print("wrote", os.path.join(a.out, name))


if __name__ == "__main__":
    main()
