"""--index: a TSV record of everything browsing saw, and nothing it didn't fetch.

The load-bearing property is that the index is a pure observer: it is fed from
listings the session was already pulling (browse, preload, a recursive download's
enumeration) and can never cause a request of its own.
"""

from __future__ import annotations

from smbex.backend.base import DirEntry
from smbex.index import ListingIndex

TREE = {
    "share": {
        "docs": {"a.txt": b"aaa", "b.txt": b"bb"},
        "pics": {"1.png": b"x"},
        "readme.txt": b"hello",
    },
}


def _rows(path) -> list[list[str]]:
    """Data lines of an index file, split into fields (comments dropped)."""
    return [
        line.split("\t")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def _entries(*specs) -> list[DirEntry]:
    return [DirEntry(name=n, is_dir=d, size=s, mtime=m) for n, d, s, m in specs]


# --- the file format --------------------------------------------------------


def test_writes_one_line_per_entry_with_type_size_and_time(tmp_path):
    import datetime

    when = 1756000000.0
    stamp = datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%dT%H:%M")  # local
    out = tmp_path / "seen.tsv"
    index = ListingIndex(out, target="user@host")
    index.add("share", _entries(("docs", True, 0, when), ("readme.txt", False, 5, 0.0)))
    index.close()

    assert _rows(out) == [
        ["/share/docs", "dir", "-", stamp],
        ["/share/readme.txt", "file", "5", "-"],  # unknown mtime -> "-"
    ]
    header = out.read_text().splitlines()[0]
    assert header.startswith("# path\t")  # column header written on creation
    assert "target=user@host" in out.read_text()  # provenance for the session


def test_directory_size_is_a_dash_not_a_zero(tmp_path):
    # A folder's recursive size can't be known without walking it, and "0" would be
    # a lie an agent reading this file could act on.
    out = tmp_path / "seen.tsv"
    index = ListingIndex(out)
    index.add("", _entries(("share", True, 0, 0.0)))
    index.close()
    assert _rows(out) == [["/share", "dir", "-", "-"]]


def test_no_file_is_created_when_nothing_was_seen(tmp_path):
    out = tmp_path / "seen.tsv"
    index = ListingIndex(out)
    index.add("share", [])
    index.close()
    assert not out.exists()  # opened lazily: an empty session leaves no litter


def test_tabs_and_newlines_in_names_are_escaped(tmp_path):
    out = tmp_path / "seen.tsv"
    index = ListingIndex(out)
    index.add("share", _entries(("od\td\nname.txt", False, 3, 0.0)))
    index.close()

    rows = _rows(out)
    assert len(rows) == 1 and len(rows[0]) == 4  # still exactly one line, four fields
    assert rows[0][0] == "/share/od\\td\\nname.txt"


def test_translation_column_only_when_it_adds_something(tmp_path):
    out = tmp_path / "seen.tsv"
    table = {"写真": "Photos"}
    index = ListingIndex(out, translate=lambda name, is_dir: table.get(name))
    index.add("share", _entries(("写真", True, 0, 0.0), ("plain.txt", False, 1, 0.0)))
    index.close()

    rows = _rows(out)
    assert rows[0] == ["/share/写真", "dir", "-", "-", "Photos"]
    assert rows[1] == ["/share/plain.txt", "file", "1", "-"]  # no column when untranslated


# --- de-duplication ---------------------------------------------------------


def test_the_same_listing_twice_writes_one_line(tmp_path):
    out = tmp_path / "seen.tsv"
    index = ListingIndex(out)
    entries = _entries(("a.txt", False, 1, 0.0))
    assert index.add("share", entries) == 1
    assert index.add("share", entries) == 0
    index.close()
    assert len(_rows(out)) == 1


def test_a_later_run_appends_only_what_is_new(tmp_path):
    out = tmp_path / "seen.tsv"
    first = ListingIndex(out, target="run1")
    first.add("share", _entries(("a.txt", False, 1, 0.0)))
    first.close()

    second = ListingIndex(out, target="run2")  # same file, new session
    added = second.add("share", _entries(("a.txt", False, 1, 0.0), ("b.txt", False, 2, 0.0)))
    second.close()

    assert added == 1  # "a.txt" was already on file
    assert [r[0] for r in _rows(out)] == ["/share/a.txt", "/share/b.txt"]
    text = out.read_text()
    assert text.count("# path\t") == 1  # column header written once, ever
    assert "target=run1" in text and "target=run2" in text  # both sessions recorded


def test_a_write_failure_never_breaks_browsing(tmp_path):
    index = ListingIndex(tmp_path / "nodir" / "x" / "seen.tsv")
    index._open = lambda: (_ for _ in ()).throw(OSError("read-only filesystem"))
    assert index.add("share", _entries(("a.txt", False, 1, 0.0))) == 0  # swallowed


# --- fed from real browsing, and only from it -------------------------------


async def test_browsing_records_what_it_saw_without_extra_requests(make_app, settle, tmp_path):
    out = tmp_path / "seen.tsv"
    app = make_app(dict(TREE), index_path=out)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("l")  # into "share"
        await settle(app, pilot)
        backend = app._gateway._backend
        calls = list(backend.list_calls)

        paths = {row[0] for row in _rows(out)}
        assert "/share" in paths  # the root listing
        assert {"/share/docs", "/share/pics", "/share/readme.txt"} <= paths
        # Only folders that were actually listed appear — the index never walked
        # ahead into pics/, which browsing hadn't opened.
        assert "/share/pics/1.png" not in paths
        assert backend.list_calls == calls  # reading the index caused no fetch
        await pilot.press("q")


async def test_the_index_adds_no_backend_calls_at_all(make_app, settle, tmp_path):
    """Same browse with and without --index must hit the backend identically."""

    async def browse(index_path):
        app = make_app(dict(TREE), index_path=index_path)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            await pilot.press("l")
            await settle(app, pilot)
            await pilot.press("j")
            await settle(app, pilot)
            calls = list(app._gateway._backend.list_calls)
            await pilot.press("q")
        return calls

    assert await browse(tmp_path / "seen.tsv") == await browse(None)


async def test_revisiting_a_folder_does_not_duplicate_lines(make_app, settle, tmp_path):
    out = tmp_path / "seen.tsv"
    app = make_app(dict(TREE), index_path=out)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("l")  # into share
        await settle(app, pilot)
        await pilot.press("h")  # back out (served from cache)
        await pilot.press("l")  # and in again
        await settle(app, pilot)

        paths = [row[0] for row in _rows(out)]
        assert len(paths) == len(set(paths))
        await pilot.press("q")


async def test_a_recursive_download_records_the_tree_it_enumerated(make_app, settle, tmp_path):
    """The enumeration a folder download does is data pulled anyway — record it."""
    out = tmp_path / "seen.tsv"
    app = make_app(dict(TREE), index_path=out, download_root=tmp_path / "dl")
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("l")  # into share; "docs" selected
        await pilot.press("d")  # grab docs/ recursively
        await app.downloads.join()
        await pilot.pause()

        paths = {row[0] for row in _rows(out)}
        assert {"/share/docs/a.txt", "/share/docs/b.txt"} <= paths
        await pilot.press("q")


async def test_translation_column_reaches_the_file(make_app, settle, tmp_path, fake_translator):
    out = tmp_path / "seen.tsv"
    tr = fake_translator({"docs": "documents", "pics": "pictures"})
    app = make_app(dict(TREE), index_path=out, translator=tr)  # translation starts on
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("l")
        await settle(app, pilot)

        rendered = {row[0]: row for row in _rows(out)}
        assert rendered["/share/docs"][-1] == "documents"
        assert rendered["/share/readme.txt"][:2] == ["/share/readme.txt", "file"]
        await pilot.press("q")
