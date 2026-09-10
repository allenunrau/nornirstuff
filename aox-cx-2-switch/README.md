# aox-cx-2-switch

Nornir environment for the currently running GNS3 project `aox-cx-2 switch`.

Discovered from GNS3 server `gns3:3080` with project ID
`2975904a-a7a3-4d48-9fe1-4111876e8eda`.

## Hosts

- `sw1-50`: `10.1.1.50`
- `sw2-51`: `10.1.1.51`

## Usage

Run the scripts from the repository root with `-d aox-cx-2-switch`, for example:

```bash
python3 scripts/chk_ssh.py -d aox-cx-2-switch
python3 scripts/ver.py -d aox-cx-2-switch
python3 scripts/ips.py -d aox-cx-2-switch
python3 scripts/conf.py -d aox-cx-2-switch
python3 scripts/cmds.py -d aox-cx-2-switch aos-3-tier/cmd_ver
```
