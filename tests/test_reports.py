# -*- coding: utf-8 -*-
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_batch.runner import _merge_csv  # noqa: E402

HEADER = ["wav", "flags", "duration", "tail_silence", "text"]


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return [l.rstrip("\n") for l in fh]


def test_fixed_clip_is_removed_from_the_report(tmp_path):
    p = str(tmp_path / "qc_flags.csv")
    _merge_csv(p, HEADER, set(), [["0041.wav", "trailing_artifact", 3.12, 0.0, "x"]])
    assert any("0041.wav" in l for l in _read(p))

    # regenerated clean: it was touched, and contributes no new row
    _merge_csv(p, HEADER, {"0041.wav"}, [])
    assert not os.path.exists(p)


def test_still_broken_clip_stays_listed_once(tmp_path):
    p = str(tmp_path / "qc_flags.csv")
    row = ["0041.wav", "trailing_artifact", 3.12, 0.0, "x"]
    _merge_csv(p, HEADER, set(), [row])
    _merge_csv(p, HEADER, {"0041.wav"}, [row])
    lines = _read(p)
    assert len(lines) == 2                      # header + one row, not two
    assert sum("0041.wav" in l for l in lines) == 1


def test_untouched_rows_survive(tmp_path):
    p = str(tmp_path / "qc_flags.csv")
    _merge_csv(p, HEADER, set(),
               [["0041.wav", "trailing_artifact", 3.1, 0.0, "a"],
                ["0099.wav", "clipped", 2.0, 0.1, "b"]])
    _merge_csv(p, HEADER, {"0041.wav"}, [])
    lines = _read(p)
    assert any("0099.wav" in l for l in lines)
    assert not any("0041.wav" in l for l in lines)


def test_rows_are_sorted(tmp_path):
    p = str(tmp_path / "failures.csv")
    _merge_csv(p, ["wav", "text", "error"], set(),
               [["0300.wav", "c", "e"], ["0100.wav", "a", "e"],
                ["0200.wav", "b", "e"]])
    lines = _read(p)[1:]
    assert [l.split(",")[0] for l in lines] == ["0100.wav", "0200.wav", "0300.wav"]
