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


COMMAND = "show ip interface brief | i up"

AUTHENTICATION_ERRORS = (NetmikoAuthenticationException, AuthenticationException)
TIMEOUT_ERRORS = (NetmikoTimeoutException, SocketTimeout, TimeoutError)
CONNECTION_ERRORS = (NoValidConnectionsError, ConnectionError, EOFError, OSError)


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
    nr = None

    try:
        nr = InitNornir(config_file="config.yaml")
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
