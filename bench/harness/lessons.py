#!/usr/bin/env python3
"""Lessons: a small indexed memory of toolkit pitfalls and their fixes, for kit builders.

  python3 memory/lessons.py "<error text or topic>"       top matches, compact (default 3; --n N)
  python3 memory/lessons.py --index                        rewrite memory/INDEX.md (one line per lesson)
  python3 memory/lessons.py --show L03                     one lesson in full
  python3 memory/lessons.py --add --title T --match REGEX --tags a,b --cause C --fix F [--snippet S]
  python3 memory/lessons.py --hit L03                      record that a lesson helped

Lessons live in memory/lessons.jsonl, one JSON object per line: id, title, match (regexes for the error text it
explains), tags, cause, fix, snippet, hits. `pgm` failures get matching lessons attached automatically as "hints".
"""
import json
import re
import sys
from pathlib import Path

MEM = Path(__file__).resolve().parent
STORE = MEM / "lessons.jsonl"
MAX_FIELD = 600


def load():
    return [json.loads(l) for l in STORE.read_text().splitlines() if l.strip()] if STORE.exists() else []


def save(lessons):
    STORE.write_text("".join(json.dumps(l) + "\n" for l in lessons))


def words(s):
    return {w for w in re.findall(r"[a-z@][a-z0-9_@-]{2,}", s.lower())}


def score(lesson, text):
    s = 0.0
    for pat in lesson.get("match", []):
        try:
            if re.search(pat, text, re.I):
                s += 10
        except re.error:
            pass
    q = words(text)
    s += 2 * len(q & set(lesson.get("tags", [])))
    s += 1.0 * len(q & words(lesson["title"]))
    s += 0.3 * len(q & words(lesson["cause"] + " " + lesson["fix"]))
    return s


def query(text, n=3, min_score=1.0):
    ranked = sorted(((score(l, text), l) for l in load()), key=lambda x: -x[0])
    return [l for s, l in ranked if s >= min_score][:n]


def compact(l, full=False):
    out = f"[{l['id']}] {l['title']}\n  cause: {l['cause']}\n  fix:   {l['fix']}"
    if l.get("snippet") and full:
        out += "\n  e.g.\n" + "\n".join("    " + s for s in l["snippet"].splitlines())
    return out


def write_index():
    ls = load()
    lines = ["# Lessons index", "", "Query with `python3 memory/lessons.py \"<error or topic>\"`; full entry with `--show ID`.", ""]
    lines += [f"- {l['id']} {l['title']}  ({', '.join(l.get('tags', []))})" for l in ls]
    (MEM / "INDEX.md").write_text("\n".join(lines) + "\n")
    return len(ls)


def arg(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 0
    if a[0] == "--index":
        print(f"INDEX.md: {write_index()} lessons")
        return 0
    if a[0] == "--show":
        for l in load():
            if l["id"] == arg("--show"):
                print(compact(l, full=True))
                return 0
        print("no such lesson")
        return 1
    if a[0] == "--hit":
        ls = load()
        for l in ls:
            if l["id"] == arg("--hit"):
                l["hits"] = l.get("hits", 0) + 1
        save(ls)
        return 0
    if a[0] == "--add":
        new = {k: (arg("--" + k) or "").strip() for k in ("title", "match", "tags", "cause", "fix", "snippet")}
        missing = [k for k in ("title", "cause", "fix") if not new[k]]
        if missing:
            print(f"--add needs {', '.join('--' + k for k in missing)}")
            return 1
        if any(len(new[k]) > MAX_FIELD for k in ("cause", "fix")):
            print(f"keep cause and fix under {MAX_FIELD} characters each")
            return 1
        dupes = [l for l in query(new["title"] + " " + new["cause"] + " " + new["match"], 1, 6.0)]
        if dupes:
            print("a similar lesson exists; improve it instead (edit memory/lessons.jsonl):\n" + compact(dupes[0]))
            return 1
        ls = load()
        lesson = {"id": f"L{max([int(l['id'][1:]) for l in ls] + [0]) + 1:02d}", "title": new["title"],
                  "match": [new["match"]] if new["match"] else [], "tags": [t.strip() for t in new["tags"].split(",") if t.strip()],
                  "cause": new["cause"], "fix": new["fix"], "snippet": new["snippet"], "hits": 0, "added_by": "agent"}
        save(ls + [lesson])
        write_index()
        print(f"added {lesson['id']}")
        return 0
    n = int(arg("--n", "3"))
    text = " ".join(x for x in a if not x.startswith("--") and x != arg("--n"))
    hits = query(text, n)
    print("\n\n".join(compact(l, full=True) for l in hits) if hits else "no matching lesson")
    return 0


if __name__ == "__main__":
    sys.exit(main())
