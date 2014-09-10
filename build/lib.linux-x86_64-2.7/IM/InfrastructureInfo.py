# IM - Infrastructure Manager
# Copyright (C) 2011 - GRyCAP - Universitat Politecnica de Valencia
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
import threading

import ConfManager
from datetime import datetime
from IM.radl.radl import RADL, Feature, deploy, system

class InfrastructureInfo:
	"""
	Stores all the information about a registered infrastructure.
	"""
	
	logger = logging.getLogger('InfrastructureManager')
	
	def __init__(self):
		self._lock = threading.Lock()
		"""Threading Lock to avoid concurrency problems."""
		self.id = 0
		"""Infrastructure ID."""
		self.vm_list = []
		"""Map of int to VirtualMachine."""
		self.auth = None
		"""Authentication of type ``InfrastructureManager``."""
		self.radl = RADL()
		"""RADL associated to the infrastructure."""
		self.private_networks = {}
		"""(dict from str to str) Cloud provider ids associated to a private network."""
		self.system_counter = 0
		"""(int) Last system generated."""
		self.deleted = False
		"""Flag to specify that this infrastructure has been deleted"""
		self.cm = None
		"""ConfManager Thread to contextualize"""
		self.vm_master = None
		"""VM selected as the master node to the contextualization step"""
		self.cont_out = ""
		"""Contextualization output message"""
		self.configured = None
		"""Configure flag. If it is None the contextualization has not been finished yet"""
	
	def __getstate__(self):
		"""
		Function to save the information to pickle
		"""
		with self._lock:
			odict = self.__dict__.copy()
		# Quit the ConfManager object and the lock to the data to be store by pickle
		del odict['cm']
		del odict['_lock']
		return odict
	
	def __setstate__(self, dic):
		"""
		Function to load the information to pickle
		"""
		self._lock = threading.Lock()
		with self._lock:
			self.__dict__.update(dic)
			# Set the ConfManager object and the lock to the data loaded by pickle
			self.cm = None
		
		# If we load an Infrastructure that is not configured, set it to False
		# because the configuration process will be lost
		if self.configured is None:
			self.configured = False
		
	def delete(self):
		"""
		Set this Inf as deleted
		"""
		self.deleted = True
		
	def get_cont_out(self):
		"""
		Returns the contextualization message
		"""
		return self.cont_out
	
	def add_vm(self, vm):
		"""
		Add, and assigns a new VM ID to the infrastructure 
		"""
		with self._lock:
			# Assign the VM IM ID
			vm.im_id = str(len(self.vm_list))
			self.vm_list.append(vm)
	
	def add_cont_msg(self, msg):
		"""
		Add a line to the contextualization message
		"""
		self.cont_out += str(datetime.now()) + ": " + str(msg) + "\n"
		
	def get_vm_list(self):
		"""
		Get the list of not destroyed VMs
		"""
		with self._lock:
			res = [vm for vm in self.vm_list if not vm.destroy]
		return res 
	
	def get_vm(self, str_vm_id):
		"""
		Get the VM with the specified ID (if it is not destroyed)
		"""
		
		vm_id = int(str_vm_id)
		if vm_id >= 0 and vm_id < len(self.vm_list):
			vm = self.vm_list[vm_id]
			return vm if not vm.destroy else None
		else:
			return None		

	def get_vm_list_by_system_name(self):
		groups = {}
		for vm in self.get_vm_list():
			if vm.getRequestedSystem().name in groups:
				groups[vm.getRequestedSystem().name].append(vm)
			else:
				groups[vm.getRequestedSystem().name] = [vm]
		return groups
	
	def is_contextualizing(self):
		"""
		Returns if the Infrastructure is in the contextualization step
		"""
		if self.cm:
			return self.cm.is_contextualizing()
		else:
			return False
		
	def Contextualize(self, auth_data):
		"""
		Starts the contextualization step
		"""
		if self.is_contextualizing():
			raise Exception("The infrastructure is contextualizing. You must wait")

		with self._lock:
			self.configured = None
			self.cont_out = ""
			self.cm = ConfManager.ConfManager()
			self.cm.Contextualize(self, auth_data)
	
	
	def update_radl(self, radl, deployed_vms):
		"""
		Update the stored radl with the passed one.

		Args:

		- radl(RADL) RADL base of the deployment.
		- deployed_vms(list of tuple of deploy, system and list of VirtualMachines): list of
		   tuples composed of the deploy, the concrete system deployed and the list of
		   virtual machines deployed.
		"""

		with self._lock:
			# Add new systems and networks only
			for s in radl.systems + radl.networks:
				if not self.radl.add(s.clone(), "ignore"):
					InfrastructureInfo.logger.warn("Ignoring the redefinition of %s %s" % (type(s), s.getId()))
	
			# Add or update configures
			for s in radl.configures:
				self.radl.add(s.clone(), "replace")
				InfrastructureInfo.logger.warn("(Re)definition of %s %s" % (type(s), s.getId()))
	
			# Append contextualize
			self.radl.add(radl.contextualize)
			
			if radl.deploys:
				# Overwrite to create only the last deploys
				self.radl.deploys = radl.deploys
	
			# Associate private networks with cloud providers
			for d, _, _ in deployed_vms:
				for private_net in [net.id for net in radl.networks if not net.isPublic() and
			                           net.id in radl.get_system_by_name(d.id).getNetworkIDs()]:
					if private_net in self.private_networks:
						assert self.private_networks[private_net] == d.cloud_id
					else:
						self.private_networks[private_net] = d.cloud_id

		# Check the RADL
		self.radl.check();

	def complete_radl(self, radl):
		"""
		Update passed radl with the stored RADL.
		"""

		with self._lock:
			# Replace references of systems, networks and configures by its definitions
			for s in radl.networks + radl.systems + radl.configures:
				if s.reference:
					aspect = self.radl.get(s)
					if aspect == None:
						raise Exception("Unknown reference in RADL to %s '%s'" % (type(s), s.getId()))
					radl.add(aspect.clone(), "replace")
	
			# Add fake deploys to indicate the cloud provider associated to a private network.
			FAKE_SYSTEM, system_counter = "F0000__FAKE_SYSTEM__%s", 0
			for n in radl.networks:
				if n.id in self.private_networks:
					system_id = FAKE_SYSTEM % system_counter
					system_counter += 1
					radl.add(system(system_id, [Feature("net_interface.0.connection", "=", n.id)]))
					radl.add(deploy(system_id, 0, self.private_networks[n.id]))
				
		# Check the RADL
		radl.check();