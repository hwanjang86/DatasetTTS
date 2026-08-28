# -*- coding: utf-8 -*-
"""python -m webapp  ->  http://127.0.0.1:8765"""

import argparse
import os
import sys

import uvicorn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    p = argparse.ArgumentParser(prog="webapp")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1",
                   help="loopback by default; this server has no auth and "
                        "reads your API key from the environment")
    args = p.parse_args()

    # flush explicitly: stdout is block-buffered when it is not a terminal, so
    # a redirected or captured launch would otherwise show nothing at all until
    # the process exits.
    if os.environ.get("ELEVENLABS_API_KEY"):
        print("API key   : found", flush=True)
    else:
        print("API key   : MISSING -- browsing and the audio tools still work, "
              "but generate and retry will fail.", flush=True)
    print("DatasetTTS: http://%s:%d" % (args.host, args.port), flush=True)
    print("stop with : Ctrl+C", flush=True)
    uvicorn.run("webapp.server:app", host=args.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
