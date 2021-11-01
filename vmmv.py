#!/usr/bin/env python3

import sys
import os
from subprocess import Popen, PIPE, DEVNULL
import re

def program(*args, **kwargs):
	return Popen([*args], **kwargs)

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
			AttributeError("{} does not exist.".format(name))

	def move(self, newid):
		if not self.__file:
			return

		with open(self.__file.format(self.__id), "rt") as f:
			s = f.read()

		pattern = re.compile(r'vm-\d+-disk-(\d+)')
		for vg, disk in self.get_disks():
			i = pattern.match(disk)[1]
			new = 'vm-{}-disk-{}'.format(newid, i)
			program('lvrename', '{}/{}'.format(vg, disk), '{}/{}'.format(vg, new), stdout=DEVNULL).wait()
			s = s.replace(disk, new)

		with open(self.__file.format(newid), "wt") as f:
			f.write(s)

		os.remove(self.__file.format(self.__id))
		self.__id = newid

	def get_disks(self):
		proc = program('lvs', '--rows', '--noheadings', stdout=PIPE)
		disknames = proc.stdout.readline().decode().split()
		vgnames = proc.stdout.readline().decode().split()
		proc.wait()

		pattern = re.compile(r'vm-{}-disk-\d+'.format(self.__id))
		pos = [i for i in range(len(disknames)) if pattern.search(disknames[i])]
		return [(vgnames[i], disknames[i]) for i in pos]

def main(argv):
	if len(argv) != 3:
		print('{} require 2 ids'.format(argv[0]))
		return 1

	fromid, toid = argv[1:]

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
