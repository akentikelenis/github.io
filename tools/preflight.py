#!/usr/bin/env python3
"""
preflight.py - pre-publication checks for kentikelenis.net

Run from the repository root:

    python3 tools/preflight.py

To also test every external link (slow, needs internet):

    python3 tools/preflight.py --links

Exit code is 0 if no ERRORs were found, 1 otherwise.
"""

import argparse
import pathlib
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is missing. Install it with:  pip3 install pyyaml")

# --------------------------------------------------------------------------
# Configuration - edit here if folders or file names ever change.
# --------------------------------------------------------------------------

DATA_FILES = ["academic-writing.yml", "policy.yml", "media.yml"]
PDF_FOLDERS = ["files/academic writing", "files/policy", "files/media"]
NONE_SENTINEL = "none"          # value meaning "no such link" in the YAML
LINK_FIELDS = ["awards", "reviewed_in", "cited_in", "media_in"]

# Characters that change the meaning of a URL and must not sit raw in a path.
UNSAFE_IN_PATH = {
    "?": "starts the query string - everything after it is dropped",
    "#": "starts the fragment - everything after it is dropped",
    "%": "starts a percent-escape - may be mis-decoded",
    "&": "separates query parameters",
    "+": "may be read as a space",
}

errors = []
warnings = []
notes = []


def error(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def note(msg):
    notes.append(msg)


def label(entry, source):
    """A short human-readable identifier for a YAML entry."""
    return f"{source} | {entry.get('year', '????')} | {str(entry.get('title', ''))[:55]}"


# --------------------------------------------------------------------------
# Load the data files
# --------------------------------------------------------------------------

def load_entries(root):
    entries = []
    for name in DATA_FILES:
        path = root / name
        if not path.exists():
            error(f"Data file not found: {name}")
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            error(f"{name} is not valid YAML: {exc}")
            continue
        if not isinstance(data, list):
            error(f"{name} should be a list of entries.")
            continue
        for entry in data:
            if isinstance(entry, dict):
                entry["_source"] = name
                entries.append(entry)
            else:
                error(f"{name} contains an entry that is not a key/value block.")
    return entries


# --------------------------------------------------------------------------
# Check 1 - PDF paths: URL-safe, correctly normalised, present on disk
# --------------------------------------------------------------------------

def check_pdfs(root, entries):
    referenced = set()

    for entry in entries:
        pdf = entry.get("pdf")
        if not pdf or pdf == NONE_SENTINEL:
            continue

        # 1a. unsafe characters in the URL path
        for char, why in UNSAFE_IN_PATH.items():
            if char in pdf:
                error(
                    f"'{char}' in PDF filename - {why}.\n"
                    f"         {label(entry, entry['_source'])}\n"
                    f"         {pdf}"
                )

        # 1b. unicode normalisation (macOS stores NFD, web servers are byte-exact)
        if pdf != unicodedata.normalize("NFC", pdf):
            error(
                f"PDF path is not in NFC normal form - will 404 on GitHub Pages.\n"
                f"         {label(entry, entry['_source'])}\n"
                f"         {pdf}"
            )

        # 1c. file actually on disk, matching byte for byte
        target = root / pdf
        referenced.add(unicodedata.normalize("NFC", pdf))
        if not target.exists():
            near = [
                p.name for p in (root / target.parent).glob("*.pdf")
                if unicodedata.normalize("NFC", p.name).lower()
                == unicodedata.normalize("NFC", target.name).lower()
            ] if target.parent.exists() else []
            hint = f"\n         on disk as: {near[0]}" if near else ""
            error(
                f"PDF referenced but not found on disk.\n"
                f"         {label(entry, entry['_source'])}\n"
                f"         {pdf}{hint}"
            )

    # 1d. PDFs sitting on disk that nothing links to
    for folder in PDF_FOLDERS:
        directory = root / folder
        if not directory.exists():
            warn(f"PDF folder missing: {folder}")
            continue
        for pdf_file in sorted(directory.glob("*.pdf")):
            rel = unicodedata.normalize("NFC", str(pdf_file.relative_to(root)))
            if rel not in referenced:
                note(f"PDF on disk but not referenced in any YAML: {rel}")
            if pdf_file.name != unicodedata.normalize("NFC", pdf_file.name):
                error(
                    f"Filename on disk is not in NFC normal form - rename it.\n"
                    f"         {rel}"
                )


# --------------------------------------------------------------------------
# Check 2 - the 'none' sentinel
# --------------------------------------------------------------------------

def check_sentinel(entries):
    counts = {}
    examples = {}
    for entry in entries:
        for field in ("pdf", "doi", "url", "data"):
            if entry.get(field) == NONE_SENTINEL:
                counts[field] = counts.get(field, 0) + 1
                examples.setdefault(field, label(entry, entry["_source"]))
    for field, count in sorted(counts.items()):
        note(
            f"'{field}: none' used {count}x - confirm assets/pub-listing.ejs "
            f"treats 'none' as absent for '{field}', or that button will link to "
            f"a file called 'none'.\n         e.g. {examples[field]}"
        )


# --------------------------------------------------------------------------
# Check 3 - annotation lines (awards / cited_in / reviewed_in / media_in)
# --------------------------------------------------------------------------

def check_annotations(entries):
    for entry in entries:
        for field in LINK_FIELDS:
            items = entry.get(field) or []
            if not isinstance(items, list):
                error(f"{field} should be a list.\n         {label(entry, entry['_source'])}")
                continue
            for item in items:
                if not isinstance(item, dict):
                    error(f"{field} item is not a key/value block.\n         {label(entry, entry['_source'])}")
                    continue
                text = item.get("text", "")
                code = item.get("code")
                url = item.get("url")
                if not text:
                    error(f"{field} item has no text.\n         {label(entry, entry['_source'])}")
                if url and not code:
                    warn(
                        f"{field} item has a url but no code - the link may not "
                        f"render.\n         {label(entry, entry['_source'])}\n"
                        f"         {text[:70]}"
                    )
                if code and code not in text:
                    error(
                        f"{field} code is not a substring of its text - link will "
                        f"not appear.\n         {label(entry, entry['_source'])}\n"
                        f"         code: {code!r}"
                    )
                if code and not url:
                    warn(f"{field} item has a code but no url.\n         {label(entry, entry['_source'])}")


# --------------------------------------------------------------------------
# Check 4 - general hygiene
# --------------------------------------------------------------------------

def check_hygiene(entries):
    seen_titles = {}

    for entry in entries:
        source = entry["_source"]

        for required in ("type", "year", "authors", "title"):
            if not entry.get(required):
                error(f"missing '{required}'.\n         {label(entry, source)}")

        # duplicate titles across all data files
        key = str(entry.get("title", "")).strip().lower()
        if key:
            if key in seen_titles:
                warn(f"duplicate title, also in {seen_titles[key]}.\n         {label(entry, source)}")
            else:
                seen_titles[key] = source

        # category filters only exist on the academic writing page
        if source in ("academic-writing.yml", "policy.yml"):
            cats = entry.get("cats")
            if not cats:
                error(f"missing 'cats' - entry will not appear under any filter.\n         {label(entry, source)}")
            else:
                if isinstance(cats, str):
                    cats = [cats]
                for cat in cats:
                    if cat not in ("gov", "health"):
                        error(f"unknown category {cat!r}.\n         {label(entry, source)}")

        # insecure links
        for field in ("url", "data", "pdf"):
            value = entry.get(field)
            if isinstance(value, str) and value.startswith("http://"):
                warn(f"{field} uses http:// rather than https://.\n         {label(entry, source)}\n         {value}")

        # DOIs should be bare, not full URLs
        doi = entry.get("doi")
        if doi and doi != NONE_SENTINEL and not str(doi).startswith("10."):
            error(f"doi should be bare (10.xxxx/...), got {doi!r}.\n         {label(entry, source)}")

        # stray whitespace that will show up in the rendered line
        for field, value in entry.items():
            if isinstance(value, str) and value != value.strip():
                warn(f"leading/trailing space in '{field}'.\n         {label(entry, source)}")

    # reverse-chronological order within each type, per file
    for name in DATA_FILES:
        subset = [e for e in entries if e["_source"] == name]
        for kind in sorted({e.get("type") for e in subset}):
            years = [e.get("year", 0) for e in subset if e.get("type") == kind]
            if years != sorted(years, reverse=True):
                warn(f"{name}: entries of type '{kind}' are not in reverse-chronological order.")


# --------------------------------------------------------------------------
# Check 5 - external links (opt-in, needs internet)
# --------------------------------------------------------------------------

def check_links(entries):
    urls = {}
    for entry in entries:
        for field in ("url", "data"):
            value = entry.get(field)
            if isinstance(value, str) and value.startswith("http"):
                urls.setdefault(value, label(entry, entry["_source"]))
        doi = entry.get("doi")
        if doi and str(doi).startswith("10."):
            urls.setdefault("https://doi.org/" + str(doi), label(entry, entry["_source"]))
        for field in LINK_FIELDS:
            for item in entry.get(field) or []:
                if isinstance(item, dict) and item.get("url"):
                    urls.setdefault(item["url"], label(entry, entry["_source"]))

    print(f"Testing {len(urls)} external links (this takes a few minutes)...\n")
    for index, (url, where) in enumerate(sorted(urls.items()), start=1):
        print(f"  [{index}/{len(urls)}] {url[:80]}", flush=True)
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "Mozilla/5.0 (preflight link check)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                if response.status >= 400:
                    warn(f"HTTP {response.status}\n         {where}\n         {url}")
        except urllib.error.HTTPError as exc:
            # 403 usually means a bot block, not a dead page
            level = note if exc.code in (403, 429) else warn
            level(f"HTTP {exc.code}\n         {where}\n         {url}")
        except Exception as exc:
            warn(f"unreachable ({type(exc).__name__})\n         {where}\n         {url}")


# --------------------------------------------------------------------------
# Check 6 - build output, run after 'quarto render'
# --------------------------------------------------------------------------

def check_build(root):
    docs = root / "docs"
    if not docs.exists():
        note("docs/ not found - run 'quarto render' before publishing.")
        return
    if not (docs / ".nojekyll").exists():
        error("docs/.nojekyll is missing - GitHub Pages will run Jekyll and may "
              "drop files.\n         Fix with:  touch docs/.nojekyll")
    if not (docs / "CNAME").exists():
        error("docs/CNAME is missing - the custom domain will not resolve.")
    for page in ("index.html", "about.html", "academic-writing.html",
                 "policy.html", "media.html", "books.html", "teaching.html", "cv.html"):
        if not (docs / page).exists():
            warn(f"docs/{page} not found - did the render complete?")


# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pre-publication checks.")
    parser.add_argument("--links", action="store_true",
                        help="also test every external URL (slow)")
    parser.add_argument("--root", default=".",
                        help="repository root (default: current folder)")
    args = parser.parse_args()

    root = pathlib.Path(args.root).resolve()
    print(f"Checking {root}\n")

    entries = load_entries(root)
    print(f"Loaded {len(entries)} entries from {len(DATA_FILES)} data files.\n")

    check_pdfs(root, entries)
    check_sentinel(entries)
    check_annotations(entries)
    check_hygiene(entries)
    check_build(root)
    if args.links:
        check_links(entries)

    print()
    for heading, bucket in (("ERROR", errors), ("WARNING", warnings), ("NOTE", notes)):
        for message in bucket:
            print(f"{heading}: {message}")
        if bucket:
            print()

    print(f"{len(errors)} error(s), {len(warnings)} warning(s), {len(notes)} note(s).")
    if errors:
        print("\nFix the errors before publishing.")
    elif warnings:
        print("\nNo blocking problems. Review the warnings.")
    else:
        print("\nAll clear.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
