"""BatchFileOpWorker "delete" mode removes files permanently (no Recycle Bin)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rawviewer_app.services.batch import BatchFileOpWorker  # noqa: E402


def test_delete_mode_removes_files_and_reports_missing(tmp_path: Path) -> None:
    a = tmp_path / "a.jpg"
    a.write_bytes(b"x")
    missing = tmp_path / "gone.jpg"
    worker = BatchFileOpWorker([str(a), str(missing)], mode="delete")
    results: list[tuple[int, int, bool, object]] = []
    worker.signals.finished.connect(lambda *args: results.append(args))
    worker.run()
    assert not os.path.exists(a)
    completed, failed, cancelled, detail = results[0]
    assert (completed, failed, cancelled) == (1, 1, False)
    assert detail[1][1]  # error recorded for the missing file
