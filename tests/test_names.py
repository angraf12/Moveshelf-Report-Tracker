"""Tests for therapist name normalizing.

The variants below are the real spellings observed on shriners/CHI-Gait on
2026-07-27, not invented ones.
"""
from __future__ import annotations

import pytest

from tracker.names import (
    build_mapping_rows,
    canonical_guess,
    load_therapist_map,
    normalize,
    write_mapping_template,
)

# Observed live, with counts: the first two are the same person.
LIVE_VARIANTS = [
    "Dawson, Renata, MPT",         # 26 sessions
    "Dawson, Renata, PT, MPT",     # 9 sessions
    "Whitfield, Marlo, PT, PCS",       # 18 sessions
    "Marsden, Delia, MPT, PCS",    # 12 sessions
    "Dawson, Renata, PT, MPT",
]


class TestCanonicalGuess:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Dawson, Renata, MPT", "Dawson, Renata"),
            ("Dawson, Renata, PT, MPT", "Dawson, Renata"),
            ("Whitfield, Marlo, PT, PCS", "Whitfield, Marlo"),
            ("Marsden, Delia, MPT, PCS", "Marsden, Delia"),
            ("Marsden, Delia, PT, MPT, PCS", "Marsden, Delia"),
        ],
    )
    def test_strips_credentials_from_live_names(self, raw, expected):
        assert canonical_guess(raw) == expected

    def test_the_two_caudill_spellings_collapse(self):
        # This is the whole point of the module.
        assert canonical_guess("Dawson, Renata, MPT") == canonical_guess(
            "Dawson, Renata, PT, MPT"
        )

    def test_a_name_without_credentials_is_unchanged(self):
        assert canonical_guess("Graf, Adam") == "Graf, Adam"

    def test_credentials_attached_without_a_comma(self):
        assert canonical_guess("Dawson, Renata MPT") == "Dawson, Renata"

    def test_dotted_credentials(self):
        assert canonical_guess("Dawson, Renata, P.T.") == "Dawson, Renata"

    def test_a_name_with_no_comma_passes_through(self):
        assert canonical_guess("Renata Dawson") == "Renata Dawson"

    def test_whitespace_is_collapsed(self):
        assert canonical_guess("  Dawson,   Renata ,  MPT ") == "Dawson, Renata"

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_empty_input_gives_empty_output(self, raw):
        assert canonical_guess(raw) == ""

    def test_a_credential_only_string_is_never_lost(self):
        # Better a strange name than a silently dropped therapist.
        assert canonical_guess("PT") == "PT"

    def test_a_middle_initial_survives(self):
        assert canonical_guess("Smith, John A, PT") == "Smith, John A"


class TestNormalize:
    def test_falls_back_to_the_guess_without_a_mapping(self):
        assert normalize("Dawson, Renata, MPT") == "Dawson, Renata"

    def test_an_explicit_mapping_wins_over_the_guess(self):
        mapping = {"Dawson, Renata, MPT": "Renata Dawson"}
        assert normalize("Dawson, Renata, MPT", mapping) == "Renata Dawson"

    def test_an_unmapped_name_still_gets_the_guess(self):
        mapping = {"Someone, Else, PT": "Someone Else"}
        assert normalize("Whitfield, Marlo, PT, PCS", mapping) == "Whitfield, Marlo"

    def test_empty_stays_empty(self):
        assert normalize("", {"a": "b"}) == ""


class TestBuildMappingRows:
    def test_deduplicates_and_prefills_a_guess(self):
        rows = build_mapping_rows(LIVE_VARIANTS)
        assert len(rows) == 4  # the duplicate Dawson spelling appears once
        assert all(r["canonical_name"] for r in rows)

    def test_variants_of_one_person_sort_together(self):
        rows = build_mapping_rows(LIVE_VARIANTS)
        canonical = [r["canonical_name"] for r in rows]
        assert canonical == sorted(canonical, key=str.lower)
        caudill = [i for i, c in enumerate(canonical) if c == "Dawson, Renata"]
        assert caudill == [0, 1], "the two Dawson rows must be adjacent"

    def test_blank_names_are_dropped(self):
        assert build_mapping_rows(["", "   ", None, "Graf, Adam"]) == [
            {"raw_name": "Graf, Adam", "canonical_name": "Graf, Adam"}
        ]


class TestWriteAndLoadRoundTrip:
    def test_written_template_loads_back(self, tmp_path):
        path = tmp_path / "therapists.csv"
        assert write_mapping_template(path, LIVE_VARIANTS) is True
        mapping = load_therapist_map(path)
        assert mapping["Dawson, Renata, MPT"] == "Dawson, Renata"
        assert mapping["Dawson, Renata, PT, MPT"] == "Dawson, Renata"

    def test_comment_header_is_not_mistaken_for_the_csv_header(self, tmp_path):
        path = tmp_path / "therapists.csv"
        write_mapping_template(path, LIVE_VARIANTS)
        assert path.read_text(encoding="utf-8").startswith("#")
        assert len(load_therapist_map(path)) == 4

    def test_an_existing_file_is_never_overwritten(self, tmp_path):
        path = tmp_path / "therapists.csv"
        path.write_text(
            "raw_name,canonical_name\nDawson, Renata, MPT,Mine\n", encoding="utf-8"
        )
        assert write_mapping_template(path, LIVE_VARIANTS) is False
        assert "Mine" in path.read_text(encoding="utf-8")

    def test_no_file_written_when_there_are_no_names(self, tmp_path):
        path = tmp_path / "therapists.csv"
        assert write_mapping_template(path, []) is False
        assert not path.exists()


class TestLoadTherapistMap:
    def test_blank_canonical_means_pass_through(self, tmp_path):
        path = tmp_path / "t.csv"
        path.write_text(
            "raw_name,canonical_name\n\"Dawson, Renata, MPT\",\n", encoding="utf-8"
        )
        mapping = load_therapist_map(path)
        assert mapping == {}
        assert normalize("Dawson, Renata, MPT", mapping) == "Dawson, Renata"

    def test_missing_file_gives_an_empty_mapping(self, tmp_path):
        assert load_therapist_map(tmp_path / "nope.csv") == {}

    def test_none_path_is_allowed(self):
        assert load_therapist_map(None) == {}

    def test_a_file_with_the_wrong_columns_does_not_raise(self, tmp_path):
        path = tmp_path / "t.csv"
        path.write_text("foo,bar\n1,2\n", encoding="utf-8")
        assert load_therapist_map(path) == {}
