import argparse
import os
import re
import sys
from pathlib import Path
from socket import timeout as SocketTimeout

from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)
from nornir import InitNornir
from nornir.core.task import Result, Task
from nornir_netmiko.tasks import netmiko_send_command
from paramiko.ssh_exception import (
    AuthenticationException,
    NoValidConnectionsError,
    SSHException,
)


WORKSPACE_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_NORNIR_DIRECTORY = WORKSPACE_DIRECTORY / "aos-3-tier"

AUTHENTICATION_ERRORS = (NetmikoAuthenticationException, AuthenticationException)
TIMEOUT_ERRORS = (NetmikoTimeoutException, SocketTimeout, TimeoutError)
CONNECTION_ERRORS = (NoValidConnectionsError, ConnectionError, EOFError, OSError)


def valid_suffix(value: str) -> str:
    """Validate a suffix before using it as part of an output filename."""
    suffix = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", suffix):
        raise argparse.ArgumentTypeError(
            "suffix must be 1-64 characters and contain only letters, "
            "numbers, periods, underscores, or hyphens"
        )
    return suffix


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run commands from a text file on every Nornir inventory host."
    )
    parser.add_argument(
        "command_file",
        type=Path,
        help="UTF-8 text file containing one command per line",
    )
    parser.add_argument(
        "-s",
        "--suffix",
        type=valid_suffix,
        default=None,
        help="suffix added to output filenames, such as 'ospf'",
    )
    parser.add_argument(
        "-d",
        "--directory",
        type=Path,
        default=DEFAULT_NORNIR_DIRECTORY,
        help=(
            "directory containing Nornir config.yaml and inventory/ "
            "(default: aos-3-tier)"
        ),
    )
    return parser.parse_args()


def resolve_nornir_directory(directory: Path) -> Path:
    """Resolve a Nornir directory from the current directory or workspace root."""
    nornir_directory = directory.expanduser()
    if nornir_directory.is_absolute():
        return nornir_directory.resolve()

    current_directory = (Path.cwd() / nornir_directory).resolve()
    if current_directory.exists():
        return current_directory

    workspace_directory = (WORKSPACE_DIRECTORY / nornir_directory).resolve()
    if workspace_directory.exists():
        return workspace_directory

    return current_directory


def resolve_command_file(filename: Path, nornir_directory: Path) -> Path:
    """Resolve command files from the current directory or Nornir directory."""
    command_file = filename.expanduser()
    if command_file.is_absolute():
        return command_file

    current_directory_file = (Path.cwd() / command_file).resolve()
    if current_directory_file.exists():
        return current_directory_file

    nornir_directory_file = (nornir_directory / command_file).resolve()
    if nornir_directory_file.exists():
        return nornir_directory_file

    return current_directory_file


def load_commands(filename: Path) -> list[str]:
    """Load nonblank, noncomment commands from a UTF-8 text file."""
    try:
        lines = filename.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"command file is not valid UTF-8: {filename}") from exc

    commands = [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not commands:
        raise ValueError(f"command file contains no commands: {filename}")

    return commands


def describe_error(exception: BaseException) -> str:
    """Return a useful category for file, SSH, and communication failures."""
    current: BaseException | None = exception
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))

        if isinstance(current, (PermissionError, IsADirectoryError)):
            return "File access failed"
        if isinstance(current, AUTHENTICATION_ERRORS):
            return "SSH authentication failed"
        if isinstance(current, TIMEOUT_ERRORS) or type(current).__name__ == "ReadTimeout":
            return "Connection or command timed out"
        if isinstance(current, CONNECTION_ERRORS):
            return "Unable to communicate with the device"
        if isinstance(current, SSHException):
            return "SSH protocol or session error"

        current = current.__cause__ or current.__context__

    return "Command failed"


def get_exception(results) -> BaseException | None:
    """Find the underlying exception in nested Nornir task results."""
    return next(
        (
            result.exception
            for result in reversed(results)
            if result.exception is not None
        ),
        None,
    )


def run_commands_and_save(
    task: Task,
    commands: list[str],
    output_directory: Path,
    filename_suffix: str | None = None,
) -> Result:
    """Run commands and write the results to a per-host output file."""
    host_output = []
    command_error = None

    for command in commands:
        command_result = task.run(
            task=netmiko_send_command,
            command_string=command,
        )
        host_output.append(f"--- Command: {command} ---\n")

        if command_result.failed:
            command_error = get_exception(command_result) or RuntimeError(
                "command failed without error details"
            )
            category = describe_error(command_error)
            details = str(command_error).strip()
            detail_suffix = f": {details}" if details else ""
            host_output.append(f"[ERROR] {category}{detail_suffix}")
            host_output.append("\n" + "=" * 40 + "\n")
            break

        output = "" if command_result.result is None else str(command_result.result)
        host_output.append(output or "[No output returned by device]")
        host_output.append("\n" + "=" * 40 + "\n")

    output_directory.mkdir(parents=True, exist_ok=True)
    suffix = f"_{filename_suffix}" if filename_suffix else ""
    filename = output_directory / f"{task.host.name}_output{suffix}.txt"
    filename.write_text("\n".join(host_output), encoding="utf-8")

    if command_error is not None:
        return Result(
            host=task.host,
            failed=True,
            exception=command_error,
            result=f"Stopped after a command failure; partial output saved to {filename}",
        )

    return Result(
        host=task.host,
        result=f"Successfully saved all command outputs to {filename}",
    )


def report_results(results) -> None:
    """Print either success or a useful error for every inventory host."""
    for host, host_results in sorted(results.items()):
        if not host_results.failed:
            print(f"OK {host}: {host_results.result}")
            continue

        exception = get_exception(host_results)
        if exception is None:
            print(f"ERROR {host}: task failed without error details", file=sys.stderr)
            continue

        category = describe_error(exception)
        details = str(exception).strip()
        suffix = f" ({details})" if details else ""
        print(f"ERROR {host}: {category}{suffix}", file=sys.stderr)


def main() -> int:
    args = parse_arguments()
    nornir_directory = resolve_nornir_directory(args.directory)
    config_file = nornir_directory / "config.yaml"
    output_directory = nornir_directory / "outputs"
    command_file = resolve_command_file(args.command_file, nornir_directory)
    nr = None

    try:
        commands = load_commands(command_file)

        # Inventory paths in config.yaml are relative to the Nornir directory.
        os.chdir(nornir_directory)
        nr = InitNornir(config_file=str(config_file))
        results = nr.run(
            task=run_commands_and_save,
            commands=commands,
            output_directory=output_directory,
            filename_suffix=args.suffix,
        )

        if not results:
            print("No hosts were found in the Nornir inventory.", file=sys.stderr)
            return 2

        report_results(results)
        return 1 if results.failed else 0
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(f"File not found: {exc.filename or exc}", file=sys.stderr)
        return 2
    except NotADirectoryError as exc:
        print(f"Invalid Nornir directory: {exc}", file=sys.stderr)
        return 2
    except (PermissionError, IsADirectoryError, ValueError) as exc:
        print(f"Invalid command file: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        category = describe_error(exc)
        print(f"Unable to run commands: {category}: {exc}", file=sys.stderr)
        return 1
    finally:
        if nr is not None:
            try:
                nr.close_connections()
            except Exception as exc:
                print(f"Warning: could not close all connections: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
