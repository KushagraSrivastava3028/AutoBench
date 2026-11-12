import io
import tarfile

import pytest

from autobench.core.errors import TaskSpecError
from autobench.taskspec import loader


def test_archive_cannot_escape_to_a_sibling_with_a_shared_prefix(tmp_path, monkeypatch):
    root = tmp_path / "upload"
    root.mkdir()
    monkeypatch.setattr(loader.tempfile, "mkdtemp", lambda **kwargs: str(root))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("../upload-sibling/escaped.txt")
        member.size = 4
        archive.addfile(member, io.BytesIO(b"data"))
    with pytest.raises(TaskSpecError, match="escapes the extraction root"):
        loader.load_task_from_tar(buffer.getvalue())
    assert not root.exists(), "Rejected uploads must not leak temporary directories"
    assert not (tmp_path / "upload-sibling").exists()


def test_expansion_limit_is_checked_before_reading_large_file_data():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("huge.bin")
        member.size = loader.MAX_UPLOAD_EXPANDED_BYTES + 1
        archive.addfile(member)
    with pytest.raises(TaskSpecError, match="expanded size limit"):
        loader.load_task_from_tar(buffer.getvalue())


def test_archive_member_count_is_bounded(monkeypatch):
    monkeypatch.setattr(loader, "MAX_UPLOAD_MEMBERS", 2)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for index in range(3):
            archive.addfile(tarfile.TarInfo(f"file-{index}"))
    with pytest.raises(TaskSpecError, match="too many files"):
        loader.load_task_from_tar(buffer.getvalue())


def test_unreadable_upload_has_a_useful_error_and_cleans_up(tmp_path, monkeypatch):
    root = tmp_path / "upload"
    root.mkdir()
    monkeypatch.setattr(loader.tempfile, "mkdtemp", lambda **kwargs: str(root))
    with pytest.raises(TaskSpecError, match="readable tar"):
        loader.load_task_from_tar(b"not an archive")
    assert not root.exists()
