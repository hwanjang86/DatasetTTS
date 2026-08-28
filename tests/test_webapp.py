# -*- coding: utf-8 -*-
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.testclient import TestClient  # noqa: E402

from webapp.jobs import JobManager  # noqa: E402
from webapp.server import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_index_serves(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "DatasetTTS" in r.text


def test_scripts_listing(client):
    r = client.get("/api/scripts")
    assert r.status_code == 200
    d = r.json()
    assert "defaults" in d
    # the filename is the voice name
    for s in d["scripts"]:
        assert s["script"].endswith(".txt")
        assert s["voice"] == s["script"][:-4]


# --- path traversal ---------------------------------------------------------
# Every endpoint takes a script or wav name from the browser. These must never
# reach outside the project, even though the server only listens on loopback.

@pytest.mark.parametrize("bad", [
    "../../../../etc/passwd",
    "..\\..\\windows\\win.ini",
    "/etc/passwd",
    "nonexistent.txt",
    "",
])
def test_script_name_cannot_escape(client, bad):
    r = client.post("/api/preview", json={"script": bad})
    assert r.status_code >= 400


@pytest.mark.parametrize("bad", [
    "../../../manifest.jsonl",
    "..\\..\\metadata.csv",
    "0001.txt",
])
def test_wav_name_cannot_escape(client, bad):
    r = client.get("/api/audio", params={"script": "Closers_Android.txt",
                                         "wav": bad})
    assert r.status_code >= 400


def test_unknown_wav_is_404_not_500(client):
    r = client.get("/api/audio", params={"script": "Closers_Android.txt",
                                         "wav": "9999999.wav"})
    assert r.status_code == 404


# --- protect endpoint -------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "has space", "x" * 20])
def test_protect_rejects_bad_tokens(client, bad):
    r = client.post("/api/protect", json={"token": bad})
    assert r.status_code == 400


def test_protect_is_idempotent_for_known_token(client):
    # 가을 is already protected; the endpoint must not append a duplicate
    before = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tts_batch", "normalize.py"),
        encoding="utf-8").read()
    r = client.post("/api/protect", json={"token": "가을"})
    assert r.status_code == 200
    assert r.json().get("already") is True
    after = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tts_batch", "normalize.py"),
        encoding="utf-8").read()
    assert before == after


# --- job manager ------------------------------------------------------------

def test_only_one_job_at_a_time():
    jm = JobManager()
    gate = [False]

    def slow(job):
        while not gate[0]:
            time.sleep(0.01)
        return "ok"

    jm.start("test", "first", slow)
    with pytest.raises(RuntimeError):
        jm.start("test", "second", slow)
    gate[0] = True
    for _ in range(200):
        if jm.current.state != "running":
            break
        time.sleep(0.01)
    assert jm.current.state == "done"
    # once finished, a new job is allowed
    jm.start("test", "third", lambda job: "ok")


def test_failure_is_captured_not_raised():
    jm = JobManager()

    def boom(job):
        raise ValueError("nope")

    jm.start("test", "boom", boom)
    for _ in range(200):
        if jm.current.state != "running":
            break
        time.sleep(0.01)
    assert jm.current.state == "failed"
    assert "nope" in jm.current.error


def test_cancellation_is_reported():
    jm = JobManager()

    def loop(job):
        while not job.should_stop():
            time.sleep(0.01)
        return {"stopped": True}

    job = jm.start("test", "cancel me", loop)
    time.sleep(0.05)
    job.stop()
    for _ in range(200):
        if jm.current.state != "running":
            break
        time.sleep(0.01)
    assert jm.current.state == "cancelled"


def test_snapshot_version_advances_on_change():
    jm = JobManager()
    job = jm.start("test", "ticker", lambda j: (j.say("a"), j.say("b"), "done")[-1])
    for _ in range(200):
        if jm.current.state != "running":
            break
        time.sleep(0.01)
    assert job.snapshot()["version"] >= 2
