# scripts/configure_vlans.py
from nornir import InitNornir
from nornir_netmiko import netmiko_send_config
from nornir_utils.plugins.functions import print_result
from nornir.core.filter import F

# Initialize Nornir with our config file
nr = InitNornir(config_file="config.yaml")

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