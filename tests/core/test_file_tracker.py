"""Tests for chimera.core.file_tracker."""
from chimera.core.file_tracker import FileTracker
from chimera.compaction.base import CompactionMetadata, FileAwareCompaction
from chimera.compaction.summary import SummaryCompaction
from chimera.types import Message


def test_record_read():
    ft = FileTracker()
    ft.record_read("src/main.py")
    assert ft.read_files == ["src/main.py"]

def test_record_modified():
    ft = FileTracker()
    ft.record_modified("src/main.py")
    assert ft.modified_files == ["src/main.py"]

def test_dedup_reads():
    ft = FileTracker()
    ft.record_read("a.py")
    ft.record_read("a.py")
    ft.record_read("b.py")
    assert ft.read_files == ["a.py", "b.py"]

def test_dedup_modifications():
    ft = FileTracker()
    ft.record_modified("a.py")
    ft.record_modified("a.py")
    assert ft.modified_files == ["a.py"]

def test_to_prompt_section_empty():
    ft = FileTracker()
    assert ft.to_prompt_section() == ""

def test_to_prompt_section_with_files():
    ft = FileTracker()
    ft.record_read("a.py")
    ft.record_modified("b.py")
    section = ft.to_prompt_section()
    assert "Modified: b.py" in section
    assert "Read: a.py" in section

def test_to_metadata():
    ft = FileTracker()
    ft.record_read("a.py")
    ft.record_modified("b.py")
    meta = ft.to_metadata()
    assert meta.read_files == ["a.py"]
    assert meta.modified_files == ["b.py"]

def test_compaction_metadata_merge():
    m1 = CompactionMetadata(read_files=["a.py"], modified_files=["b.py"])
    m2 = CompactionMetadata(read_files=["a.py", "c.py"], modified_files=["d.py"])
    merged = m1.merge(m2)
    assert merged.read_files == ["a.py", "c.py"]
    assert merged.modified_files == ["b.py", "d.py"]

def test_summary_compaction_is_file_aware():
    sc = SummaryCompaction()
    assert isinstance(sc, FileAwareCompaction)

def test_file_aware_set_metadata():
    sc = SummaryCompaction()
    meta = CompactionMetadata(read_files=["a.py"], modified_files=["b.py"])
    sc.set_metadata(meta)
    section = sc.get_file_prompt_section()
    assert "a.py" in section
    assert "b.py" in section

def test_file_section_empty_without_metadata():
    sc = SummaryCompaction()
    assert sc.get_file_prompt_section() == ""

def test_compact_includes_files():
    sc = SummaryCompaction(keep_first=1, keep_last=1)
    meta = CompactionMetadata(read_files=["src/app.py"], modified_files=["src/main.py"])
    sc.set_metadata(meta)
    messages = [
        Message.system("system"),
        Message.user("do stuff"),
        Message.assistant("ok"),
        Message.user("more stuff"),
        Message.assistant("done"),
    ]
    result = sc.compact(messages, budget=1000)
    summary_msg = result[1]
    assert "src/main.py" in summary_msg.content or "src/app.py" in summary_msg.content
