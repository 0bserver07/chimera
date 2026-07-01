"""Tests for the stdlib .env loader (chimera.config.dotenv)."""
import os

from chimera.config.dotenv import load_dotenv, parse_dotenv


def test_parse_handles_export_quotes_and_comments():
    d = parse_dotenv('A=1\nexport B="two"\nC=\'three\'\n# a comment\n\nD=four # inline\n')
    assert d == {"A": "1", "B": "two", "C": "three", "D": "four"}


def test_parse_skips_malformed_lines():
    assert parse_dotenv("noequals\n=noval\nOK=yes\n") == {"OK": "yes"}


def test_load_does_not_override_by_default(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("CHIMERA_TEST_X=fromfile\nCHIMERA_TEST_Y=y\n")
    monkeypatch.setenv("CHIMERA_TEST_X", "fromshell")
    monkeypatch.delenv("CHIMERA_TEST_Y", raising=False)

    applied = load_dotenv(env)

    assert "CHIMERA_TEST_Y" in applied
    assert "CHIMERA_TEST_X" not in applied  # existing shell var wins
    assert os.environ["CHIMERA_TEST_X"] == "fromshell"
    assert os.environ["CHIMERA_TEST_Y"] == "y"


def test_load_override_replaces_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("CHIMERA_TEST_Z=fromfile\n")
    monkeypatch.setenv("CHIMERA_TEST_Z", "fromshell")

    load_dotenv(env, override=True)

    assert os.environ["CHIMERA_TEST_Z"] == "fromfile"


def test_load_missing_file_is_noop(tmp_path):
    assert load_dotenv(tmp_path / "does_not_exist.env") == []
