"""Flat download layout: one folder per host, remote path folded into the name.

Mirrored layout is covered by test_download.py; here we pin the name construction
(separator, unsafe characters, byte-truncation), the collision numbering, and the
end-to-end landing place through the TUI.
"""

from __future__ import annotations

from smbex.download import DownloadManager, flat_name


def test_flat_name_folds_the_path():
    assert flat_name("share/2024/report.pdf") == "share_2024_report.pdf"
    assert flat_name("readme.txt") == "readme.txt"
    assert flat_name("/leading/slash.txt") == "leading_slash.txt"


def test_flat_name_keeps_unicode_but_drops_unsafe_characters():
    assert flat_name("share/写真/家族.jpg") == "share_写真_家族.jpg"  # names may be CJK
    assert flat_name('share/a:b*c?d"e.txt') == "share_a_b_c_d_e.txt"
    assert flat_name("share/back\\slash.txt") == "share_back_slash.txt"
    assert flat_name("") == "download"  # never an empty filename


def test_flat_name_truncates_in_bytes_and_keeps_the_extension():
    name = flat_name("x/" + "写" * 300 + ".txt")  # 3 bytes a character
    assert len(name.encode()) <= 200  # room under a 255-byte filesystem limit
    assert name.endswith(".txt") and name.startswith("x_写")


def test_flat_layout_maps_into_one_directory(tmp_path):
    dl = DownloadManager(None, tmp_path, flat=True)
    assert dl._local_for("share/docs/a.txt") == tmp_path / "share_docs_a.txt"
    assert dl._local_for("share/docs/a.txt") == tmp_path / "share_docs_a.txt"  # stable


def test_mirror_layout_is_unchanged(tmp_path):
    dl = DownloadManager(None, tmp_path)
    assert dl._local_for("share/docs/a.txt") == tmp_path / "share" / "docs" / "a.txt"


def test_colliding_names_are_numbered_not_overwritten(tmp_path):
    # 'a/b_c.txt' and 'a_b/c.txt' both fold to 'a_b_c.txt' — the second must not
    # silently overwrite the first.
    dl = DownloadManager(None, tmp_path, flat=True)
    first = dl._local_for("a/b_c.txt")
    second = dl._local_for("a_b/c.txt")
    assert first == tmp_path / "a_b_c.txt"
    assert second == tmp_path / "a_b_c~2.txt"
    assert dl._local_for("a/b_c.txt") == first  # each keeps its own name
    assert dl._local_for("a_b/c.txt") == second


def test_extensionless_collision_is_numbered_too(tmp_path):
    dl = DownloadManager(None, tmp_path, flat=True)
    assert dl._local_for("a/b_c") == tmp_path / "a_b_c"
    assert dl._local_for("a_b/c") == tmp_path / "a_b_c~2"


# --- through the UI ---------------------------------------------------------


async def test_flat_download_lands_in_one_folder(make_app, tmp_path):
    tree = {"share": {"docs": {"deep": {"note.txt": b"hi"}}}}
    app = make_app(tree, download_root=tmp_path, flat=True)
    async with app.run_test() as pilot:
        await pilot.press("l")  # into share
        await pilot.press("d")  # grab docs/ recursively
        await app.downloads.join()

        assert (tmp_path / "share_docs_deep_note.txt").read_bytes() == b"hi"
        assert not (tmp_path / "share").exists()  # no mirrored tree
        await pilot.press("q")


async def test_flat_download_preview_finds_the_file(make_app, tmp_path):
    """The preview's 'is this downloaded?' lookup must agree with the flat name."""
    tree = {"share": {"notes.txt": b"hello\nworld\n"}}
    app = make_app(tree, download_root=tmp_path, flat=True)
    async with app.run_test() as pilot:
        await pilot.press("l")  # into share; notes.txt selected
        await pilot.press("d")
        await app.downloads.join()
        for _ in range(60):
            await pilot.pause()
            if "hello\nworld" in app.query_one("#preview").rendered_text:
                break
        assert (tmp_path / "share_notes.txt").is_file()
        assert "hello\nworld" in app.query_one("#preview").rendered_text
        await pilot.press("q")
