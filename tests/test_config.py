"""Config file: INI parsing, path resolution, sample write, and the
built-in < config < CLI precedence (plus initial sort reaching the app)."""

from __future__ import annotations

from pathlib import Path

from smbex.cli import build_parser
from smbex.config import config_path, load_config, write_sample_config


def test_config_path_uses_xdg_and_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "smbex" / "config.ini"
    assert config_path("/x/y.ini") == Path("/x/y.ini")


def test_missing_or_blank_config_is_empty(tmp_path):
    assert load_config(tmp_path / "nope.ini") == {}
    blank = tmp_path / "blank.ini"
    blank.write_text("[ui]\ntranslate =\nsort =\n")  # blank values are ignored
    assert load_config(blank) == {}


def test_load_config_types(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        "[ui]\npreload = true\nauto_reconnect = false\n"
        "translate = ja\nsort = newest\ndownload_dir = /data/dl\n"
    )
    assert load_config(cfg) == {
        "preload": True,
        "auto_reconnect": False,
        "translate": "ja",
        "sort": "newest",
        "download_dir": "/data/dl",
    }


def test_malformed_config_does_not_raise(tmp_path):
    bad = tmp_path / "bad.ini"
    bad.write_text("not = ini [without a section")
    assert load_config(bad) == {}  # launch must not be blocked by a bad file


def test_write_sample_config_roundtrips(tmp_path):
    path = write_sample_config(tmp_path / "smbex" / "config.ini")
    assert path.is_file()
    cfg = load_config(path)
    assert cfg["preload"] is False and cfg["sort"] == "name"
    assert "translate" not in cfg  # blank in the sample -> omitted


def test_precedence_builtin_config_cli(tmp_path):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[ui]\npreload = true\nsort = newest\nauto_reconnect = true\n")

    parser = build_parser()
    assert parser.parse_args(["host"]).preload is False  # built-in default

    parser.set_defaults(**load_config(cfg))  # config seeds defaults
    seeded = parser.parse_args(["host"])
    assert seeded.preload is True and seeded.auto_reconnect is True
    assert seeded.sort == "newest"

    overridden = parser.parse_args(["host", "--no-preload", "--sort", "name"])
    assert overridden.preload is False and overridden.sort == "name"  # CLI wins


def test_initial_sort_reaches_the_browser(make_app):
    assert make_app(sort="newest").browser.sort_mode == "mtime_desc"
    assert make_app(sort="oldest").browser.sort_mode == "mtime_asc"
    assert make_app().browser.sort_mode == "name"
