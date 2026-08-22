"""Windows code signing diagnostics.

Provides structured detection of signtool availability, certificate
requirement gating, signing command construction and post-sign verification
without ever reading secrets or deleting the unsigned artifact.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"

# Candidate well-known Windows SDK locations (no I/O beyond existence check).
_SDK_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe"),
    Path(r"C:\Program Files (x86)\Windows Kits\10\bin\x86\signtool.exe"),
    Path(r"C:\Program Files\Microsoft SDKs\Windows\v7.1\Bin\signtool.exe"),
]


@dataclasses.dataclass(frozen=True, slots=True)
class SigningResult:
    """Structured outcome of a signing attempt."""

    status: str  # "unsigned" | "signed" | "failed"
    reason: str
    target: str | None = None
    signtool: str | None = None
    certificate: str | None = None
    verify_ok: bool | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "status": self.status,
            "reason": self.reason,
        }
        if self.target is not None:
            data["target"] = self.target
        if self.signtool is not None:
            data["signtool"] = self.signtool
        if self.certificate is not None:
            data["certificate"] = self.certificate
        if self.verify_ok is not None:
            data["verify_ok"] = self.verify_ok
        return data


def find_signtool() -> Path | None:
    """Locate ``signtool.exe`` without invoking it.

    Search order:
    1. ``SHANGBACKGROUND_SIGNTOOL`` env var (explicit override)
    2. ``signtool.exe`` on PATH
    3. ``signtool`` on PATH (POSIX alias for tests)
    4. Well-known Windows SDK locations

    Never reads certificate material or secrets.
    """
    override = os.environ.get("SHANGBACKGROUND_SIGNTOOL")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate
        # If override is a bare name, try which.
        which = shutil.which(override)
        if which:
            return Path(which)
    for name in ("signtool.exe", "signtool"):
        which = shutil.which(name)
        if which:
            return Path(which)
    for candidate in _SDK_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def run_signtool(args: list[str] | tuple[str, ...], *, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """Execute signtool and return the completed process.

    Separated for testability – tests monkeypatch this symbol to avoid
    invoking a real signing tool.
    """
    return subprocess.run(
        list(args),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def build_sign_command(
    signtool: Path | str,
    target: Path | str,
    certificate: str | Path,
    timestamp_url: str | None = None,
) -> list[str]:
    """Construct the Authenticode signing command."""
    cmd: list[str] = [
        os.fspath(signtool),
        "sign",
        "/fd",
        "SHA256",
        "/f",
        os.fspath(certificate),
        "/tr",
        timestamp_url or DEFAULT_TIMESTAMP_URL,
        "/td",
        "SHA256",
    ]
    cmd.append(os.fspath(target))
    return cmd


def build_verify_command(signtool: Path | str, target: Path | str) -> list[str]:
    """Construct the post-sign verification command."""
    return [os.fspath(signtool), "verify", "/pa", "/all", os.fspath(target)]


def sign_and_verify(
    target: Path | str,
    *,
    certificate: str | Path | None = None,
    timestamp_url: str | None = None,
    signtool: Path | str | None = None,
    verify: bool = True,
) -> SigningResult:
    """Attempt to sign ``target`` and verify the signature.

    Returns a structured :class:`SigningResult` with ``status`` in
    ``{"unsigned", "signed", "failed"}``. The function never deletes the
    unsigned artifact and never reads secret material from disk.

    Precedence (required for test contract):
    1. Missing signtool -> unsigned (reason contains "signtool")
    2. Missing certificate -> unsigned without invoking signtool
    3. Signing invocation failure -> failed
    4. Verification failure -> failed
    5. Success -> signed
    """
    target_path = Path(target)
    target_str = os.fspath(target_path)

    # Resolve tool (explicit override or auto-discovery).
    resolved_tool: Path | None
    if signtool is not None:
        resolved_tool = Path(signtool)
        # Allow bare name that may not exist on filesystem in tests – treat as found
        # if caller explicitly provided it. Only treat as missing when None.
        if not resolved_tool.is_file() and not shutil.which(os.fspath(resolved_tool)):
            # If caller gave a fake path for tests (e.g. C:/fake/signtool.exe) we
            # still consider it "found" so tests can exercise success paths without
            # real filesystem access. Only return unsigned when discovery yields None.
            # For explicit signtool values we do NOT probe existence strictly;
            # we let the subsequent run_signtool mock decide outcome.
            pass
    else:
        resolved_tool = find_signtool()

    if resolved_tool is None:
        return SigningResult(
            status="unsigned",
            reason="signtool not found on PATH – install Windows SDK or set SHANGBACKGROUND_SIGNTOOL",
            target=target_str,
            signtool=None,
            certificate=str(certificate) if certificate else None,
            verify_ok=None,
        )

    tool_str = os.fspath(resolved_tool)

    # Certificate gating – must not invoke tool when missing (test contract).
    if not certificate:
        return SigningResult(
            status="unsigned",
            reason="certificate is required for signing – provide --certificate or configure signing certificate",
            target=target_str,
            signtool=tool_str,
            certificate=None,
            verify_ok=None,
        )

    # Timestamp service is optional; if caller passes an explicit empty string,
    # treat that as a missing prerequisite and report unsigned per plan note.
    if timestamp_url is not None and not str(timestamp_url).strip():
        return SigningResult(
            status="unsigned",
            reason="timestamp service URL is required – provide --timestamp-url or set a valid timestamp server",
            target=target_str,
            signtool=tool_str,
            certificate=str(certificate),
            verify_ok=None,
        )

    # Do not require target existence for unsigned cases – those already returned.
    # For signing attempts, report failed if file is absent, but never delete it.
    if not target_path.is_file():
        return SigningResult(
            status="failed",
            reason=f"target not found: {target_str}",
            target=target_str,
            signtool=tool_str,
            certificate=str(certificate),
            verify_ok=None,
        )

    effective_timestamp = timestamp_url or DEFAULT_TIMESTAMP_URL
    sign_cmd = build_sign_command(resolved_tool, target_path, certificate, effective_timestamp)
    try:
        sign_proc = run_signtool(sign_cmd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SigningResult(
            status="failed",
            reason=f"signtool sign invocation failed: {exc}",
            target=target_str,
            signtool=tool_str,
            certificate=str(certificate),
            verify_ok=None,
        )

    if sign_proc.returncode != 0:
        detail = (sign_proc.stderr or sign_proc.stdout or f"exit code {sign_proc.returncode}").strip()
        return SigningResult(
            status="failed",
            reason=f"signtool sign failed: {detail}",
            target=target_str,
            signtool=tool_str,
            certificate=str(certificate),
            verify_ok=None,
        )

    if verify:
        verify_cmd = build_verify_command(resolved_tool, target_path)
        try:
            verify_proc = run_signtool(verify_cmd)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return SigningResult(
                status="failed",
                reason=f"signtool verify invocation failed: {exc}",
                target=target_str,
                signtool=tool_str,
                certificate=str(certificate),
                verify_ok=False,
            )
        if verify_proc.returncode != 0:
            detail = (verify_proc.stderr or verify_proc.stdout or f"exit code {verify_proc.returncode}").strip()
            return SigningResult(
                status="failed",
                reason=f"signtool verify failed: {detail}",
                target=target_str,
                signtool=tool_str,
                certificate=str(certificate),
                verify_ok=False,
            )
        return SigningResult(
            status="signed",
            reason="signed and verified",
            target=target_str,
            signtool=tool_str,
            certificate=str(certificate),
            verify_ok=True,
        )

    return SigningResult(
        status="signed",
        reason="signed",
        target=target_str,
        signtool=tool_str,
        certificate=str(certificate),
        verify_ok=None,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signing", description="Windows signing diagnostics")
    subparsers = parser.add_subparsers(dest="command")

    # Shared arguments
    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--input", "--target", dest="target", type=Path, default=None, help="Target executable to inspect or sign")
        sp.add_argument("--certificate", type=str, default=None, help="Signing certificate (PFX) path – never reads secret content")
        sp.add_argument("--timestamp-url", type=str, default=None, help=f"RFC3161 timestamp server (default: {DEFAULT_TIMESTAMP_URL})")
        sp.add_argument("--signtool", type=str, default=None, help="Explicit signtool.exe path (overrides discovery)")
        sp.add_argument("--json", action="store_true", help="Emit JSON result")

    check_p = subparsers.add_parser("check", help="Diagnose signing prerequisites without invoking signtool when prerequisites are missing")
    add_common(check_p)

    sign_p = subparsers.add_parser("sign", help="Attempt to sign and verify (still reports unsigned when prerequisites are missing)")
    add_common(sign_p)
    sign_p.add_argument("--no-verify", action="store_true", help="Skip post-sign verification")

    # Backwards compat: if no subcommand, behave like check with same flags
    add_common(parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Determine effective command and parameters
    command = getattr(args, "command", None)
    target: Path | None = getattr(args, "target", None)
    certificate = getattr(args, "certificate", None)
    # Allow env fallback for certificate without reading its content
    if certificate is None:
        certificate = os.environ.get("SHANGBACKGROUND_CERTIFICATE") or os.environ.get("CSC_LINK")

    timestamp_url = getattr(args, "timestamp_url", None)
    signtool_override = getattr(args, "signtool", None)
    as_json = bool(getattr(args, "json", False))
    no_verify = bool(getattr(args, "no_verify", False))

    # When no target is supplied, perform environment-only diagnostics.
    if target is None:
        # Synthesize a placeholder target for diagnostics purposes.
        # sign_and_verify will still correctly report missing signtool/certificate
        # with status=unsigned, without touching filesystem or secrets.
        placeholder = Path("ShangBackground.exe")
        result = sign_and_verify(
            placeholder,
            certificate=certificate,
            timestamp_url=timestamp_url,
            signtool=signtool_override,
            verify=False,
        )
        # If the only reason is placeholder missing, override to unsigned diagnostics
        if result.status == "failed" and "target not found" in result.reason:
            # Re-evaluate with placeholder that won't fail on file check – just diagnostics
            tool = Path(signtool_override) if signtool_override else find_signtool()
            if tool is None:
                result = SigningResult(
                    status="unsigned",
                    reason="signtool not found on PATH – install Windows SDK or set SHANGBACKGROUND_SIGNTOOL",
                    target=None,
                    signtool=None,
                    certificate=str(certificate) if certificate else None,
                )
            elif not certificate:
                result = SigningResult(
                    status="unsigned",
                    reason="certificate is required for signing – provide --certificate or configure signing certificate",
                    target=None,
                    signtool=os.fspath(tool),
                    certificate=None,
                )
            else:
                # Tool and cert present but no input – report ready
                result = SigningResult(
                    status="unsigned",
                    reason="no input file specified – signing prerequisites are satisfied (provide --input to sign)",
                    target=None,
                    signtool=os.fspath(tool),
                    certificate=str(certificate),
                )
    else:
        result = sign_and_verify(
            target,
            certificate=certificate,
            timestamp_url=timestamp_url,
            signtool=signtool_override,
            verify=not no_verify if command == "sign" else True,
        )
        # For `check` command, when target exists but prerequisites missing we already have unsigned.
        # `check` should never claim signed without verification, which sign_and_verify already ensures.

    # Output
    if as_json or command in ("check", "sign") or target is not None:
        payload = result.to_dict()
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    else:
        # Human-readable fallback
        print(f"status={result.status} reason={result.reason}")

    # Exit code: 0 for diagnostics success (including unsigned), 0 for signed,
    # 2 for failed verification – but never claim signed when unsigned.
    # To make CI detection easy, return 0 always for unsigned (diagnostics ok),
    # 1 for failed, 0 for signed.
    if result.status == "failed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
