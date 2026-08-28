# -*- coding: utf-8 -*-
"""One long-running job at a time, with progress the browser can subscribe to.

Generation takes ~30 minutes and spends real credit, so the rules are: only one
job runs at once, it can be asked to stop, and every tick is retained for late
subscribers -- a browser that connects mid-run still sees where things stand.
"""

import threading
import time
import traceback
import uuid


class Job(object):
    def __init__(self, kind, label):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.label = label
        self.state = "running"        # running | done | failed | cancelled
        self.progress = None
        self.result = None
        self.error = None
        self.log = []
        self.started = time.time()
        self.finished = None
        self._stop = threading.Event()
        self._version = 0
        self._cv = threading.Condition()

    def stop(self):
        self._stop.set()
        self.say("stop requested; finishing in-flight clips")

    def should_stop(self):
        return self._stop.is_set()

    def _bump(self):
        with self._cv:
            self._version += 1
            self._cv.notify_all()

    def say(self, line):
        self.log.append({"t": round(time.time() - self.started, 1), "line": line})
        del self.log[:-200]
        self._bump()

    def tick(self, snapshot):
        self.progress = snapshot
        self._bump()

    def snapshot(self):
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "state": self.state, "progress": self.progress,
                "result": self.result, "error": self.error,
                "log": self.log[-40:],
                "elapsed": round((self.finished or time.time()) - self.started, 1),
                "version": self._version}

    def wait(self, since, timeout=20.0):
        """Block until something changed past `since`, or the timeout lapses."""
        with self._cv:
            if self._version > since:
                return True
            return self._cv.wait(timeout)


class JobManager(object):
    def __init__(self):
        self.current = None
        self.history = []
        self._lock = threading.Lock()

    def busy(self):
        return self.current is not None and self.current.state == "running"

    def start(self, kind, label, fn):
        """Run fn(job) on a worker thread. Refuses if a job is already running."""
        with self._lock:
            if self.busy():
                raise RuntimeError("a job is already running: %s"
                                   % self.current.label)
            job = Job(kind, label)
            self.current = job

        def target():
            try:
                job.result = fn(job)
                job.state = "cancelled" if job.should_stop() else "done"
            except Exception as exc:            # noqa: BLE001
                job.state = "failed"
                job.error = "%s: %s" % (type(exc).__name__, exc)
                job.say(traceback.format_exc().strip().splitlines()[-1])
            finally:
                job.finished = time.time()
                job._bump()
                self.history.append(job.snapshot())
                del self.history[:-20]

        threading.Thread(target=target, daemon=True).start()
        return job

    def get(self, job_id=None):
        if job_id is None or (self.current and self.current.id == job_id):
            return self.current
        return None
