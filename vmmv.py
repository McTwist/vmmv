#!/usr/bin/env python3

import sys
import os
from subprocess import Popen, PIPE, DEVNULL
import re
import re

def program(*args, **kwargs):
	return Popen([*args], **kwargs)

class Storages:
	def __init__(self):
		self.__storages = {}
		cfg_storage = re.compile(r'^([\w\-]+): (\w+)$')
		cfg_conf = re.compile(r'^\s+(\w+) (.+)$')
		storage = None
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
					if not g:
						continue
					self.__storages[storage][g.group(1)] = g.group(2)
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
			if 'backup' in self.__storages[storage]['content'] and 'path' in self.__storages}
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

			vg = storages[self.item]
			new = self.__new_vm(_id)
			program('lvrename', '{}/{}'.format(vg, self.item), '{}/{}'.format(vg, new), stdout=DEVNULL).wait()
			return new
		elif self.__storage['type'] == 'zfspool':
			proc = program('zfs', 'list', '-H', '-o', 'name', '-t', 'volume', stdout=PIPE)
			volumes = dict(reversed(line.decode().strip().split("/")) for line in proc.stdout.readlines())
			proc.wait()

			pool = volumes[self.item]
			new = self.__new_vm(_id)
			program("zfs", "rename", '{}/{}'.format(pool, self.item), '{}/{}'.format(pool, new), stdout=DEVNULL).wait()
			return new
		elif self.__storage['type'] in ['dir', 'nfs', 'cifs']:
			path = self.__storage['path']
			fr = os.path.join(path, "dump", self.item)
			new = self.__new_file(_id)
			to = os.path.join(path, "dump", new)
			os.rename(fr, to)
			return new
	def __new_vm(self, _id):
		m = re.match(r'vm-\d+-disk-(\d+)', self.item)
		return "vm-{}-disk-{}".format(_id, m[1])
	def __new_file(self, _id):
		m = re.match(r"vzdump-(\w+)-\d+-(.+)", self.item)
		return "vzdump-{}-{}-{}".format(m[1], _id, m[2])

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
			raise AttributeError("{} does not exist.".format(name))

	def move(self, newid):
		if not self.__file:
			return

		# Get content from file
		with open(self.__file.format(self.__id), "rt") as f:
			s = f.read()

		storages = Storages()

		pattern = re.compile(r'vm-\d+-disk-(\d+)')
		for storage, disk in re.findall(r'(\w+):(vm-\d+-disk-\d+)', s):
			item = storages.get_item(storage, disk)
			new = item.rename(newid)
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
		with open("/etc/pve/user.cfg", "rt") as f:
			s = f.readlines()

		# Update pools
		change = lambda m: "{}{}{}".format(m[1], newid, m[2])
		pattern = re.compile(r"(,|:){}(,|:)".format(self.__id))
		for i in range(len(s)):
			if s[i].startswith("pool:"):
				s[i] = pattern.sub(change, s[i])

		with open("/etc/pve/user.cfg", "wt") as f:
			f.writelines(s)
	def __update_jobs(self, newid):
		with open("/etc/pve/jobs.cfg", "rt") as f:
			s = f.readlines()

		# Update jobs
		change = lambda m: "{}{}{}".format(m[1], newid, m[2])
		pattern = re.compile(r"(,| ){}(,|\n)".format(self.__id))
		for i in range(len(s)):
			if s[i].strip().startswith("vmid "):
				s[i] = pattern.sub(change, s[i])

		with open("/etc/pve/jobs.cfg", "wt") as f:
			f.writelines(s)

def main(argv):
	if len(argv) != 3:
		print('{} require 2 ids'.format(argv[0]))
		return 1

	fromid, toid = argv[1:]
	if not re.search(r'\d+', fromid) or not re.search(r'\d+', toid):
		print("Not a valid vmid")
		return 1

	if UnitFile(toid).exist:
		print("{} already exist".format(toid))
		return 1

	unit = UnitFile(fromid)
	if not unit.exist:
		print("{} is not a vm".format(fromid))
		return 1

	unit.move(toid)
	return 0

if __name__ == "__main__":
	sys.exit(main(sys.argv))
