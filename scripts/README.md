# Nornir Scripts

This directory contains helper scripts for working with a Nornir project
directory. Most scripts connect to every inventory host with Netmiko, run one
or more commands, and either print the results or save them under
`<nornir-directory>/outputs/`.

Run the scripts from the repository root:

```bash
cd /path/to/nornirstuff
```

## Requirements

- Python environment with `nornir`, `nornir_netmiko`, `netmiko`, `paramiko`, and
  `nornir_utils` installed.
- A valid Nornir configuration file at `<nornir-directory>/config.yaml`.
- Inventory files referenced by `config.yaml`:
  - `<nornir-directory>/inventory/hosts.yaml`
  - `<nornir-directory>/inventory/groups.yaml`
  - `<nornir-directory>/inventory/defaults.yaml`
- SSH reachability from your machine to each device in the inventory.
- Valid credentials in the inventory defaults, groups, or host entries.

The current lab inventory uses the `aruba_aoscx` Netmiko device type for hosts
in the `switches` group.

Use `--directory` or `-d` with `cmds.py`, `conf.py`, and `ips.py` to choose the
Nornir directory that contains `config.yaml` and the inventory files.

## Common Troubleshooting

- `Configuration or inventory file not found`: Run the script from the
  repository root, and confirm `<nornir-directory>/config.yaml` and the
  inventory files exist.
- `SSH authentication failed`: Check the username and password in the inventory.
- `Connection or command timed out`: Confirm the device is reachable and SSH is
  enabled.
- `Unable to communicate with the device`: Check the host IP address, network
  path, SSH service, and device availability.
- File write errors: Check permissions for the
  `<nornir-directory>/outputs/` directory.

## `conf.py`

`conf.py` backs up the running configuration from every host in the Nornir
inventory.

It runs:

```text
no page
show running-config
```

Then it writes one backup file per host:

```text
<nornir-directory>/outputs/<host>_config.aos
```

For example, `core1` is saved as
`<nornir-directory>/outputs/core1_config.aos`.

### Usage

```bash
python scripts/conf.py
```

Use another Nornir directory:

```bash
python scripts/conf.py --directory <nornir-directory>
```

### What Happens When It Runs

1. Nornir loads the inventory from `config.yaml`.
2. Each host is processed using the threaded runner configured in `config.yaml`.
3. The script connects to each host using Netmiko.
4. It disables paging with `no page`.
5. It collects the running configuration with `show running-config`.
6. It creates `<nornir-directory>/outputs/` if needed.
7. It writes one `.aos` backup file per host.
8. It prints an `OK` or `ERROR` line for each host.

Example success message:

```text
OK core1: Successfully saved all command outputs to /path/to/nornirstuff/<nornir-directory>/outputs/core1_config.aos
```

### Customizing

To collect different commands, edit the `COMMANDS` list in `conf.py`:

```python
COMMANDS = [
    "no page",
    "show running-config",
]
```

To change where backups are saved, edit:

```python
output_directory = nornir_directory / "outputs"
```

### Arguments

- `-d`, `--directory`: Optional directory containing Nornir `config.yaml` and
  inventory files.

### Exit Codes

- `0`: All hosts completed successfully.
- `1`: One or more hosts failed, or an unexpected error occurred.
- `2`: Required configuration or inventory files were missing, or no hosts were
  found.
- `130`: The run was cancelled with `Ctrl+C`.

## `cmds.py`

`cmds.py` runs commands from a text file against every inventory host and saves
the output to one file per host. It is useful when you want to collect ad hoc
show-command output without editing a Python script.

Command files must be UTF-8 text files. Blank lines are ignored, and lines that
start with `#` are treated as comments.

Relative command file paths are checked from the current directory first and
then from the selected Nornir directory, so the existing lab command files can
still be passed by filename when they are stored in that directory.

Example command file:

```text
show ip interface brief | i up
show ip route
show ip ospf
show ip ospf nei
```

### Usage

```bash
python scripts/cmds.py cmds_ospf
```

Use another Nornir directory:

```bash
python scripts/cmds.py --directory <nornir-directory> cmds_ospf
```

Add a filename suffix with `--suffix` or `-s`:

```bash
python scripts/cmds.py cmds_ospf --suffix ospf
```

Output files are written to:

```text
<nornir-directory>/outputs/<host>_output.txt
<nornir-directory>/outputs/<host>_output_<suffix>.txt
```

For example, using `--suffix ospf` writes
`<nornir-directory>/outputs/core1_output_ospf.txt`.

### What Happens When It Runs

1. The script reads commands from the command file.
2. Nornir loads the inventory from `config.yaml`.
3. The script connects to each host using Netmiko.
4. It runs each command in order.
5. It writes one output file per host under `<nornir-directory>/outputs/`.
6. If a command fails for a host, it stops running more commands on that host
   and saves the partial output.
7. It prints an `OK` or `ERROR` line for each host.

### Arguments

- `command_file`: Required path to a text file containing one command per line.
- `-s`, `--suffix`: Optional suffix added to output filenames. The suffix must
  be 1 to 64 characters and can contain letters, numbers, periods,
  underscores, or hyphens.
- `-d`, `--directory`: Optional directory containing Nornir `config.yaml` and
  inventory files.

### Exit Codes

- `0`: All hosts completed successfully.
- `1`: One or more hosts failed, or an unexpected error occurred.
- `2`: The command file, configuration, or inventory was invalid or missing, or
  no hosts were found.
- `130`: The run was cancelled with `Ctrl+C`.

## `configure_vlans.py`

`configure_vlans.py` creates VLANs on inventory hosts whose host data contains
`role: access_switch`.

This script changes device configuration. Review the VLAN list and target host
filter before running it.

It sends these configuration commands:

```text
vlan 100
name Engineering
vlan 200
name Sales
vlan 300
name Management
exit
```

### Usage

```bash
python scripts/configure_vlans.py
```

### What Happens When It Runs

1. Nornir loads the inventory from `config.yaml`.
2. The inventory is filtered to hosts with `data.role` equal to
   `access_switch`.
3. The script connects to those hosts using Netmiko.
4. It sends the VLAN configuration commands with `netmiko_send_config`.
5. It prints detailed Nornir task results with `print_result`.

### Customizing

To change the target devices, edit the Nornir filter:

```python
switches = nr.filter(F(data__role="access_switch"))
```

To change the VLANs, edit the `vlan_config` list:

```python
vlan_config = [
    "vlan 100",
    "name Engineering",
    "vlan 200",
    "name Sales",
    "vlan 300",
    "name Management",
    "exit",
]
```

## `ips.py`

`ips.py` runs a single interface summary command against every inventory host
and prints the output to the terminal.

It runs:

```text
show ip interface brief | i up
```

The command filters for interfaces containing `up` in the output.

### Usage

```bash
python scripts/ips.py
```

Use another Nornir directory:

```bash
python scripts/ips.py --directory <nornir-directory>
```

### What Happens When It Runs

1. Nornir loads the inventory from `config.yaml`.
2. The script connects to each host using Netmiko.
3. It runs `show ip interface brief | i up`.
4. It prints one section per host.
5. If a host fails, it prints a concise error for that host to standard error.

Example output:

```text
===== core1 =====
<command output>
```

### Customizing

To check a different interface command, edit:

```python
COMMAND = "show ip interface brief | i up"
```

### Arguments

- `-d`, `--directory`: Optional directory containing Nornir `config.yaml` and
  inventory files.

### Exit Codes

- `0`: All hosts completed successfully.
- `1`: One or more hosts failed, or an unexpected error occurred.
- `2`: Required configuration or inventory files were missing, or no hosts were
  found.
- `130`: The run was cancelled with `Ctrl+C`.

## `ver.py`

`ver.py` runs `show version` against every inventory host and prints the output
to the terminal. It is useful for checking software versions and basic device
details across the lab.

### Usage

```bash
python scripts/ver.py
```

### What Happens When It Runs

1. Nornir loads the inventory from `config.yaml`.
2. The script connects to each host using Netmiko.
3. It runs `show version`.
4. It prints command output for hosts that completed successfully.
5. If any hosts fail, it prints a concise error summary to standard error.

Example output:

```text
===== core1 =====
<show version output>
```

### Customizing

To run a different version or inventory check command, edit:

```python
SHOW_VERSION_COMMAND = "show version"
```

### Exit Codes

- `0`: All hosts completed successfully.
- `1`: One or more hosts failed, or an unexpected error occurred.
- `2`: Required configuration or inventory files were missing, or no hosts were
  found.
- `130`: The run was cancelled with `Ctrl+C`.
