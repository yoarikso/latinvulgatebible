#!/usr/bin/env python3

# This vulgate.py fetches Biblia Sacra Vulgata text from Bible Gateway and encodes it into JSON.
# Structure is aligned to Catholic Public Domain Version in file naming and JSON structure (see cpdv.py in cpdvbible)
# Please read the README.md file for more information.

import argparse
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

try:
    import certifi
except ImportError:
    certifi = None  # type: ignore[assignment, misc]

CHAPTERS_FILE = "data/bible-vulgate-book-chapters.json"

PASSAGE_URL = "https://www.biblegateway.com/passage/"

# Bible Gateway uses "VULGATE" for Biblia Sacra Vulgata.
BIBLE_GATEWAY_BIBLE_VERSION = "VULGATE"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; vulgate.py/1.0; +https://www.biblegateway.com/)"
    ),
}

# Per-verse spans use a reference token "{BookAbbr}-{chapter}-{verse}" (e.g. Lev-1-3, 1Sam-1-1).
REF_CLASS_RE = re.compile(r"^(.+)-(\d+)-(\d+)$")
HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


def strip_leading_chapter_number_from_verse_one(text: str, chapter_num: int) -> str:
    """Remove a leading chapter number Bible Gateway repeats on the first verse of each chapter."""
    if not text:
        return text
    prefix = str(chapter_num)
    t = text.lstrip()
    if t.startswith(prefix):
        rest = t[len(prefix) :]
        if not rest or rest[0].isspace():
            return rest.lstrip()
    return text


def _https_context():
    """Use certifi's CA bundle when available (fixes many macOS python.org SSL failures)."""
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _load_chapters_by_book():
    with open(CHAPTERS_FILE, encoding="utf-8") as f:
        entries = json.load(f)
    return {e["Book"]: int(e["Chapters"]) for e in entries}


CHAPTERS_BY_BOOK = _load_chapters_by_book()


def cpdv_suffix_to_vulgate_book(suffix):
    """Map CPDV-style book slug (after OT-xx_ / NT-xx_) to keys in bible-vulgate-book-chapters.json."""
    if suffix == "Song2":
        return "SongofSongs"
    return suffix.replace("-", "")


def _passage_html_slice(full_html: str) -> str:
    """Keep only the main passage body; avoids footnote lists and page chrome.

    Bible Gateway Biblia Sacra Vulgata marks verses with span.text plus a class like Lev-1-3
    (not older verse/chapter-* wrapper markup).
    """
    marker = 'class="passage-text"'
    i = full_html.find(marker)
    if i < 0:
        return full_html
    j = full_html.find(">", i) + 1
    end = full_html.find('class="footnotes"', j)
    if end < 0:
        end = len(full_html)
    return full_html[j:end]


def _class_list(attrs):
    for k, v in attrs:
        if k == "class":
            return v.split()
    return []


class _VulgateChapterParser(HTMLParser):
    """Parse Bible Gateway Biblia Sacra Vulgata HTML: span.text with a {Book}-{ch}-{v} class; skip outline headings."""

    def __init__(self, chapter_num: int):
        super().__init__(convert_charrefs=True)
        self.chapter_num = chapter_num
        self.verses: dict[str, str] = {}
        self._stack: list[str] = []
        self._span_matched: list[bool] = []
        self._verse_nest = 0
        self._verse_num: int | None = None
        self._chunks: list[str] = []
        self._sup_skip = 0

    def _flush_verse(self):
        raw = "".join(self._chunks)
        raw = re.sub(r"\s+", " ", raw).strip()
        raw = html.unescape(raw)
        if self._verse_num == 1:
            raw = strip_leading_chapter_number_from_verse_one(raw, self.chapter_num)
        if raw and self._verse_num is not None:
            key = str(self._verse_num)
            # Bible Gateway may use multiple sibling span.text tags for one verse.
            if key in self.verses:
                prior = self.verses[key]
                if prior:
                    merged = f"{prior} {raw}"
                    self.verses[key] = re.sub(r"\s+", " ", merged).strip()
                else:
                    self.verses[key] = raw
            else:
                self.verses[key] = raw
        self._chunks = []
        self._verse_num = None

    def handle_starttag(self, tag, attrs):
        self._stack.append(tag)
        cl = _class_list(attrs)
        clset = set[str](cl)

        if tag == "span":
            matched = False
            if "text" in clset:
                for c in cl:
                    m = REF_CLASS_RE.match(c)
                    if m and int(m.group(2)) == self.chapter_num:
                        if not any(t in HEADINGS for t in self._stack[:-1]):
                            matched = True
                            self._verse_nest += 1
                            if self._verse_nest == 1:
                                self._verse_num = int(m.group(3))
                                self._chunks = []
                        break
            self._span_matched.append(matched)

        if self._verse_nest > 0:
            if tag == "sup" and (
                "versenum" in clset
                or "footnote" in clset
                or "crossreference" in clset
            ):
                self._sup_skip += 1
            elif tag == "br":
                self._chunks.append(" ")

    def handle_endtag(self, tag):
        if self._verse_nest > 0:
            if self._sup_skip > 0 and tag == "sup":
                self._sup_skip -= 1

        if tag == "span" and self._span_matched:
            vm = self._span_matched.pop()
            if vm:
                self._verse_nest -= 1
                if self._verse_nest == 0:
                    self._flush_verse()

        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data):
        if self._verse_nest > 0 and self._sup_skip == 0:
            self._chunks.append(data)


def fetch_chapter_html(book, chapter):
    search = f"{book} {chapter}"
    qs = urllib.parse.urlencode({"search": search, "version": BIBLE_GATEWAY_BIBLE_VERSION})
    url = f"{PASSAGE_URL}?{qs}"
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(
            req, timeout=60, context=_https_context()
        ) as resp:
            return resp.read().decode(
                resp.headers.get_content_charset() or "utf-8", errors="replace"
            )
    except (urllib.error.URLError, OSError) as e:
        print(f"    fetch error {book} {chapter}: {e}", file=sys.stderr)
        return None


def parse_chapter_verses(html: str, chapter_num: int) -> dict[str, str]:
    if not html:
        return {}
    snippet = _passage_html_slice(html)
    parser = _VulgateChapterParser(chapter_num)
    parser.feed(snippet)
    parser.close()
    return parser.verses


def to_json(book_name, bible_map):
    book_name_key = book_name[book_name.index("_") + 1 :]
    vulgate_book = cpdv_suffix_to_vulgate_book(book_name_key)
    num_chapters = CHAPTERS_BY_BOOK[vulgate_book]

    print(f"Processing Book: {vulgate_book}")

    book = {}
    for chapter in range(1, num_chapters + 1):
        print(f"  Chapter {chapter}")
        page = fetch_chapter_html(vulgate_book, chapter)
        verses = parse_chapter_verses(page or "", chapter)
        if not verses:
            print(
                f"    warning: no verses parsed for {vulgate_book} {chapter}",
                file=sys.stderr,
            )
        book[str(chapter)] = verses

    if bible_map is not None:
        bible_map[book_name_key] = book.copy()

    book["charset"] = "UTF-8"

    json_str = json.dumps(book, indent=4, ensure_ascii=False)

    file_json = f"vulgate-json/{book_name}.json"
    os.makedirs(os.path.dirname(file_json), exist_ok=True)
    with open(file_json, "w", encoding="utf-8") as f:
        f.write(json_str)

def merge_entire_bible_json():
    folder = "vulgate-json"
    output_file = "EntireBible-VULGATE.json"

    book_files = []
    for filename in os.listdir(folder):
        if filename == output_file or not filename.endswith(".json"):
            continue

        match = re.match(r"^(OT|NT)-(\d+)_([^.]+)\.json$", filename)
        if not match:
            continue

        testament, order, book_name_key = match.groups()
        testament_order = 0 if testament == "OT" else 1
        book_files.append((testament_order, int(order), book_name_key, filename))

    book_files.sort(key=lambda x: (x[0], x[1]))

    entire_bible = {"charset": "UTF-8"}
    for _, _, book_name_key, filename in book_files:
        file_path = os.path.join(folder, filename)
        with open(file_path, encoding="utf-8") as f:
            entire_bible[book_name_key] = json.load(f)

    file_json = os.path.join(folder, output_file)
    with open(file_json, "w", encoding="utf-8") as f:
        json.dump(entire_bible, f, indent=4, ensure_ascii=False)


if __name__ == "__main__":

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Fetch Biblia Sacra Vulgata from Bible Gateway into JSON."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "-m",
        "--merge-bible",
        action="store_true",
        help="Merge vulgate-json books into a single EntireBible-VULGATE.json and exit.",
    )
    mode.add_argument(
        "-e",
        "--encode-bible",
        action="store_true",
        help="Fetch and encode all books into vulgate-json (and write EntireBible-VULGATE.json).",
    )
    args = parser.parse_args()

    if args.merge_bible:
        merge_entire_bible_json()
        sys.exit(0)

    if not args.encode_bible:
        parser.print_help()
        sys.exit(0)

    print("Encoding Biblia Sacra Vulgata Bible into JSON format")

    bible_map = {}
    bible_map["charset"] = "UTF-8"

    os.makedirs("vulgate-json", exist_ok=True)

    start_time = time.time()

    # Old Testament
    to_json("OT-01_Genesis", bible_map)
    to_json("OT-02_Exodus", bible_map)
    to_json("OT-03_Leviticus", bible_map)
    to_json("OT-04_Numbers", bible_map)
    to_json("OT-05_Deuteronomy", bible_map)
    to_json("OT-06_Joshua", bible_map)
    to_json("OT-07_Judges", bible_map)
    to_json("OT-08_Ruth", bible_map)
    to_json("OT-09_1-Samuel", bible_map)
    to_json("OT-10_2-Samuel", bible_map)
    to_json("OT-11_1-Kings", bible_map)
    to_json("OT-12_2-Kings", bible_map)
    to_json("OT-13_1-Chronicles", bible_map)
    to_json("OT-14_2-Chronicles", bible_map)
    to_json("OT-15_Ezra", bible_map)
    to_json("OT-16_Nehemiah", bible_map)
    to_json("OT-17_Tobit", bible_map)
    to_json("OT-18_Judith", bible_map)
    to_json("OT-19_Esther", bible_map)
    to_json("OT-20_Job", bible_map)
    to_json("OT-21_Psalms", bible_map)
    to_json("OT-22_Proverbs", bible_map)
    to_json("OT-23_Ecclesiastes", bible_map)
    to_json("OT-24_Song2", bible_map)
    to_json("OT-25_Wisdom", bible_map)
    to_json("OT-26_Sirach", bible_map)
    to_json("OT-27_Isaiah", bible_map)
    to_json("OT-28_Jeremiah", bible_map)
    to_json("OT-29_Lamentations", bible_map)
    to_json("OT-30_Baruch", bible_map)
    to_json("OT-31_Ezekiel", bible_map)
    to_json("OT-32_Daniel", bible_map)
    to_json("OT-33_Hosea", bible_map)
    to_json("OT-34_Joel", bible_map)
    to_json("OT-35_Amos", bible_map)
    to_json("OT-36_Obadiah", bible_map)
    to_json("OT-37_Jonah", bible_map)
    to_json("OT-38_Micah", bible_map)
    to_json("OT-39_Nahum", bible_map)
    to_json("OT-40_Habakkuk", bible_map)
    to_json("OT-41_Zephaniah", bible_map)
    to_json("OT-42_Haggai", bible_map)
    to_json("OT-43_Zechariah", bible_map)
    to_json("OT-44_Malachi", bible_map)
    to_json("OT-45_1-Maccabees", bible_map)
    to_json("OT-46_2-Maccabees", bible_map)

    # New Testament
    to_json("NT-01_Matthew", bible_map)
    to_json("NT-02_Mark", bible_map)
    to_json("NT-03_Luke", bible_map)
    to_json("NT-04_John", bible_map)
    to_json("NT-05_Acts", bible_map)
    to_json("NT-06_Romans", bible_map)
    to_json("NT-07_1-Corinthians", bible_map)
    to_json("NT-08_2-Corinthians", bible_map)
    to_json("NT-09_Galatians", bible_map)
    to_json("NT-10_Ephesians", bible_map)
    to_json("NT-11_Philippians", bible_map)
    to_json("NT-12_Colossians", bible_map)
    to_json("NT-13_1-Thessalonians", bible_map)
    to_json("NT-14_2-Thessalonians", bible_map)
    to_json("NT-15_1-Timothy", bible_map)
    to_json("NT-16_2-Timothy", bible_map)
    to_json("NT-17_Titus", bible_map)
    to_json("NT-18_Philemon", bible_map)
    to_json("NT-19_Hebrews", bible_map)
    to_json("NT-20_James", bible_map)
    to_json("NT-21_1-Peter", bible_map)
    to_json("NT-22_2-Peter", bible_map)
    to_json("NT-23_1-John", bible_map)
    to_json("NT-24_2-John", bible_map)
    to_json("NT-25_3-John", bible_map)
    to_json("NT-26_Jude", bible_map)
    to_json("NT-27_Revelation", bible_map)

    end_time = time.time()

    total_time = (end_time - start_time)
    total_time_ms = total_time * 1000

    print(
        f"Finished encoding Biblia Sacra Vulgata Bible into JSON format - {total_time_ms:.0f}ms ({total_time:.0f}s)"
    )

    json_str = json.dumps(bible_map, indent=4, ensure_ascii=False)
    file_json = "vulgate-json/EntireBible-VULGATE.json"
    with open(file_json, "w", encoding="utf-8") as f:
        f.write(json_str)
