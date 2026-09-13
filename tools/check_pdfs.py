#!/usr/bin/env python3
"""
check_pdfs.py - verify that every file the website links to actually exists,
under exactly the name the site uses.

Run from the project root (the folder containing _quarto.yml):

    python3 tools/check_pdfs.py

What it checks
--------------
1. Every  pdf:  line in academic-writing.yml, policy.yml and media.yml points
   at a file that exists on disk.
2. The name matches character for character, including capitalisation.
   This matters: macOS ignores case and GitHub's servers do not, so a link to
   "files/Policy/..." works on your laptop and 404s once published.
3. Every file linked from a .qmd page (CV, syllabi, book PDFs, cover images,
   photos) also exists.
4. Reports PDFs sitting in files/ that nothing on the site links to, so you can
   spot a report you added to the folder but forgot to add to the .yml.

When a file is missing, the script looks for the closest name on disk and
prints the exact characters that differ - usually an en dash (-) typed as a
hyphen (-), a curly apostrophe, or a double space.

Exit code is 0 if everything is fine, 1 if any problem was found.
"""

import os
import re
import sys
import unicodedata
from difflib import SequenceMatcher, get_close_matches

# --------------------------------------------------------------------------
# Configuration - edit these two lists if you add data files or asset folders.
# --------------------------------------------------------------------------

YAML_FILES = ["academic-writing.yml", "policy.yml", "media.yml"]
ASSET_ROOT = "files"

# A pdf: value of exactly this means "there is deliberately no PDF here".
NO_PDF_SENTINEL = "none"

# File extensions to treat as linked assets when scanning .qmd pages.
LINKED_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def describe_char(ch):
    """Human-readable description of one character, e.g. '- (U+2013 EN DASH)'."""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        name = "unnamed"
    if ch == " ":
        return "space (U+0020)"
    return "{} (U+{:04X} {})".format(ch, ord(ch), name)


def diff_report(expected, actual):
    """Describe how the yml value differs from the name on disk."""
    lines = []
    matcher = SequenceMatcher(None, expected, actual)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        yml_part = expected[i1:i2]
        disk_part = actual[j1:j2]
        if tag == "replace" and len(yml_part) == 1 and len(disk_part) == 1:
            lines.append("      yml has  {}".format(describe_char(yml_part)))
            lines.append("      disk has {}".format(describe_char(disk_part)))
        elif tag == "delete":
            lines.append("      yml has extra text: {!r}".format(yml_part))
        elif tag == "insert":
            lines.append("      yml is missing text: {!r}".format(disk_part))
        else:
            lines.append("      yml has  {!r}".format(yml_part))
            lines.append("      disk has {!r}".format(disk_part))
    return lines


def unicode_form(text):
    """Which Unicode form a string uses - the two look identical on screen."""
    if unicodedata.normalize("NFC", text) == text:
        return "NFC, accent built into the letter"
    if unicodedata.normalize("NFD", text) == text:
        return "NFD, accent stored as a separate mark"
    return "a mixture of NFC and NFD"


def list_dir(path):
    """Directory entries, or empty list if the directory does not exist."""
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def resolve_exact(relpath):
    """
    Walk a relative path one component at a time, comparing each component
    against the real names on disk. Returns (status, detail):

      ("ok",       None)               - exists, exact match
      ("norm",     corrected_path)     - same letters, different Unicode form
      ("case",     corrected_path)     - exists but spelled differently
      ("missing",  parent_dir)         - the final component was not found
      ("no-dir",   missing_dir_path)   - a parent folder does not exist
    """
    parts = [p for p in relpath.split("/") if p]
    current = "."
    corrected = []
    normalisation_issue = False
    for index, part in enumerate(parts):
        entries = list_dir(current)
        if part in entries:
            corrected.append(part)
            current = os.path.join(current, part)
            continue
        # Same letters, different Unicode form: "o" + combining accent (how
        # macOS often stores it) versus the single character "o".
        nfc = {unicodedata.normalize("NFC", e): e for e in entries}
        key = unicodedata.normalize("NFC", part)
        if key in nfc:
            normalisation_issue = True
            corrected.append(nfc[key])
            current = os.path.join(current, nfc[key])
            continue
        # Differs only in capitalisation or doubled spaces.
        folded = {unicodedata.normalize("NFC", e).casefold().replace("  ", " "): e
                  for e in entries}
        key = key.casefold().replace("  ", " ")
        if key in folded:
            corrected.append(folded[key])
            current = os.path.join(current, folded[key])
            continue
        if index < len(parts) - 1:
            return "no-dir", "/".join(parts[: index + 1])
        return "missing", current
    fixed = "/".join(corrected)
    if fixed == relpath.strip("/"):
        return "ok", None
    return ("norm", fixed) if normalisation_issue else ("case", fixed)


def build_disk_index():
    """Map every file under files/ by a loosened version of its name, so a file
    sitting in the wrong folder or spelled with different capitalisation can
    still be located."""
    index = {}
    for dirpath, _dirnames, filenames in os.walk(ASSET_ROOT):
        for filename in filenames:
            key = unicodedata.normalize("NFC", filename).casefold()
            path = os.path.join(dirpath, filename).replace(os.sep, "/")
            index.setdefault(key, []).append(path)
    return index


def suggest(name, directory):
    """Closest filename in `directory` to `name`, or None if nothing is close."""
    entries = list_dir(directory)
    matches = get_close_matches(name, entries, n=1, cutoff=0.82)
    return matches[0] if matches else None


# --------------------------------------------------------------------------
# Collecting the links
# --------------------------------------------------------------------------

def read_yaml_pdfs(path):
    """
    Return a list of (line_number, pdf_value, entry_title) for every pdf: line.
    Deliberately a plain text scan, so the script needs no extra packages.
    """
    found = []
    title = "(no title)"
    if not os.path.exists(path):
        print("  ! data file not found: {}".format(path))
        return found
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            title_match = re.match(r"\s*title:\s*(.+?)\s*$", line)
            if title_match:
                title = title_match.group(1).strip("'\"")
            pdf_match = re.match(r"\s*pdf:\s*(.+?)\s*$", line)
            if pdf_match:
                found.append((number, pdf_match.group(1).strip("'\""), title))
    return found


def read_qmd_links():
    """Return a list of (filename, relative_link) for assets linked in pages."""
    found = []
    for name in sorted(os.listdir(".")):
        if not name.endswith(".qmd"):
            continue
        with open(name, encoding="utf-8") as handle:
            text = handle.read()
        # Strip HTML comments so commented-out links are not flagged.
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        pattern = r"(?:href=\"|src=\"|\]\()({}/[^\"')\s][^\"')]*)".format(ASSET_ROOT)
        for link in re.findall(pattern, text):
            link = link.split("#")[0].split("?")[0]
            if link.lower().endswith(LINKED_EXTENSIONS):
                found.append((name, link))
    return found


def walk_assets():
    """Every PDF currently sitting under files/."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(ASSET_ROOT):
        for filename in filenames:
            if filename.lower().endswith(".pdf"):
                found.append(os.path.join(dirpath, filename).replace(os.sep, "/"))
    return sorted(found)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    if not os.path.exists("_quarto.yml"):
        print("Run this from the project root - the folder holding _quarto.yml.")
        return 2

    problems = 0
    linked = set()
    disk_index = build_disk_index()

    print("=" * 72)
    print("1. PDFs listed in the data files")
    print("=" * 72)

    for data_file in YAML_FILES:
        entries = read_yaml_pdfs(data_file)
        print("\n{}  ({} pdf: lines)".format(data_file, len(entries)))
        declared_none = 0
        for number, value, title in entries:
            if value == NO_PDF_SENTINEL:
                declared_none += 1
                continue
            status, detail = resolve_exact(value)
            if status == "ok":
                linked.add(value)
            elif status == "case":
                problems += 1
                linked.add(detail)
                print("\n  CASE MISMATCH  line {}".format(number))
                print("    entry: {}".format(title[:70]))
                print("    yml:   {}".format(value))
                print("    disk:  {}".format(detail))
                print("    -> works on your Mac, will 404 once published")
            elif status == "norm":
                problems += 1
                linked.add(detail)
                print("\n  ACCENT ENCODING MISMATCH  line {}".format(number))
                print("    entry: {}".format(title[:70]))
                print("    yml:   {}".format(value))
                print("    disk:  {}".format(detail))
                print("    yml uses:  {}".format(unicode_form(value)))
                print("    disk uses: {}".format(unicode_form(detail)))
                print("    -> the accented letters are stored differently in the")
                print("       yml and on disk; the link will 404 once published.")
                print("       Fix by renaming the file in Finder (retype the")
                print("       accented letter), then re-running this script.")
            elif status == "no-dir":
                problems += 1
                print("\n  FOLDER NOT FOUND  line {}".format(number))
                print("    entry:   {}".format(title[:70]))
                print("    missing: {}/".format(detail))
            else:
                problems += 1
                wanted = value.rsplit("/", 1)[-1]
                print("\n  MISSING  line {}".format(number))
                print("    entry: {}".format(title[:70]))
                print("    yml:   {}".format(value))
                elsewhere = disk_index.get(
                    unicodedata.normalize("NFC", wanted).casefold(), [])
                closest = suggest(wanted, detail)
                if elsewhere:
                    for path in elsewhere:
                        print("    found at: {}".format(path))
                        linked.add(path)
                    print("    -> the file is in a different folder, or the folder")
                    print("       name is capitalised differently in the yml")
                elif closest:
                    print("    disk:  {}".format(closest))
                    print("    differences:")
                    for line in diff_report(wanted, closest):
                        print(line)
                else:
                    print("    no similar file in {}/".format(detail))
        if declared_none:
            print("  {} entr{} marked 'pdf: none' (skipped)".format(
                declared_none, "y" if declared_none == 1 else "ies"))

    print("\n" + "=" * 72)
    print("2. Files linked from the pages")
    print("=" * 72)

    qmd_links = read_qmd_links()
    page_problems = 0
    for page, link in qmd_links:
        status, detail = resolve_exact(link)
        if status == "ok":
            linked.add(link)
        elif status in ("case", "norm"):
            page_problems += 1
            linked.add(detail)
            label = "CASE MISMATCH" if status == "case" else "ACCENT ENCODING MISMATCH"
            print("\n  {}  {}".format(label, page))
            print("    page: {}".format(link))
            print("    disk: {}".format(detail))
        else:
            page_problems += 1
            print("\n  MISSING  {}".format(page))
            print("    page: {}".format(link))
    problems += page_problems
    if page_problems == 0:
        print("\n  all {} linked files present".format(len(qmd_links)))

    print("\n" + "=" * 72)
    print("3. PDFs on disk that nothing links to")
    print("=" * 72)

    orphans = [p for p in walk_assets() if p not in linked]
    if orphans:
        print("\n  Not an error - but check whether an entry is missing:")
        for path in orphans:
            print("    {}".format(path))
    else:
        print("\n  none")

    print("\n" + "=" * 72)
    if problems:
        print("{} problem{} found. Fix before pushing.".format(
            problems, "" if problems == 1 else "s"))
        print("Tip: copy the 'disk:' line into the yml rather than retyping it,")
        print("so dashes and apostrophes come across exactly.")
    else:
        print("All good - every linked file is present and correctly named.")
    print("=" * 72)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
