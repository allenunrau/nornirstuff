import argparse
import ipaddress
import os
import re
import shlex
import sys
from dataclasses import dataclass
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
CLI_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.:/-]+")
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
CHECK_MARK = "✓"
FAIL_MARK = "x"
PING_STATS_PATTERN = re.compile(
    r"(?P<sent>\d+)\s+packets?\s+transmitted,\s+"
    r"(?P<received>\d+)\s+(?:packets?\s+)?received,\s+"
    r"(?P<loss>\d+(?:\.\d+)?)%\s+packet loss",
    re.IGNORECASE,
)
SUCCESS_RATE_PATTERN = re.compile(
    r"Success rate is\s+(?P<rate>\d+(?:\.\d+)?)\s+percent\s+"
    r"\((?P<received>\d+)/(?P<sent>\d+)\)",
    re.IGNORECASE,
)

AUTHENTICATION_ERRORS = (NetmikoAuthenticationException, AuthenticationException)
TIMEOUT_ERRORS = (NetmikoTimeoutException, SocketTimeout, TimeoutError)
CONNECTION_ERRORS = (NoValidConnectionsError, ConnectionError, EOFError, OSError)


@dataclass(frozen=True)
class IcmpCheck:
    target: str
    command: str
    passed: bool
    summary: str
    output: str
    source: str | None = None


@dataclass(frozen=True)
class SourceRequest:
    host: str
    source: str | None = None


def valid_cli_token(value: str) -> str:
    token = value.strip()
    if not CLI_TOKEN_PATTERN.fullmatch(token):
        raise argparse.ArgumentTypeError(
            "value must contain only letters, numbers, periods, underscores, "
            "hyphens, colons, or slashes"
        )
    return token


def normalize_ping_source(value: str) -> str:
    token = valid_cli_token(value)
    vlan_match = re.fullmatch(r"vlan[:=_-]?(\d+)", token, re.IGNORECASE)
    if vlan_match is not None:
        return normalize_vlan_source(vlan_match.group(1))
    if token.isdecimal():
        return normalize_vlan_source(token)
    return token


def normalize_vlan_source(value: str) -> str:
    return f"vlan{valid_vlan(value)}"


def valid_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("count must be an integer") from exc

    if not 1 <= count <= 10000:
        raise argparse.ArgumentTypeError("count must be between 1 and 10000")
    return count


def valid_timeout(value: str) -> int:
    try:
        timeout = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be an integer") from exc

    if not 1 <= timeout <= 60:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 60 seconds")
    return timeout


def valid_loss_percent(value: str) -> float:
    try:
        percent = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max loss must be a number") from exc

    if not 0 <= percent <= 100:
        raise argparse.ArgumentTypeError("max loss must be between 0 and 100")
    return percent


def valid_vlan(value: str) -> int:
    try:
        vlan_id = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("VLAN must be an integer") from exc

    if not 1 <= vlan_id <= 4094:
        raise argparse.ArgumentTypeError("VLAN must be between 1 and 4094")
    return vlan_id


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ICMP reachability tests from one or more Nornir hosts."
    )
    parser.add_argument(
        "source_host",
        nargs="?",
        type=valid_cli_token,
        help=(
            "inventory host name or inventory hostname/IP address to run pings "
            "from; optional when --source-file is used"
        ),
    )
    parser.add_argument(
        "targets",
        nargs="*",
        type=valid_cli_token,
        help="target IP addresses or FQDNs to ping; optional with --target-file",
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
    parser.add_argument(
        "-c",
        "--count",
        type=valid_count,
        default=5,
        help="ICMP echo requests to send per target (default: 5)",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=valid_timeout,
        default=2,
        help="seconds to wait for each reply (default: 2)",
    )
    parser.add_argument(
        "--max-loss",
        type=valid_loss_percent,
        default=0.0,
        help="maximum allowed packet loss percentage (default: 0)",
    )
    parser.add_argument(
        "--vrf",
        type=valid_cli_token,
        default=None,
        help="VRF to use for each ping",
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        default=None,
        help=(
            "text file containing source hosts with optional interfaces or "
            "VLANs after each host"
        ),
    )
    parser.add_argument(
        "--target-file",
        type=Path,
        default=None,
        help="text file containing target IP addresses or FQDNs, one per line",
    )

    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--source",
        type=valid_cli_token,
        default=None,
        help="source IP address or interface name passed directly to ping",
    )
    source_group.add_argument(
        "--source-interface",
        type=valid_cli_token,
        default=None,
        help="source interface name, such as 1/1/1, loopback0, or vlan10",
    )
    source_group.add_argument(
        "--source-vlan",
        type=valid_vlan,
        default=None,
        help="source VLAN ID, converted to the AOS-CX interface name vlan<ID>",
    )
    parser.add_argument(
        "--show-output",
        choices=("never", "failures", "always"),
        default="failures",
        help="when to print raw ping output (default: failures)",
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


def resolve_input_file(filename: Path, nornir_directory: Path) -> Path:
    """Resolve input files from the current directory or Nornir directory."""
    input_file = filename.expanduser()
    if input_file.is_absolute():
        return input_file

    current_directory_file = (Path.cwd() / input_file).resolve()
    if current_directory_file.exists():
        return current_directory_file

    nornir_directory_file = (nornir_directory / input_file).resolve()
    if nornir_directory_file.exists():
        return nornir_directory_file

    return current_directory_file


def split_value_line(line: str) -> list[str]:
    """Split an input-file line into comma or whitespace separated tokens."""
    return shlex.split(line.replace(",", " "), comments=True)


def load_values(filename: Path, value_name: str) -> list[str]:
    """Load nonblank, noncomment values from a UTF-8 text file."""
    try:
        lines = filename.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{value_name} file is not valid UTF-8: {filename}") from exc

    values = []
    for line_number, line in enumerate(lines, start=1):
        values_on_line = split_value_line(line)
        if not values_on_line:
            continue
        for value in values_on_line:
            try:
                values.append(valid_cli_token(value))
            except argparse.ArgumentTypeError as exc:
                raise ValueError(
                    f"invalid {value_name} on line {line_number} of {filename}: {exc}"
                ) from exc

    if not values:
        raise ValueError(f"{value_name} file contains no values: {filename}")

    return values


def normalize_source_tokens(tokens: list[str], filename: Path, line_number: int) -> list[str]:
    sources = []
    index = 0

    while index < len(tokens):
        token = tokens[index]
        token_lower = token.lower()

        if token_lower in {"interface", "int"}:
            if index + 1 >= len(tokens):
                raise ValueError(
                    f"missing interface after {token!r} on line {line_number} of {filename}"
                )
            try:
                sources.append(normalize_ping_source(tokens[index + 1]))
            except argparse.ArgumentTypeError as exc:
                raise ValueError(
                    f"invalid interface on line {line_number} of {filename}: {exc}"
                ) from exc
            index += 2
            continue

        if token_lower == "vlan":
            if index + 1 >= len(tokens):
                raise ValueError(
                    f"missing VLAN ID after 'vlan' on line {line_number} of {filename}"
                )
            try:
                sources.append(normalize_vlan_source(tokens[index + 1]))
            except argparse.ArgumentTypeError as exc:
                raise ValueError(
                    f"invalid VLAN on line {line_number} of {filename}: {exc}"
                ) from exc
            index += 2
            continue

        try:
            sources.append(normalize_ping_source(token))
        except argparse.ArgumentTypeError as exc:
            raise ValueError(
                f"invalid source on line {line_number} of {filename}: {exc}"
            ) from exc
        index += 1

    return sources


def load_source_requests(
    filename: Path,
    default_source: str | None,
) -> list[SourceRequest]:
    """Load source hosts and optional per-host ping sources from a text file."""
    try:
        lines = filename.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"source file is not valid UTF-8: {filename}") from exc

    source_requests = []
    for line_number, line in enumerate(lines, start=1):
        tokens = split_value_line(line)
        if not tokens:
            continue

        try:
            source_host = valid_cli_token(tokens[0])
        except argparse.ArgumentTypeError as exc:
            raise ValueError(
                f"invalid source host on line {line_number} of {filename}: {exc}"
            ) from exc

        ping_sources = normalize_source_tokens(tokens[1:], filename, line_number)
        if not ping_sources:
            source_requests.append(SourceRequest(host=source_host, source=default_source))
            continue

        for ping_source in ping_sources:
            source_requests.append(SourceRequest(host=source_host, source=ping_source))

    if not source_requests:
        raise ValueError(f"source file contains no values: {filename}")

    return source_requests


def unique_ordered(values: list[str]) -> list[str]:
    """Remove duplicate values while preserving input order."""
    unique_values = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        unique_values.append(value)
        seen.add(value)
    return unique_values


def unique_source_requests(source_requests: list[SourceRequest]) -> list[SourceRequest]:
    """Remove duplicate source requests while preserving input order."""
    unique_requests = []
    seen = set()
    for source_request in source_requests:
        key = (
            source_request.host.lower(),
            None if source_request.source is None else source_request.source.lower(),
        )
        if key in seen:
            continue
        unique_requests.append(source_request)
        seen.add(key)
    return unique_requests


def load_sources_and_targets(
    args: argparse.Namespace,
    nornir_directory: Path,
    default_source: str | None,
) -> tuple[list[SourceRequest], list[str]]:
    source_requests = []
    targets = list(args.targets)

    if (
        args.source_file is not None
        and args.target_file is None
        and args.source_host is not None
    ):
        targets.insert(0, args.source_host)
    elif args.source_host is not None:
        source_requests.append(
            SourceRequest(host=args.source_host, source=default_source)
        )

    if args.source_file is not None:
        source_file = resolve_input_file(args.source_file, nornir_directory)
        source_requests.extend(load_source_requests(source_file, default_source))
    if args.target_file is not None:
        target_file = resolve_input_file(args.target_file, nornir_directory)
        targets.extend(load_values(target_file, "target"))

    source_requests = unique_source_requests(source_requests)
    targets = unique_ordered(targets)

    if not source_requests:
        raise ValueError("at least one source host or --source-file is required")
    if not targets:
        raise ValueError("at least one target or --target-file is required")

    return source_requests, targets


def ping_source_from_args(args: argparse.Namespace) -> str | None:
    if args.source is not None:
        return normalize_ping_source(args.source)
    if args.source_interface is not None:
        return normalize_ping_source(args.source_interface)
    if args.source_vlan is not None:
        return normalize_vlan_source(str(args.source_vlan))
    return None


def is_ipv6_address(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).version == 6
    except ValueError:
        return False


def build_ping_command(
    target: str,
    count: int,
    timeout: int,
    source: str | None = None,
    vrf: str | None = None,
) -> str:
    command = "ping6" if is_ipv6_address(target) else "ping"
    parts = [command, target, "repetitions", str(count), "timeout", str(timeout)]

    if vrf is not None:
        parts.extend(("vrf", vrf))
    if source is not None:
        parts.extend(("source", source))

    return " ".join(parts)


def parse_ping_result(
    target: str,
    command: str,
    output: str,
    max_loss: float,
    source: str | None = None,
) -> IcmpCheck:
    stats_match = PING_STATS_PATTERN.search(output)
    if stats_match is not None:
        sent = int(stats_match.group("sent"))
        received = int(stats_match.group("received"))
        loss = float(stats_match.group("loss"))
        passed = sent > 0 and received > 0 and loss <= max_loss
        return IcmpCheck(
            target=target,
            command=command,
            passed=passed,
            summary=f"{received}/{sent} received, {loss:g}% packet loss",
            output=output,
            source=source,
        )

    success_match = SUCCESS_RATE_PATTERN.search(output)
    if success_match is not None:
        sent = int(success_match.group("sent"))
        received = int(success_match.group("received"))
        loss = 100 - float(success_match.group("rate"))
        passed = sent > 0 and received > 0 and loss <= max_loss
        return IcmpCheck(
            target=target,
            command=command,
            passed=passed,
            summary=f"{received}/{sent} received, {loss:g}% packet loss",
            output=output,
            source=source,
        )

    lowered_output = output.lower()
    if "unknown host" in lowered_output or "name or service not known" in lowered_output:
        summary = "name resolution failed"
    elif "network is unreachable" in lowered_output:
        summary = "network unreachable"
    elif "invalid input" in lowered_output or "ambiguous command" in lowered_output:
        summary = "device rejected the ping command"
    elif not output.strip():
        summary = "no output returned by device"
    else:
        summary = "could not parse ping statistics"

    return IcmpCheck(
        target=target,
        command=command,
        passed=False,
        summary=summary,
        output=output,
        source=source,
    )


def describe_error(exception: BaseException) -> str:
    """Return a useful category for SSH, communication, and command failures."""
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

    return "ICMP test failed"


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


def find_source_host(nr, source_host: str):
    """Find one inventory host matching a name or hostname/IP."""
    source_host_lower = source_host.lower()
    matches = [
        host
        for host in nr.inventory.hosts.values()
        if source_host_lower
        in {
            host.name.lower(),
            str(host.hostname).lower(),
        }
    ]

    if not matches:
        raise ValueError(
            f"source host not found in inventory by name or hostname/IP: {source_host}"
        )
    if len(matches) > 1:
        names = ", ".join(sorted(host.name for host in matches))
        raise ValueError(f"source host matched multiple inventory hosts: {names}")

    selected_host = matches[0]
    return selected_host


def select_source_hosts(nr, source_requests: list[SourceRequest]):
    """Filter Nornir to every requested source host."""
    selected_hosts = {}
    sources_by_host = {}

    for source_request in source_requests:
        selected_host = find_source_host(nr, source_request.host)
        selected_hosts[selected_host.name] = selected_host
        sources_by_host.setdefault(selected_host.name, [])
        if source_request.source not in sources_by_host[selected_host.name]:
            sources_by_host[selected_host.name].append(source_request.source)

    selected_names = set(selected_hosts)
    filtered_nr = nr.filter(filter_func=lambda host: host.name in selected_names)
    return filtered_nr, selected_hosts, sources_by_host


def run_icmp_tests(
    task: Task,
    targets: list[str],
    count: int,
    timeout: int,
    max_loss: float,
    sources_by_host: dict[str, list[str | None]],
    vrf: str | None = None,
) -> Result:
    """Run pings from one device and return per-target checks."""
    checks = []
    ping_sources = sources_by_host.get(task.host.name, [None])
    read_timeout = max(30, count * (timeout + 1) + 10)

    for source in ping_sources:
        for target in targets:
            command = build_ping_command(
                target=target,
                count=count,
                timeout=timeout,
                source=source,
                vrf=vrf,
            )
            command_result = task.run(
                task=netmiko_send_command,
                command_string=command,
                read_timeout=read_timeout,
            )

            if command_result.failed:
                exception = get_exception(command_result) or RuntimeError(
                    "ping command failed without error details"
                )
                checks.append(
                    IcmpCheck(
                        target=target,
                        command=command,
                        passed=False,
                        summary=describe_error(exception),
                        output=str(exception),
                        source=source,
                    )
                )
                continue

            output = "" if command_result.result is None else str(command_result.result)
            checks.append(
                parse_ping_result(
                    target=target,
                    command=command,
                    output=output,
                    max_loss=max_loss,
                    source=source,
                )
            )

    return Result(
        host=task.host,
        failed=any(not check.passed for check in checks),
        result=checks,
    )


def get_checks(host_results) -> list[IcmpCheck]:
    """Return the parent task's ICMP checks from a Nornir host result."""
    for result in reversed(host_results):
        if isinstance(result.result, list) and all(
            isinstance(check, IcmpCheck) for check in result.result
        ):
            return result.result
    return []


def print_raw_output(check: IcmpCheck) -> None:
    print(f"  command: {check.command}")
    if not check.output.strip():
        print("  output: [No output returned by device]")
        return

    print("  output:")
    for line in check.output.rstrip().splitlines():
        print(f"    {line}")


def format_check_label(check: IcmpCheck) -> str:
    if check.source is None:
        return check.target
    return f"{check.source} -> {check.target}"


def format_status(passed: bool) -> str:
    if passed:
        return f"{GREEN}{CHECK_MARK} PASS{RESET}"
    return f"{RED}{FAIL_MARK} FAIL{RESET}"


def report_results(
    results,
    source_hosts,
    show_output: str,
    summary_label: str = "Summary",
) -> tuple[int, int]:
    """Print PASS/FAIL lines and return passed and total check counts."""
    passed_count = 0
    total_count = 0

    for host, host_results in sorted(results.items()):
        checks = get_checks(host_results)
        source_host = source_hosts.get(host)
        source_hostname = source_host.hostname if source_host is not None else host
        print(f"\n===== ICMP tests from {host} ({source_hostname}) =====")

        if not checks:
            exception = get_exception(host_results)
            if exception is None:
                print("ERROR: task failed without error details", file=sys.stderr)
            else:
                category = describe_error(exception)
                details = str(exception).strip()
                suffix = f" ({details})" if details else ""
                print(f"ERROR: {category}{suffix}", file=sys.stderr)
            continue

        for check in checks:
            total_count += 1
            if check.passed:
                passed_count += 1

            status = format_status(check.passed)
            print(f"{status} {format_check_label(check)}: {check.summary}")

            should_show_output = show_output == "always" or (
                show_output == "failures" and not check.passed
            )
            if should_show_output:
                print_raw_output(check)

    print(f"\n{summary_label}: {passed_count}/{total_count} target(s) passed")
    return passed_count, total_count


def print_preflight_summary(selected_source_hosts, sources_by_host, targets: list[str]) -> None:
    """Print planned test counts before any ping commands are sent."""
    source_count = sum(len(sources) for sources in sources_by_host.values())
    target_count = len(targets)

    print("\nPre-flight summary")
    print(f"Source devices: {len(selected_source_hosts)}")
    print(f"Sources: {source_count}")
    print(f"Targets: {target_count}")
    print(f"Planned checks: {source_count * target_count}")
    sys.stdout.flush()


def run_and_report_per_source(
    nr,
    selected_source_hosts,
    sources_by_host,
    targets: list[str],
    count: int,
    timeout: int,
    max_loss: float,
    vrf: str | None,
    show_output: str,
) -> tuple[int, int]:
    """Run each source host separately and print results after each host."""
    passed_count = 0
    total_count = 0

    for source_name in selected_source_hosts:
        source_nr = nr.filter(
            filter_func=lambda host, name=source_name: host.name == name
        )
        results = source_nr.run(
            task=run_icmp_tests,
            targets=targets,
            count=count,
            timeout=timeout,
            max_loss=max_loss,
            sources_by_host=sources_by_host,
            vrf=vrf,
        )
        source_passed, source_total = report_results(
            results=results,
            source_hosts=selected_source_hosts,
            show_output=show_output,
            summary_label="Device summary",
        )
        passed_count += source_passed
        total_count += source_total

    return passed_count, total_count


def main() -> int:
    args = parse_arguments()
    nornir_directory = resolve_nornir_directory(args.directory)
    config_file = nornir_directory / "config.yaml"
    ping_source = ping_source_from_args(args)
    nr = None

    try:
        source_requests, targets = load_sources_and_targets(
            args,
            nornir_directory,
            ping_source,
        )

        # Inventory paths in config.yaml are relative to the Nornir directory.
        os.chdir(nornir_directory)
        nr = InitNornir(config_file=str(config_file))
        _, selected_source_hosts, sources_by_host = select_source_hosts(
            nr,
            source_requests,
        )
        print_preflight_summary(
            selected_source_hosts=selected_source_hosts,
            sources_by_host=sources_by_host,
            targets=targets,
        )
        passed_count, total_count = run_and_report_per_source(
            nr=nr,
            selected_source_hosts=selected_source_hosts,
            sources_by_host=sources_by_host,
            targets=targets,
            count=args.count,
            timeout=args.timeout,
            max_loss=args.max_loss,
            vrf=args.vrf,
            show_output=args.show_output,
        )
        if total_count == 0:
            return 1
        if len(selected_source_hosts) > 1:
            print(f"\nOverall summary: {passed_count}/{total_count} target(s) passed")
        return 0 if passed_count == total_count else 1
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(f"Configuration or inventory file not found: {exc}", file=sys.stderr)
        return 2
    except NotADirectoryError as exc:
        print(f"Invalid Nornir directory: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Invalid ICMP test input: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        category = describe_error(exc)
        print(f"Unable to run ICMP tests: {category}: {exc}", file=sys.stderr)
        return 1
    finally:
        if nr is not None:
            try:
                nr.close_connections(on_good=True, on_failed=True)
            except Exception as exc:
                print(f"Warning: could not close all connections: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
