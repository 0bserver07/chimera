import os
import tempfile


from chimera.context.mentions import Mention, MentionResolver


def test_resolve_file():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "test.py"), "w") as f:
            f.write("def hello(): pass")
        resolver = MentionResolver(workdir=d)
        cleaned, mentions = resolver.resolve("Check @file:test.py please")
        assert len(mentions) == 1
        assert mentions[0].type == "file"
        assert "def hello" in mentions[0].content
        assert "@file" not in cleaned


def test_resolve_folder():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "src"))
        with open(os.path.join(d, "src", "a.py"), "w") as f:
            f.write("")
        with open(os.path.join(d, "src", "b.py"), "w") as f:
            f.write("")
        resolver = MentionResolver(workdir=d)
        _, mentions = resolver.resolve("Look at @folder:src")
        assert len(mentions) == 1
        assert "a.py" in mentions[0].content


def test_resolve_url():
    # Just test that it handles missing httpx gracefully
    resolver = MentionResolver()
    _, mentions = resolver.resolve("See @url:https://example.com")
    assert len(mentions) == 1
    # Either fetched content or "httpx not installed" message
    assert mentions[0].content != ""


def test_no_mentions():
    resolver = MentionResolver()
    cleaned, mentions = resolver.resolve("Just plain text")
    assert cleaned == "Just plain text"
    assert mentions == []


def test_inject():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "utils.py"), "w") as f:
            f.write("x = 1")
        resolver = MentionResolver(workdir=d)
        result = resolver.inject("Check @file:utils.py")
        assert "x = 1" in result
        assert "utils.py" in result


def test_missing_file():
    resolver = MentionResolver(workdir="/tmp")
    _, mentions = resolver.resolve("@file:nonexistent.py")
    assert "not found" in mentions[0].content


def test_multiple_mentions():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "a.py"), "w") as f:
            f.write("a")
        with open(os.path.join(d, "b.py"), "w") as f:
            f.write("b")
        resolver = MentionResolver(workdir=d)
        _, mentions = resolver.resolve("@file:a.py and @file:b.py")
        assert len(mentions) == 2


def test_mention_dataclass():
    m = Mention(type="file", reference="foo.py", content="hello")
    assert m.type == "file"
    assert m.reference == "foo.py"
    assert m.content == "hello"


def test_cleaned_text_removes_double_spaces():
    resolver = MentionResolver(workdir="/tmp")
    cleaned, _ = resolver.resolve("before @file:nonexistent.py after")
    assert "  " not in cleaned
    assert "before" in cleaned
    assert "after" in cleaned


def test_inject_url_format():
    resolver = MentionResolver()
    result = resolver.inject("See @url:https://example.com")
    assert "--- url: https://example.com ---" in result


def test_resolve_folder_empty():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "empty"))
        resolver = MentionResolver(workdir=d)
        _, mentions = resolver.resolve("@folder:empty")
        assert len(mentions) == 1
        assert "empty" in mentions[0].content


def test_resolve_folder_not_found():
    resolver = MentionResolver(workdir="/tmp")
    _, mentions = resolver.resolve("@folder:does_not_exist_xyz")
    assert len(mentions) == 1
    assert "not found" in mentions[0].content


def test_trailing_punctuation_is_stripped():
    """Bug fix: regex previously greedily consumed trailing punctuation.

    Inputs like ``"check @file:foo.py, please"`` used to resolve the
    reference as ``"foo.py,"`` (with the comma), which obviously fails
    to match any real file. Each of the samples below must resolve to
    the clean reference.
    """
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "foo.py"), "w") as f:
            f.write("x = 1")
        resolver = MentionResolver(workdir=d)

        for wrapped, expected_ref in [
            ("check @file:foo.py, please", "foo.py"),
            ("see @file:foo.py.", "foo.py"),
            ("run @file:foo.py; then stop", "foo.py"),
            ("look at (@file:foo.py)", "foo.py"),
            ("entries are [@file:foo.py]", "foo.py"),
        ]:
            _, mentions = resolver.resolve(wrapped)
            assert len(mentions) == 1, f"failed on: {wrapped!r}"
            assert mentions[0].reference == expected_ref, (
                f"got {mentions[0].reference!r} for {wrapped!r}"
            )
            # And critically the file actually resolved (i.e. we ended
            # up with the real content, not a 'file not found' sentinel).
            assert "x = 1" in mentions[0].content, (
                f"expected file content for {wrapped!r}, got "
                f"{mentions[0].content!r}"
            )


def test_url_trailing_punctuation():
    """URLs embedded in prose should strip trailing sentence punctuation."""
    resolver = MentionResolver()
    _, mentions = resolver.resolve("See @url:https://example.com.")
    assert len(mentions) == 1
    assert mentions[0].reference == "https://example.com"
