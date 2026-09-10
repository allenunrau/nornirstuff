#
# configure_vlans.py
#
# Adds VLAN 100, VLAN 200, and VLAN 300 to Nornir inventory hosts whose
# host data role is "access_switch". The script uses the lab Nornir config at
# aos-3-tier/config.yaml and sends the configured VLAN commands with Netmiko.
#
# CLI usage:
#   python scripts/configure_vlans.py [-d NORNIR_DIRECTORY]
#
# Options:
#   -d, --directory NORNIR_DIRECTORY
#       Directory containing config.yaml and inventory/. Defaults to aos-3-tier.
#       Edit vlan_config in this file to change the VLANs that are pushed.
#
import argparse
import os
import sys
from pathlib import Path

from nornir import InitNornir
from nornir.core.filter import F
from nornir_netmiko import netmiko_send_config
from nornir_utils.plugins.functions import print_result

WORKSPACE_DIRECTORY = Path(__file__).resolve().parents[1]
DEFAULT_NORNIR_DIRECTORY = WORKSPACE_DIRECTORY / "aos-3-tier"

# Define the configuration commands
vlan_config = [
    "vlan 100",
    "name Engineering",
    "vlan 200",
    "name Sales",
    "vlan 300",
    "name Management",
    "exit"
]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure standard VLANs on access switches."
    )
    parser.add_argument(
        "-d",
        "--directory",
        type=Path,
        default=DEFAULT_NORNIR_DIRECTORY,
        help=(
            "directory containing config.yaml and inventory/ "
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


def main() -> int:
    args = parse_arguments()
    nornir_directory = resolve_nornir_directory(args.directory)
    config_file = nornir_directory / "config.yaml"
    nr = None

    try:
        # Inventory paths in config.yaml are relative to the Nornir directory.
        os.chdir(nornir_directory)
        nr = InitNornir(config_file=str(config_file))
        switches = nr.filter(F(data__role="access_switch"))
        results = switches.run(
            task=netmiko_send_config,
            config_commands=vlan_config,
        )
        print_result(results)
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
    finally:
        if nr is not None:
            try:
                nr.close_connections()
            except Exception as exc:
                print(f"Warning: could not close all connections: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
