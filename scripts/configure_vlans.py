# scripts/configure_vlans.py
import os
from pathlib import Path

from nornir import InitNornir
from nornir_netmiko import netmiko_send_config
from nornir_utils.plugins.functions import print_result
from nornir.core.filter import F

WORKSPACE_DIRECTORY = Path(__file__).resolve().parents[1]
PROJECT_DIRECTORY = WORKSPACE_DIRECTORY / "aos-3-tier"
CONFIG_FILE = PROJECT_DIRECTORY / "config.yaml"

# Initialize Nornir with our config file
# Inventory paths in config.yaml are relative to the lab directory.
os.chdir(PROJECT_DIRECTORY)
nr = InitNornir(config_file=str(CONFIG_FILE))

# Filter for only access switches
switches = nr.filter(F(data__role="access_switch"))

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

# Execute the configuration on all devices
results = switches.run(
    task=netmiko_send_config,
    config_commands=vlan_config
)

# Print the results
print_result(results)
