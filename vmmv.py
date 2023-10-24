#!/usr/bin/env python3

import sys
import os
from subprocess import Popen, PIPE, DEVNULL
import re

def program(*args, **kwargs):
	return Popen([*args], **kwargs)

class Storages:
	def __init__(self):
		self.__storages = {}
		cfg_storage = re.compile(r'^(\w+): (.+)$')
		cfg_conf = re.compile(r'^\s+(\w+) (.+)$')
		storage = None
		try:
			with open("/etc/pve/storage.cfg", "rt") as f:
				cfg = [line.rstrip() for line in f.readlines()]
				for c in cfg:
					g = cfg_storage.match(c)
					if g:
						storage = g.group(2)
						self.__storages[storage] = {'name': storage}
						self.__storages[storage]['type'] = g.group(1)
					elif storage:
						g = cfg_conf.match(c)
						if g:
							self.__storages[storage][g.group(1)] = g.group(2)
						else:
							self.__storages[storage][c.strip()] = True
			print(self.__storages.keys())
			raise Exception()
		except FileNotFoundError:
			return
	def __getitem__(self, storage):
		if storage not in self.__storage:
			return None
		return self.__storage[storage]
	def __repr__(self):
		return str(self.__storages)
	# Backups
	def backup(self):
		return {storage: self.__storages[storage]
			for storage in self.__storages
			if 'backup' in self.__storages[storage]['content']
				and 'path' in self.__storages[storage]}
	def get_item(self, storage, item):
		if storage not in self.__storages:
			return None
		return UnitItem(self.__storages[storage], item)

class UnitItem:
	def __init__(self, storage, item):
		self.__storage = storage
		self.item = item
	def rename(self, _id):
		if self.__storage['type'] in ['lvm', 'lvmthin']:
			proc = program('lvs', '--rows', '--noheadings', stdout=PIPE)
			storages = dict(zip(proc.stdout.readline().decode().split(), proc.stdout.readline().decode().split()))
			proc.wait()

			if self.item not in storages:
				return None
			vg = storages[self.item]
			new = self.__new_disk(_id)
			program('lvrename', f'{vg}/{self.item}', f'{vg}/{new}', stdout=DEVNULL).wait()
			return new
		elif self.__storage['type'] == 'zfspool':
			proc = program('zfs', 'list', '-H', '-o', 'name', '-t', 'volume,filesystem', stdout=PIPE)
			volumes = dict(reversed(line.decode().strip().split("/")) for line in proc.stdout.readlines() if line.count(b"/") == 1)
			proc.wait()

			if self.item not in volumes:
				return None
			pool = volumes[self.item]
			new = self.__new_disk(_id)
			program("zfs", "rename", f'{pool}/{self.item}', f'{pool}/{new}', stdout=DEVNULL).wait()
			return new
		elif self.__storage['type'] in ['dir', 'nfs', 'cifs']:
			path = self.__storage['path']
			fr = os.path.join(path, "dump", self.item)
			new = self.__new_file(_id)
			to = os.path.join(path, "dump", new)
			os.rename(fr, to)
			return new
	def __new_disk(self, _id):
		m = re.match(r'(vm|subvol|base)-\d+-disk-(\d+)', self.item)
		return f'{m[1]}-{_id}-disk-{m[2]}'
	def __new_file(self, _id):
		m = re.match(r'vzdump-(\w+)-\d+-(.+)', self.item)
		return f'vzdump-{m[1]}-{_id}-{m[2]}'

class UnitFile:
	def __init__(self, _id):
		self.__id = _id
		fqm = "/etc/pve/qemu-server/{}.conf"
		flxc = "/etc/pve/lxc/{}.conf"
		if os.path.exists(fqm.format(_id)):
			self.__file = fqm
		elif os.path.exists(flxc.format(_id)):
			self.__file = flxc
		else:
			self.__file = None

	def __getattr__(self, name):
		if name == "exist":
			return self.__file is not None
		else:
			raise AttributeError(f"{name} does not exist.")

	def move(self, newid):
		if not self.__file:
			return

		# Get content from file
		with open(self.__file.format(self.__id), "rt") as f:
			s = f.read()

		storages = Storages()

		pattern = re.compile(r'(\w+):((?:vm|subvol|base)-\d+-disk-\d+)')
		for storage, disk in pattern.findall(s):
			item = storages.get_item(storage, disk)
			if item is None:
				print(f"Disk {disk} does not exist in {storage}, ignoring")
				continue
			new = item.rename(newid)
			if new is None:
				print(f"Disk {disk} does not exist, fatality")
				continue
			# Update config
			s = s.replace(disk, new)

		# Write content to file
		with open(self.__file.format(newid), "wt") as f:
			f.write(s)

		# Remove previous file
		os.remove(self.__file.format(self.__id))

		# Move backups
		pattern = re.compile(r"vzdump-(\w+)-" + self.__id + r"-(\d{4}_\d{2}_\d{2}-\d{2}_\d{2}_\d{2}(?:\.\w+)+)")
		for backup in storages.backup().values():
			if 'disable' in backup and backup['disable']:
				continue
			path = os.path.join(backup['path'], "dump")
			for file in os.listdir(path):
				m = pattern.match(file)
				if m:
					item = storages.get_item(backup['name'], file)
					new = item.rename(newid)

		# Resource pool
		self.__update_pool(newid)

		# Scheduled backups
		self.__update_jobs(newid)

		# Set the new id
		self.__id = newid
	def __update_pool(self, newid):
		try:
			with open("/etc/pve/user.cfg", "rt") as f:
				s = f.readlines()
		except FileNotFoundError:
			return

		# Update pools
		change = lambda m: f"{m[1]}{newid}{m[2]}"
		pattern = re.compile(r"(,|:){}(,|:)".format(self.__id))
		for i in range(len(s)):
			if s[i].startswith("pool:"):
				s[i] = pattern.sub(change, s[i])

		with open("/etc/pve/user.cfg", "wt") as f:
			f.writelines(s)
	def __update_jobs(self, newid):
		try:
			with open("/etc/pve/jobs.cfg", "rt") as f:
				s = f.readlines()
		except FileNotFoundError:
			return

		# Update jobs
		change = lambda m: f"{m[1]}{newid}{m[2]}"
		pattern = re.compile(r"(,| ){}(,|\n)".format(self.__id))
		for i in range(len(s)):
			if s[i].strip().startswith("vmid "):
				s[i] = pattern.sub(change, s[i])

		with open("/etc/pve/jobs.cfg", "wt") as f:
			f.writelines(s)

def main(argv):
	if not os.path.exists("/etc/pve"):
		print(f"Please run {argv[0]} directly on your node")
		return 1
	if len(argv) != 3:
		print(f"{argv[0]} require 2 ids")
		return 1

	fromid, toid = argv[1:]
	if not re.search(r'\d+', fromid) or not re.search(r'\d+', toid):
		print("Not a valid vmid")
		return 1

	if UnitFile(toid).exist:
		print(f"{toid} already exist")
		return 1

	unit = UnitFile(fromid)
	if not unit.exist:
		print(f"{fromid} is not a vm")
		return 1

	unit.move(toid)
	return 0

if __name__ == "__main__":
	sys.exit(main(sys.argv))
