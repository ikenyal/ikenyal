#!/usr/bin/env python3
"""Rebuild the generated sections of README.md.

- HACKATHONS: from data/hackathons.yml
- OSS:        merged pull requests in public repositories (GitHub search API)

Usage:
  GITHUB_TOKEN=... python scripts/build_readme.py           # both sections
  python scripts/build_readme.py --skip-oss                  # gallery only (no token needed)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
USER = "ikenyal"
COLUMNS = 3

LINK_LABELS = [
    ("repo", "Repo"),
    ("demo", "Demo"),
    ("youtube", "Video"),
    ("devpost", "Devpost"),
    ("project", "Builder Center"),
    ("article", "Article"),
]


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def replace_section(readme: str, name: str, body: str) -> str:
    pattern = re.compile(
        rf"(<!-- {name}:START -->).*?(<!-- {name}:END -->)", re.DOTALL
    )
    if not pattern.search(readme):
        sys.exit(f"marker <!-- {name}:START/END --> not found in README.md")
    return pattern.sub(lambda m: m.group(1) + "\n" + body + "\n" + m.group(2), readme)


# ---------------------------------------------------------------- hackathons
def video_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def card(item: dict) -> str:
    name = esc(item["name"])
    thumb = item.get("image") or (
        f"https://img.youtube.com/vi/{item['youtube']}/mqdefault.jpg"
        if item.get("youtube")
        else None
    )
    main_link = item.get("repo") or item.get("project") or item.get("devpost")
    watch = video_url(item["youtube"]) if item.get("youtube") else main_link

    lines = []
    if thumb:
        lines.append(
            f'<a href="{esc(watch)}"><img src="{esc(thumb)}" width="100%" alt="{name}"></a><br>'
        )
    title = f'<a href="{esc(main_link)}">{name}</a>' if main_link else name
    result = f" &nbsp;{esc(item['result'])}" if item.get("result") else ""
    lines.append(f"<b>{title}</b>{result}<br>")
    lines.append(f"<sub>{esc(item['event'])} · {esc(item['date'])}</sub><br>")
    lines.append(f"{esc(item['tagline'])}<br>")

    links = []
    for key, label in LINK_LABELS:
        if not item.get(key):
            continue
        url = video_url(item[key]) if key == "youtube" else item[key]
        links.append(f'<a href="{esc(url)}">{label}</a>')
    if links:
        lines.append(f"<sub>{' · '.join(links)}</sub>")

    return f'<td width="{100 // COLUMNS}%" valign="top">\n' + "\n".join(lines) + "\n</td>"


def build_hackathons() -> str:
    items = yaml.safe_load((ROOT / "data/hackathons.yml").read_text()) or []
    for item in items:
        for key in ("name", "tagline", "event", "date"):
            if not item.get(key):
                sys.exit(f"hackathons.yml: '{key}' is missing in {item}")
        item["date"] = str(item["date"])
    rows = []
    for i in range(0, len(items), COLUMNS):
        cells = [card(item) for item in items[i : i + COLUMNS]]
        while len(cells) < COLUMNS:
            cells.append(f'<td width="{100 // COLUMNS}%"></td>')
        rows.append("<tr>\n" + "\n".join(cells) + "\n</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


# ----------------------------------------------------------------------- OSS
def _neg_date(date: str) -> str:
    """Sort key that puts newer YYYY-MM-DD dates first."""
    return "".join(chr(0x7F - ord(c)) for c in date)


def search_merged_prs(token: str) -> list[dict]:
    query = f"author:{USER} type:pr is:merged is:public"
    results: list[dict] = []
    for page in range(1, 11):  # search API returns at most 1,000 results
        params = urllib.parse.urlencode(
            {"q": query, "per_page": 100, "page": page, "sort": "created", "order": "desc"}
        )
        req = urllib.request.Request(
            f"https://api.github.com/search/issues?{params}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.load(res)
        items = data.get("items", [])
        results.extend(items)
        if len(items) < 100:
            break
    return results


def build_oss(token: str) -> str:
    conf = yaml.safe_load((ROOT / "data/oss.yml").read_text()) or {}
    exclude_owners = {o.lower() for o in conf.get("exclude_owners", [])}
    exclude_repos = {r.lower() for r in conf.get("exclude_repos", [])}
    highlight = [r.lower() for r in conf.get("highlight", [])]
    max_repos = int(conf.get("max_repos", 12))
    recent = int(conf.get("recent_per_repo", 3))

    by_repo: dict[str, list[dict]] = defaultdict(list)
    for pr in search_merged_prs(token):
        repo = pr["repository_url"].split("/repos/", 1)[1]
        owner = repo.split("/", 1)[0].lower()
        if owner in exclude_owners or repo.lower() in exclude_repos:
            continue
        merged = (pr.get("pull_request") or {}).get("merged_at") or pr.get("closed_at") or ""
        by_repo[repo].append(
            {"title": pr["title"], "url": pr["html_url"], "merged": merged[:10]}
        )
    if not by_repo:
        return "_No merged pull requests found yet._"

    for prs in by_repo.values():
        prs.sort(key=lambda p: p["merged"], reverse=True)

    def rank(repo: str):
        lowered = repo.lower()
        pinned = highlight.index(lowered) if lowered in highlight else len(highlight)
        return (pinned, _neg_date(by_repo[repo][0]["merged"]), repo.lower())

    repos = sorted(by_repo, key=rank)
    total = sum(len(v) for v in by_repo.values())
    shown = repos[:max_repos]

    lines = [
        f"**{total}** merged pull requests across **{len(by_repo)}** public repositories.",
        "",
        "| Repository | Merged PRs | Latest |",
        "| --- | ---: | --- |",
    ]
    for repo in shown:
        prs = by_repo[repo]
        latest = "<br>".join(
            f"[{esc(p['title'])}]({p['url']}) <sub>{p['merged']}</sub>" for p in prs[:recent]
        )
        all_prs = (
            f"https://github.com/{repo}/pulls?q=is%3Apr+is%3Amerged+author%3A{USER}"
        )
        lines.append(f"| [{repo}](https://github.com/{repo}) | [{len(prs)}]({all_prs}) | {latest} |")
    if len(repos) > len(shown):
        lines.append("")
        lines.append(
            f"<sub>…and {len(repos) - len(shown)} more. "
            f"[See all](https://github.com/search?q=author%3A{USER}+type%3Apr+is%3Amerged+is%3Apublic&type=pullrequests)</sub>"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------- main
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-oss", action="store_true")
    args = parser.parse_args()

    readme = README.read_text()
    readme = replace_section(readme, "HACKATHONS", build_hackathons())
    count = len(yaml.safe_load((ROOT / "data/hackathons.yml").read_text()) or [])
    readme = re.sub(
        r"<!-- HACKATHON_COUNT:START -->.*?<!-- HACKATHON_COUNT:END -->",
        f"<!-- HACKATHON_COUNT:START -->{count} projects so far.<!-- HACKATHON_COUNT:END -->",
        readme,
    )

    if not args.skip_oss:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            sys.exit("GITHUB_TOKEN is required for the OSS section (or use --skip-oss)")
        readme = replace_section(readme, "OSS", build_oss(token))

    README.write_text(readme)
    print("README.md updated")


if __name__ == "__main__":
    main()
