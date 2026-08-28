"""Best-effort OS-level stdout/stderr suppression.

Some third-party packages (e.g. `sox`, pulled in by `qwen-tts`) print a noisy
"SoX could not be found" banner from a *subprocess* on import. `contextlib.
redirect_stdout` can't catch that. This redirects fds 1 and 2 to os.devnull for
the duration.

It is *best effort*: if the process has no usable stdout/stderr fds (detached
process, pythonw, some service launchers) it simply does nothing rather than
raising — a cosmetic banner is never worth breaking the caller.
"""
from __future__ import annotations

import contextlib
import os
import sys


@contextlib.contextmanager
def suppress_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass

    devnull = saved_out = saved_err = None
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        saved_out = os.dup(1)
        saved_err = os.dup(2)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    except OSError:
        # can't redirect (no valid fds) — clean up and run without suppression
        for fd in (devnull, saved_out, saved_err):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        yield
        return

    try:
        yield
    finally:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        try:
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
        except OSError:
            pass
        for fd in (devnull, saved_out, saved_err):
            try:
                os.close(fd)
            except OSError:
                pass
