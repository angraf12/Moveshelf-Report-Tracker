"""Checks on the folder that gets handed out.

Only the stray-file detection is covered here. The rest of ``check_outputs``
inspects a real built guide and executable, which belong to a release run rather
than to the test suite.

The staging folder is monkeypatched to a temporary directory, so these never
touch the maintainer's own. Each test asserts on the one problem it cares about
and ignores the "missing from the staging folder" complaints that an otherwise
empty directory naturally produces.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

import release


@pytest.fixture
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(release, "STAGING", tmp_path)
    return tmp_path


def problems_about(staging: Path, needle: str) -> List[str]:
    return [p for p in release.check_outputs() if needle in p]


class TestStagingFolderIsDerived:
    def test_no_maintainer_path_is_hardcoded(self):
        # A literal home directory here would be published to a public repo and
        # would silently create a junk folder for anyone else who ran this.
        source = Path(release.__file__).read_text(encoding="utf-8")
        assert "Users\\agraf" not in source
        assert "Users/agraf" not in source

    def test_the_environment_variable_wins(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REPORT_TRACKER_STAGING", str(Path("D:/elsewhere")))
        import importlib

        reloaded = importlib.reload(release)
        try:
            assert reloaded.STAGING == Path("D:/elsewhere")
        finally:
            monkeypatch.delenv("REPORT_TRACKER_STAGING")
            importlib.reload(release)


class TestExportedWorklistIsCaught:
    """An export is the only file here that would be a reportable disclosure."""

    def test_an_exported_csv_is_flagged_as_patient_data(self, staging: Path):
        (staging / "report-tracker-CHI-Gait-2026-08-18.csv").write_text(
            "Status,Subject ID,MRN\n", encoding="utf-8"
        )
        found = problems_about(staging, "PATIENT DATA")
        assert len(found) == 1
        assert "report-tracker-CHI-Gait-2026-08-18.csv" in found[0]

    @pytest.mark.parametrize("name", ["worklist.xlsx", "access.jsonl", "SAVED.CSV"])
    def test_other_data_shapes_are_flagged_too(self, staging: Path, name: str):
        (staging / name).write_text("x", encoding="utf-8")
        assert problems_about(staging, "PATIENT DATA")

    def test_the_handed_out_files_are_not_flagged(self, staging: Path):
        for name in release.EXPECTED:
            (staging / name).write_text("x", encoding="utf-8")
        assert not problems_about(staging, "PATIENT DATA")
        assert not problems_about(staging, "CREDENTIAL")

    def test_therapists_csv_is_called_staff_data_not_patient_data(self, staging: Path):
        # It is a name mapping, not a worklist, and saying otherwise would train
        # the reader to discount the message that matters.
        (staging / "therapists.csv").write_text("raw_name,canonical_name\n",
                                                encoding="utf-8")
        assert not problems_about(staging, "PATIENT DATA")
        assert problems_about(staging, "personal runtime file")


class TestCredentialsAndRuntimeFiles:
    """Unchanged behaviour, pinned so the new check cannot displace it."""

    @pytest.mark.parametrize(
        "name", ["mvshlf-api-key.json", "api_key.txt", "api_key.dat", "secret.key"]
    )
    def test_a_credential_is_flagged(self, staging: Path, name: str):
        (staging / name).write_text("x", encoding="utf-8")
        assert problems_about(staging, "CREDENTIAL")

    @pytest.mark.parametrize("name", ["settings.json", "logs"])
    def test_a_runtime_file_is_flagged(self, staging: Path, name: str):
        target = staging / name
        if name == "logs":
            target.mkdir()
        else:
            target.write_text("{}", encoding="utf-8")
        assert problems_about(staging, "personal runtime file")
