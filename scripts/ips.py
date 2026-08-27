import argparse
import os
import sys
from pathlib import Path
from socket import timeout as SocketTimeout

from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)
from nornir import InitNornir
from nornir_netmiko import netmiko_send_command
from paramiko.ssh_exception import (
    AuthenticationException,
    NoValidConnectionsError,
    SSHException,
)


COMMAND = "show ip interface brief | i up"
WORKSPACE_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_NORNIR_DIRECTORY = WORKSPACE_DIRECTORY / "aos-3-tier"

AUTHENTICATION_ERRORS = (NetmikoAuthenticationException, AuthenticationException)
TIMEOUT_ERRORS = (NetmikoTimeoutException, SocketTimeout, TimeoutError)
CONNECTION_ERRORS = (NoValidConnectionsError, ConnectionError, EOFError, OSError)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show up IP interfaces for every Nornir inventory host."
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


def describe_error(exception: BaseException) -> str:
    """Return a useful category for Netmiko/Paramiko failures."""
    current: BaseException | None = exception
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))

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


def report_results(results) -> None:
    """Print command output or a useful error for every host."""
    for host, host_results in sorted(results.items()):
        exception = next(
            (
                task_result.exception
                for task_result in reversed(host_results)
                if task_result.exception is not None
            ),
            None,
        )

        if exception is not None:
            category = describe_error(exception)
            details = str(exception).strip()
            suffix = f" ({details})" if details else ""
            print(f"ERROR {host}: {category}{suffix}", file=sys.stderr)
            continue

        task_result = next(
            (
                task_result
                for task_result in reversed(host_results)
                if not task_result.failed
            ),
            None,
        )
        output = (
            ""
            if task_result is None or task_result.result is None
            else str(task_result.result).strip()
        )

        print(f"\n===== {host} =====")
        print(output or "[No output returned by device]")


def main() -> int:
    args = parse_arguments()
    nornir_directory = resolve_nornir_directory(args.directory)
    config_file = nornir_directory / "config.yaml"
    nr = None

    try:
        # Inventory paths in config.yaml are relative to the Nornir directory.
        os.chdir(nornir_directory)
        nr = InitNornir(config_file=str(config_file))
        results = nr.run(
            task=netmiko_send_command,
            command_string=COMMAND,
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
        print(f"Configuration or inventory file not found: {exc}", file=sys.stderr)
        return 2
    except NotADirectoryError as exc:
        print(f"Invalid Nornir directory: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        category = describe_error(exc)
        print(f"Unable to check interface IPs: {category}: {exc}", file=sys.stderr)
        return 1
    finally:
        if nr is not None:
            try:
                nr.close_connections()
            except Exception as exc:
                print(f"Warning: could not close all connections: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
