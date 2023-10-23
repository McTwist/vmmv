# vmunit
Rename virtual unit id

*Disclaimer: This tool should only be used in a local environment, directly within a Proxmox host. It does not acccount for, nor does it care, about units located on other servers. If a cluster is used, this tool is* **not** *your solution, as it is not tested if the change is propagated. I made this tool for my own set up, which may not suit you.*

## Usage
```
./vmunit.py <from_id> <to_id>
```

## What does this do?
It locally reassigns a VM/CT/template id to an another available one. It keeps track of the following:

- Configuration
- Disks (LVM(thin), ZFS, Directory, NFS, CIFS)(Note: Disks are on LVM/ZFS and backups are on dir/NFS/CIFS)
- Backups
- Pools
- Backup schedules

Internally it directly accesses the config files, modifying them where needed and renames disks through `lvrename`/`zfs rename`.
