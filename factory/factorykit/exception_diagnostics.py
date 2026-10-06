"""Bounded local exception locations; never serialize exception or request data."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat

MAX_CHAIN = 4
MAX_FRAMES = 20
MAX_FILE_BYTES = 512 * 1024
SEATS = frozenset({'pm', 'architect', 'designer', 'backend', 'frontend', 'qa', 'reviewer'})
PHASES = frozenset({'client_build', 'adapter_event', 'event_admission', 'event_completion',
                    'request_thread_start', 'request_thread_resume', 'request_turn_start',
                    'request_turn_steer', 'request_other'})
_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*\Z')


def _symbol(value):
    return value if type(value) is str and len(value) <= 128 and _IDENTIFIER.fullmatch(value) else 'unknown'


def exception_locations(error):
    """Return code identities only; do not format errors, frames, or source lines."""
    chain, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(chain) < MAX_CHAIN:
        seen.add(id(error))
        frames, trace = [], error.__traceback__
        while trace is not None and len(frames) < MAX_FRAMES:
            frame = trace.tb_frame
            function = frame.f_code.co_name
            frames.append({'module': _symbol(frame.f_globals.get('__name__')),
                           'function': function if function in {'<module>', '<lambda>', '<listcomp>', '<genexpr>'}
                           else _symbol(function), 'line': trace.tb_lineno})
            trace = trace.tb_next
        chain.append({'type': {'module': _symbol(type(error).__module__), 'name': _symbol(type(error).__name__)},
                      'frames': frames, 'frames_truncated': trace is not None})
        error = error.__cause__ if error.__cause__ is not None else (
            None if error.__suppress_context__ else error.__context__)
    return {'exceptions': chain, 'chain_truncated': error is not None}


def record_exception(directory, error, *, seat, phase):
    """Best-effort, private JSONL; a diagnostic failure never changes execution.

    Callers provide only fixed seat/phase labels. The exception payload consists
    solely of type identities and traceback module/function/line locations.
    """
    try:
        if seat not in SEATS or phase not in PHASES or not isinstance(error, BaseException):
            return False
        directory = Path(directory).absolute()
        if any(path.is_symlink() for path in (directory, *directory.parents)):
            return False
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            return False
        record = {'schema_version': 1, 'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
                  'seat': seat, 'phase': phase, **exception_locations(error)}
        data = json.dumps(record, separators=(',', ':'), sort_keys=True).encode('utf-8') + b'\n'
        destination = directory / 'exceptions.jsonl'
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(destination, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size + len(data) > MAX_FILE_BYTES):
                return False
            # Interrupted/short writes remain local diagnostics only. They never
            # trigger a model/transport retry or replace the original exception.
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    return False
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        folder = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
        return True
    except Exception:
        return False
