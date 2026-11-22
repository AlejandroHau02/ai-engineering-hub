"""Shared JSON contract helpers for SYNCODE command-line tools (stdlib only).

The JSON mode is a local data contract for a future desktop client. It must
produce exactly one JSON object on stdout, independent of the console code page
or locale, and expose stable machine-readable error codes. Text mode is not
changed by this module except where a caller explicitly opts in.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

SCHEMA_VERSION = 1
PRODUCT = "SYNCODE"

# Stable, documented error codes (docs/SYNCODE_CLI_CONTRACT.md). Codes are
# additive: never rename or reuse one with a different meaning.
ERROR_CODES = frozenset({
    "invalid-arguments",
    "unsafe-path",
    "managed-markers-invalid",
    "skill-upgrade-available",
    "skill-modified",
    "destination-conflict",
    "destination-changed",
    "unknown-component",
    "transaction-pending",
    "lock-busy",
    "lock-unexpected",
    "transaction-limit",
    "integrity-mismatch",
    "recovery-blocked",
    "recovery-required",
    "cleanup-failed",
    "approved-skill-rejected",
    "registry-invalid",
    "io-error",
    "encoding-error",
    "operation-failed",
    "interrupted",
    "internal-error",
})


class SyncodeError(ValueError):
    """A ValueError with a stable contract code; the message is unchanged.

    Subclassing ValueError keeps every existing ``except ValueError`` handler
    and text-mode message intact.
    """

    default_code = "operation-failed"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        selected = code or self.default_code
        if selected not in ERROR_CODES:
            raise AssertionError(f"Undocumented SYNCODE error code: {selected}")
        self.code = selected


class ArgumentContractError(Exception):
    """Argument parsing failed while a JSON response was requested."""


# Existing fail-closed error classes in helper modules, mapped by name to avoid
# coupling those modules to the CLI contract.
_CLASS_CODES = {
    "PromotionError": "approved-skill-rejected",
    "RegistryError": "registry-invalid",
}

INTERNAL_ERROR_MESSAGE = (
    "Unexpected internal error; details are not included in JSON output. "
    "Run SYNCODE Doctor before retrying."
)


def error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code in ERROR_CODES:
        return code
    for cls in type(error).__mro__:
        if cls.__name__ in _CLASS_CODES:
            return _CLASS_CODES[cls.__name__]
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, ArgumentContractError):
        return "invalid-arguments"
    if isinstance(error, UnicodeError):  # before OSError/ValueError: UnicodeError is a ValueError
        return "encoding-error"
    if isinstance(error, OSError):
        return "io-error"
    if isinstance(error, ValueError):
        return "operation-failed"
    return "internal-error"


def error_object(error: BaseException) -> dict:
    """Return the public error shape; ``type`` and ``message`` are informational.

    ``message`` is human-readable, may contain local absolute paths and must not
    be parsed or transmitted. Unexpected (non-contract) exceptions never expose
    their text, which could contain arbitrary data.
    """
    code = error_code(error)
    if code == "internal-error":
        return {"type": "InternalError", "code": code, "message": INTERNAL_ERROR_MESSAGE}
    if code == "interrupted":
        return {"type": "KeyboardInterrupt", "code": code, "message": "Operation interrupted"}
    name = type(error).__name__
    if name == "SyncodeError":
        # Preserve the schemaVersion 1 ``type`` value previously emitted for
        # these failures; clients must branch on ``code``, not ``type``.
        name = "ValueError"
    elif name == "ArgumentContractError":
        name = "ArgumentError"
    return {"type": name, "code": code, "message": str(error)}


def wants_json(argv: list[str] | None) -> bool:
    """Detect ``--format json`` before argparse runs (supports prefixes and ``=``)."""
    tokens = list(sys.argv[1:] if argv is None else argv)
    for index, token in enumerate(tokens):
        if token == "--":
            break
        name, sep, value = token.partition("=")
        if len(name) < 3 or not "--format".startswith(name):
            continue
        if sep:
            if value == "json":
                return True
        elif index + 1 < len(tokens) and tokens[index + 1] == "json":
            return True
    return False


class ContractArgumentParser(argparse.ArgumentParser):
    """In JSON mode, raise instead of printing usage and exiting with code 2.

    Text mode keeps argparse's historical behavior (usage on stderr, exit 2).
    """

    def __init__(self, *args, json_errors: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._json_errors = json_errors

    def error(self, message):  # noqa: D401 - argparse API
        if self._json_errors:
            raise ArgumentContractError(message)
        super().error(message)


def emit_json(payload: dict) -> None:
    """Write one JSON object as ASCII-only UTF-8 bytes, regardless of locale.

    ``ensure_ascii`` escapes every non-ASCII character (including unpaired
    surrogates from unusual Windows paths), so encoding can never fail and the
    bytes are identical with any console code page. A closed pipe is ignored
    (Windows may report it as EINVAL): the exit code still reports the
    operation result, and stdout is redirected to devnull so the interpreter
    does not print a second error at shutdown.
    """
    raw = (json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
    if sys.stdout is None:  # e.g. pythonw without a console: nothing to write to
        return
    stream = getattr(sys.stdout, "buffer", None)
    try:
        if stream is not None:
            sys.stdout.flush()
            stream.write(raw)
            stream.flush()
        else:  # e.g. an in-memory text stream in tests
            sys.stdout.write(raw.decode("ascii"))
            sys.stdout.flush()
    except OSError:
        _redirect_to_devnull(sys.stdout, 1)


def _redirect_to_devnull(stream, expected_fd: int) -> None:
    """Point a failed standard stream's descriptor at devnull.

    Data left in its buffer then flushes harmlessly at interpreter shutdown;
    otherwise CPython reports the failed final flush and exits with 120,
    overriding the exit code already decided by the command. Only the
    standard descriptor itself (1 or 2) is ever replaced, so a stream object
    bound to some other, possibly reused descriptor (e.g. the lock file) is
    never clobbered.
    """
    devnull = None
    try:
        if stream.fileno() != expected_fd:
            return
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, expected_fd)
    except (OSError, ValueError, AttributeError):
        pass
    finally:
        if devnull is not None:
            os.close(devnull)


def diagnostic(message: str) -> None:
    """Best-effort human diagnostic on stderr; never raises.

    stderr is outside the contract: a closed, broken or missing stderr must not
    prevent the JSON response on stdout or change the exit code. Never falls
    back to stdout (``print(file=None)`` would corrupt the JSON stream).
    """
    stream = sys.stderr
    if stream is None:
        return
    try:
        stream.write(message + "\n")
        stream.flush()
    except UnicodeError:
        pass  # this message is unencodable; the stream itself is healthy
    except (OSError, ValueError):  # broken pipe / EINVAL / closed file
        _redirect_to_devnull(stream, 2)
    except (AttributeError, TypeError):
        pass  # stderr replaced by a non-stream object


def resolve_home(value: Path | None) -> Path:
    """Resolve ``--home`` (default: user home) with a contract error on failure."""
    try:
        return (value if value is not None else Path.home()).expanduser().resolve()
    except (RuntimeError, KeyError) as error:  # no home / symlink loop (< 3.13)
        raise SyncodeError(f"Cannot resolve home directory ({error}); pass --home",
                           code="io-error") from None


def envelope(status: str, **fields) -> dict:
    return {"schemaVersion": SCHEMA_VERSION, "product": PRODUCT, "status": status, **fields}
