import sys
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


AUTHENTICATION_ERRORS = (NetmikoAuthenticationException, AuthenticationException)
TIMEOUT_ERRORS = (NetmikoTimeoutException, SocketTimeout, TimeoutError)
CONNECTION_ERRORS = (NoValidConnectionsError, ConnectionError, EOFError, OSError)
SHOW_VERSION_COMMAND = "show version"


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


def report_failed_hosts(result) -> None:
    """Print a concise explanation for each failed Nornir host."""
    print(
        f"Error: command failed on {len(result.failed_hosts)} host(s):",
        file=sys.stderr,
    )

    for host, host_results in sorted(result.failed_hosts.items()):
        exception = next(
            (
                task_result.exception
                for task_result in reversed(host_results)
                if task_result.exception is not None
            ),
            None,
        )

        if exception is None:
            print(f"  {host}: task failed without error details", file=sys.stderr)
            continue

        category = describe_error(exception)
        details = str(exception).strip()
        suffix = f" ({details})" if details else ""
        print(f"  {host}: {category}{suffix}", file=sys.stderr)


def report_command_output(result) -> None:
    """Print the command output for every host that completed successfully."""
    for host, host_results in sorted(result.items()):
        task_result = next(
            (
                task_result
                for task_result in reversed(host_results)
                if not task_result.failed
            ),
            None,
        )

        if task_result is None:
            continue

        output = "" if task_result.result is None else str(task_result.result).strip()
        print(f"\n===== {host} =====")
        print(output or "[No output returned by device]")


def main() -> int:
    nr = None

    try:
        nr = InitNornir(config_file="config.yaml")

        result = nr.run(
            task=netmiko_send_command,
            command_string=SHOW_VERSION_COMMAND,
        )

        if not result:
            print("No hosts were found in the Nornir inventory.", file=sys.stderr)
            return 2

        report_command_output(result)

        if result.failed:
            report_failed_hosts(result)
            return 1

        return 0
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(f"Configuration or inventory file not found: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        category = describe_error(exc)
        print(f"Unable to run version check: {category}: {exc}", file=sys.stderr)
        return 1
    finally:
        if nr is not None:
            try:
                nr.close_connections()
            except Exception as exc:
                print(f"Warning: could not close all connections: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
