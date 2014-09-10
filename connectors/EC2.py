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

import time
import base64
from IM.uriparse import uriparse
import boto.ec2
import os
from IM.VirtualMachine import VirtualMachine
from CloudConnector import CloudConnector
from IM.radl.radl import network, Feature
from IM.config import Config

class EC2CloudConnector(CloudConnector):
	"""
	Cloud Launcher to the EC2 platform
	"""

	type = "EC2"
	"""str with the name of the provider."""
	KEYPAIR_DIR = '/tmp'
	"""str with a path to store the keypair files."""
	INSTANCE_TYPE = 't1.micro'
	"""str with the name of the default instance type to launch."""


	def concreteSystem(self, radl_system, auth_data):
		if radl_system.getValue("disk.0.image.url"):
			url = uriparse(radl_system.getValue("disk.0.image.url"))
			protocol = url[0]
			if protocol == "aws":
				# Currently EC2 plugin only uses private_key credentials
				res_system = radl_system.clone()
				if res_system.getValue('disk.0.os.credentials.private_key'):
					res_system.delValue('disk.0.os.credentials.password')
				
				instance_type = self.get_instance_type(res_system)

				res_system.addFeature(Feature("cpu.count", "=", instance_type.num_cpu * instance_type.cores_per_cpu), conflict="other", missing="other")
				res_system.addFeature(Feature("memory.size", "=", instance_type.mem, 'M'), conflict="other", missing="other")
				res_system.addFeature(Feature("disk.0.free_size", "=", instance_type.disks * instance_type.disk_space, 'G'), conflict="other", missing="other")
				res_system.addFeature(Feature("cpu.performance", "=", instance_type.cpu_perf, 'ECU'), conflict="other", missing="other")
				res_system.addFeature(Feature("price", "=", instance_type.price), conflict="me", missing="other")
					
				return [res_system]
			else:
				return []
		else:
			return [radl_system.clone()]

	# Set the EC2 credentials
	def set_ec2_credentials(self, key_id, access_key):
		"""
		Set the EC2 credentials as environment values

		Arguments:
		   - key_id(str): AWS_ACCESS_KEY_ID value.
		   - access_key(str): AWS_SECRET_ACCESS_KEY value.
		"""
		os.environ['AWS_ACCESS_KEY_ID'] = key_id
		os.environ['AWS_SECRET_ACCESS_KEY'] = access_key

	# Get the EC2 connection object
	def get_connection(self, region_name, auth_data):
		"""
		Get a :py:class:`boto.ec2.connection` to interact with.

		Arguments:
		   - region_name(str): EC2 region to connect.
		   - auth_data(:py:class:`dict` of str objects): Authentication data to access cloud provider.
		Returns: a :py:class:`boto.ec2.connection` or None in case of error		
		"""
		conn = None
		try:
			auth = auth_data.getAuthInfo(EC2CloudConnector.type)
			if auth and 'username' in auth[0] and 'password' in auth[0]:
				self.set_ec2_credentials(auth[0]['username'], auth[0]['password'])
			else:
				self.logger.error("Incorrect auth data")
				return None

			region = None
			regions = boto.ec2.regions()

			for r in regions:
				if r.name == region_name:
					region = r
			if region != None:
				conn = region.connect()
		except Exception, e:
			self.logger.error("Error getting the region " + region_name + ": ")
			self.logger.error(e)
			return None
	
		return conn
	
	# el path sera algo asi: aws://eu-west-1/ami-00685b74
	def getAMIData(self, path):
		"""
		Get the region and the AMI ID from an URL of a VMI

		Arguments:
		   - path(str): URL of a VMI (some like this: aws://eu-west-1/ami-00685b74)
		Returns: a tuple (region, ami) with the region and the AMI ID	
		"""
		region = uriparse(path)[1]
		ami = uriparse(path)[2][1:]
		
		return (region, ami)
	
	def get_instance_type(self, radl):
		"""
		Get the name of the instance type to launch to EC2

		Arguments:
		   - radl(str): RADL document with the requirements of the VM to get the instance type
		Returns: a str with the name of the instance type to launch to EC2	
		"""
		cpu = radl.getValue('cpu.count')
		if not cpu:
			cpu = Config.DEFAULT_VM_CPUS
		arch = radl.getValue('cpu.arch')
		if not arch:
			arch = Config.DEFAULT_VM_CPU_ARCH
		memory = radl.getFeature('memory.size').getValue('M')
		if not memory:
			memory = Config.DEFAULT_VM_MEMORY
		disk_free = 0
		if radl.getValue('disk.0.free_size'):
			disk_free = radl.getFeature('disk.0.free_size').getValue('G')
		
		performance = 0
		if radl.getValue("cpu.performance"):
			cpu_perf = radl.getFeature("cpu.performance")
			# Assume that GCEU = ECU
			if cpu_perf.unit == "ECU" or cpu_perf.unidad == "GCEU":
				performance = float(cpu_perf.value)
			else:
				self.logger.debug("Performance unit unknown: " + cpu_perf.unit + ". Ignore it")
		
		instace_types = EC2InstanceTypes.get_all_instance_types()

		res = None
		for type in instace_types:
			# get the instance type with the lowest price
			if res is None or (type.price <= res.price):
				if arch in type.cpu_arch and type.cores_per_cpu * type.num_cpu >= cpu and type.mem >= memory and type.cpu_perf >= performance and type.disks * type.disk_space >= disk_free:
					res = type
		
		if res is None:
			EC2InstanceTypes.get_instance_type_by_name(self.INSTANCE_TYPE)
		else:
			return res

	def launch(self, radl, requested_radl, num_vm, auth_data):
		
		system = radl.systems[0]
		
		# Currently EC2 plugin uses first private_key credentials
		if system.getValue('disk.0.os.credentials.private_key'):
			system.delValue('disk.0.os.credentials.password')
		
		(region_name, ami) = self.getAMIData(system.getValue("disk.0.image.url"))
		
		self.logger.debug("Connecting with the region: " + region_name)
		conn = self.get_connection(region_name, auth_data)
		
		res = []
		if not conn:
			for i in range(num_vm):
				res.append((False, "Error connecting with EC2, check the credentials"))
			return res
		
		images = conn.get_all_images([ami])

		try:
			# TODO: create one
			for sg in conn.get_all_security_groups():
				if sg.name == 'default':
					sg.authorize('tcp', 22, 22, '0.0.0.0/0')
		except Exception, ex:
			self.logger.debug("Error adding SSH port to the default security group. Probably it was already added.")
			self.logger.debug(ex)
			pass
		
		if len(images) == 1:
			keypair_name = "im-" + str(int(time.time()*100))
			# create the keypair
			private = system.getValue('disk.0.os.credentials.private_key')
			public = system.getValue('disk.0.os.credentials.public_key')
			if private and public:
				if public.find('-----BEGIN CERTIFICATE-----') != -1:
					self.logger.debug("The RADL specifies the PK, upload it to EC2")
					public_key = base64.b64encode(public)
					conn.import_key_pair(keypair_name, public_key)
				else:
					# the public_key nodes specifies the keypair name
					keypair_name = public
				# Update the credential data
				system.setUserKeyCredentials(system.getCredentials().username, public, private)
			else:
				self.logger.debug("Creating the Keypair")
				keypair_file = self.KEYPAIR_DIR + '/' + keypair_name + '.pem'
				keypair = conn.create_key_pair(keypair_name)
				keypair.save(self.KEYPAIR_DIR)
				os.chmod(keypair_file, 0400)
				fkeypair = open(keypair_file, "r")
				system.setUserKeyCredentials(system.getCredentials().username, None, fkeypair.read())
				fkeypair.close()
				os.unlink(keypair_file)
			i = 0
			while i < num_vm:
				instance_type = "ondemand"
				if system.getValue("instance_type"):
					instance_type = system.getValue("instance_type")
					self.logger.debug("The instance type is: " + instance_type)

				if instance_type == "spot":
					self.logger.debug("Launching a spot instance")
					instance_type = self.get_instance_type(system)
					if not instance_type:
						self.logger.error("Error launching the VM, no instance type available for the requirements.")
						self.logger.debug(system)
						res.append((False, "Error launching the VM, no instance type available for the requirements."))
						
					#TODO: Amanda -> Mirar el boto EC2 y completar el tema de las spot
					price = system.getValue("price")
					#Realizamos el request de spot instances
					if system.getValue("disk.0.os.name"):
						operative_system = system.getValue("disk.0.os.name")
						if operative_system == "linux":
							operative_system = 'Linux/UNIX'
							#TODO: diferenciar entre cuando sea 'Linux/UNIX', 'SUSE Linux' o 'Windows' teniendo en cuenta tambien el atributo "flavour" del RADL
					else:
						res.append((False, "Error launching the image: spot instances need the OS defined in the RADL"))
						#operative_system = 'Linux/UNIX'
					
					availability_zone = 'us-east-1c'
					historical_price = 1000.0
					availability_zone_list = conn.get_all_zones()
					for zone in availability_zone_list:
						history = conn.get_spot_price_history(instance_type=instance_type.name, product_description=operative_system, availability_zone=zone.name, max_results=1)
						self.logger.debug("Spot price history for the region " + zone.name)
						self.logger.debug(history)
						if history:
							if (history[0].price < historical_price):
								historical_price = history[0].price
								availability_zone = zone.name
					self.logger.debug("Launching the spot request in the zone " + availability_zone)
					
					request = conn.request_spot_instances(price=price, image_id=images[0].id, count=1, type='one-time', instance_type=instance_type.name, placement=availability_zone, key_name=keypair_name)
					
					if request:
						vm_id = region_name + ";" + request[0].id
						
						self.logger.debug("RADL:")
						self.logger.debug(system)
					
						vm = VirtualMachine(vm_id, self.cloud, radl, requested_radl)
						# Add the keypair name to remove it later 
						vm.keypair_name = keypair_name
						self.logger.debug("Instance successfully launched.")
						res.append((True, vm))
					else: 
						res.append((False, "Error launching the image"))
						
				else:
					self.logger.debug("Launching ondemand instance")
					instance_type = self.get_instance_type(system)
					if not instance_type:
						self.logger.error("Error launching the VM, no instance type available for the requirements.")
						self.logger.debug(system)
						res.append((False, "Error launching the VM, no instance type available for the requirements."))

					reservation = images[0].run(min_count=1,max_count=1,key_name=keypair_name,instance_type=instance_type.name)

					if len(reservation.instances) == 1:
						instance = reservation.instances[0]
						vm_id = region_name + ";" + instance.id
							
						self.logger.debug("RADL:")
						self.logger.debug(system)
						
						vm = VirtualMachine(vm_id, self.cloud, radl, requested_radl)
						# Add the keypair name to remove it later 
						vm.keypair_name = keypair_name
						self.logger.debug("Instance successfully launched.")
						res.append((True, vm))
					else:
						res.append((False, "Error launching the image"))
					
				i += 1
			
		return res

	def create_volume(self, conn, disk_size, placement, timeout = 60):
		"""
		Create an EBS volume

		Arguments:
		   - conn(:py:class:`boto.ec2.connection`): object to connect to EC2 API.
		   - disk_size(:py:class:`boto.ec2.connection`): The size of the new volume, in GiB
		   - placement(str): The availability zone in which the Volume will be created.
		   - timeout(int): Time needed to create the volume.
		Returns: a :py:class:`boto.ec2.volume.Volume` of the new volume	
		"""
		curr_vol = volume = conn.create_volume(disk_size, placement)
		cont = 0
		while str(volume.status) != 'available' and cont < timeout:
			self.logger.debug("State: " + str(volume.status))
			cont += 2
			time.sleep(2)
			curr_vol = conn.get_all_volumes([volume.id])[0]
		return curr_vol

	def attach_volumes(self, instance, vm):
		"""
		Attach a the required volumes (in the RADL) to the launched instance

		Arguments:
		   - instance(:py:class:`boto.ec2.instance`): object to connect to EC2 instance.
		   - vm(:py:class:`IM.VirtualMachine`): VM information.	
		"""
		try:
			if instance.state == 'running' and not "volumes" in vm.__dict__.keys():
				conn = instance.connection
				vm.volumes = []
				cont = 1
				while vm.info.systems[0].getValue("disk." + str(cont) + ".size") and vm.info.systems[0].getValue("disk." + str(cont) + ".device"):
					disk_size = vm.info.systems[0].getFeature("disk." + str(cont) + ".size").getValue('G')
					disk_device = vm.info.systems[0].getValue("disk." + str(cont) + ".device")
					self.logger.debug("Creating a %d GB volume for the disk %d" % (int(disk_size), cont))
					volume = self.create_volume(conn, int(disk_size), instance.placement)
					vm.volumes.append(volume.id)
					self.logger.debug("Attach the volume ID " + str(volume.id))
					conn.attach_volume(volume.id, instance.id, "/dev/" + disk_device)
					cont += 1
		except Exception, ex:
			self.logger.error("Error creating or attaching the volume to the instance")
			self.logger.error(ex)
			
	def delete_volumes(self, conn, vm, timeout = 60):
		"""
		Delete the volumes of a VM

		Arguments:
		   - conn(:py:class:`boto.ec2.connection`): object to connect to EC2 API.
		   - vm(:py:class:`IM.VirtualMachine`): VM information.	
		   - timeout(int): Time needed to delete the volume.	
		"""
		if "volumes" in vm.__dict__.keys() and vm.volumes:
			instance_id = vm.id.split(";")[1]
			for volume_id in vm.volumes:
				cont = 0
				deleted = False
				while not deleted and cont < timeout:
					cont += 5
					try:
						curr_vol = conn.get_all_volumes([volume_id])[0]
						if str(curr_vol.attachment_state()) == "attached":
							self.logger.debug("Detaching the volume " + volume_id + " from the instance " + instance_id)
							conn.detach_volume(volume_id, instance_id, force=True)
						elif curr_vol.attachment_state() is None:
							self.logger.debug("Removing the volume " + volume_id)
							conn.delete_volume(volume_id)
							deleted = True
						else:
							self.logger.debug("State: " + str(curr_vol.attachment_state()))
					except Exception:
						self.logger.exception("Error removing the volume.")

					time.sleep(5)
				
				if not deleted:	
					self.logger.error("Error removing the volume " + volume_id)

	# Get the EC2 instance object with the specified ID
	def get_instance_by_id(self, instance_id, region_name, auth_data):
		"""
		Get the EC2 instance object with the specified ID

		Arguments:
		   - id(str): ID of the EC2 instance.
		   - region_name(str): Region name to search the instance.
		   - auth_data(:py:class:`dict` of str objects): Authentication data to access cloud provider.
		Returns: a :py:class:`boto.ec2.instance` of found instance or None if it was not found	
		"""
		instance = None
		
		try:
			conn = self.get_connection(region_name, auth_data)
	
			reservations = conn.get_all_instances([instance_id])
			instance = reservations[0].instances[0]
		except:
			pass
			
		return instance

	def add_elastic_ip(self, vm, instance, fixed_ip = None):
		"""
		Add an elastic IP to an instance

		Arguments:
		   - vm(:py:class:`IM.VirtualMachine`): VM information.
		   - instance(:py:class:`boto.ec2.instance`): object to connect to EC2 instance.
		   - fixed_ip(str, optional): specifies a fixed IP to add to the instance.
		Returns: a :py:class:`boto.ec2.address.Address` added or None if some problem occur.	
		"""
		if vm.state == VirtualMachine.RUNNING:
			try:
				pub_address = None
				self.logger.debug("Add an Elastic IP")
				if fixed_ip:
					for address in instance.connection.get_all_addresses():
						if str(address.public_ip) == fixed_ip:
							pub_address = address

					if pub_address:
						self.logger.debug("Setting a fixed allocated IP: " + fixed_ip)
					else:
						self.logger.warn("Setting a fixed IP NOT ALLOCATED! (" + fixed_ip + "). Ignore it.")
						return None
				else:
					pub_address = instance.connection.allocate_address()

				self.logger.debug(pub_address)
				pub_address.associate(instance.id)
				return pub_address
			except Exception:
				self.logger.exception("Error adding an Elastic IP to VM ID: " + str(vm.id))
				if pub_address:
					self.logger.exception("The Elastic IP was allocated, release it.")
					pub_address.release()
				return None
		else:
			self.logger.debug("The VM is not running, not adding an Elastic IP.")
			return None

	def delete_elastic_ips(self, conn, vm):
		"""
		remove the elastic IPs of a VM

		Arguments:
		   - conn(:py:class:`boto.ec2.connection`): object to connect to EC2 API.
		   - vm(:py:class:`IM.VirtualMachine`): VM information.	
		"""
		try:
			instance_id = vm.id.split(";")[1]
			# Get the elastic IPs
			for address in conn.get_all_addresses():
				if address.instance_id == instance_id:
					self.logger.debug("This VM has a Elastic IP, disassociate it")
					address.disassociate()

					n = 0
					found = False
					while vm.getRequestedSystem().getValue("net_interface." + str(n) + ".connection"):
						net_conn = vm.getRequestedSystem().getValue('net_interface.' + str(n) + '.connection')
						if vm.info.get_network_by_id(net_conn).isPublic():
							if vm.getRequestedSystem().getValue("net_interface." + str(n) + ".ip"):
								fixed_ip = vm.getRequestedSystem().getValue("net_interface." + str(n) + ".ip")
								# If it is a fixed IP we must not release it
								if fixed_ip == str(address.public_ip):
									found = True
						n += 1

					if not found:
						self.logger.debug("Now release it")
						address.release()
					else:
						self.logger.debug("This is a fixed IP, it is not released")
		except Exception:
			self.logger.exception("Error deleting the Elastic IPs to VM ID: " + str(vm.id))

	def setIPsFromInstance(self, vm, instance):
		"""
		Adapt the RADL information of the VM to the real IPs assigned by EC2

		Arguments:
		   - vm(:py:class:`IM.VirtualMachine`): VM information.	
		   - instance(:py:class:`boto.ec2.instance`): object to connect to EC2 instance.
		"""
		num_pub_nets = num_nets = 0
		now = str(int(time.time()*100))
		#vm.info.network = []
		vm_system = vm.info.systems[0]

		if instance.ip_address != None and len(instance.ip_address) > 0 and instance.ip_address != instance.private_ip_address:
			public_net = None
			for net in vm.info.networks:
				if net.isPublic():
					public_net = net
			
			if public_net is None:
				public_net = network.createNetwork("public." + now, True)
				vm.info.networks.append(public_net)
				num_net = vm.getNumNetworkIfaces()
			else:
				# If there are are public net, get the ID
				num_net = vm.getNumNetworkWithConnection(public_net.id)
				if num_net is None:
					# There are a public net but it has not been used in this VM
					num_net = vm.getNumNetworkIfaces()

			vm_system.setValue('net_interface.' + str(num_net) + '.ip', str(instance.ip_address))
			vm_system.setValue('net_interface.' + str(num_net) + '.connection',public_net.id)
				
			num_nets += 1
			num_pub_nets = 1

		if instance.private_ip_address != None and len(instance.private_ip_address) > 0:
			private_net = None
			for net in vm.info.networks:
				if not net.isPublic():
					private_net = net
			
			if private_net is None:
				private_net = network.createNetwork("private." + now)
				vm.info.networks.append(private_net)
				num_net = vm.getNumNetworkIfaces()
			else:
				# If there are are private net, get the ID
				num_net = vm.getNumNetworkWithConnection(private_net.id)
				if num_net is None:
					# There are a private net but it has not been used in this VM
					num_net = vm.getNumNetworkIfaces()

			vm_system.setValue('net_interface.' + str(num_net) + '.ip', str(instance.private_ip_address))
			vm_system.setValue('net_interface.' + str(num_net) + '.connection',private_net.id)
				
			num_nets += 1

		elastic_ips = []
		# Get the elastic IPs assigned (there must be only 1)
		for address in instance.connection.get_all_addresses():
			if address.instance_id == instance.id:
				elastic_ips.append(str(address.public_ip))
				# It will be used if it is different to the public IP of the instance
				if str(address.public_ip) != instance.ip_address:
					vm_system.setValue('net_interface.' + str(num_nets) + '.ip', str(instance.ip_address))
					vm_system.setValue('net_interface.' + str(num_nets) + '.connection',public_net.id)

					num_pub_nets += 1
					num_nets += 1

		n = 0
		requested_ips = []
		while vm.getRequestedSystem().getValue("net_interface." + str(n) + ".connection"):
			net_conn = vm.getRequestedSystem().getValue('net_interface.' + str(n) + '.connection')
			if vm.info.get_network_by_id(net_conn).isPublic():
				fixed_ip = vm.getRequestedSystem().getValue("net_interface." + str(n) + ".ip")
				requested_ips.append(fixed_ip)
			n += 1
		
		for num, ip in enumerate(sorted(requested_ips, reverse=True)):
			if ip:
				# It is a fixed IP
				if ip not in elastic_ips:
					# It has not been created yet, do it
					self.add_elastic_ip(vm, instance, ip)
					# EC2 only supports 1 elastic IP per instance (without VPC), so break
					break
			else:
				# Check if we have enough public IPs
				if num >= num_pub_nets:
					self.add_elastic_ip(vm, instance)
					# EC2 only supports 1 elastic IP per instance (without VPC), so break
					break


	def updateVMInfo(self, vm, auth_data):
		region = vm.id.split(";")[0]
		instance_id = vm.id.split(";")[1]
		
		try:
			conn = self.get_connection(region, auth_data)
		except:
			pass

		# Check if the instance_id starts with "sir" -> spot request
		if (instance_id[0] == "s"):
			# Check if the request has been fulfilled and the instance has been deployed
			job_instance_id = None

			self.logger.debug("Check if the request has been fulfilled and the instance has been deployed")
			job_sir_id = instance_id 
			request_list = conn.get_all_spot_instance_requests()
			for sir in request_list:
				if sir.id == job_sir_id:
					job_instance_id = sir.instance_id
					break

			if job_instance_id:
				self.logger.debug("Request fulfilled, instance_id: " + str(job_instance_id))
				instance_id = job_instance_id
				vm.id = region + ";" + instance_id
			else:
				vm.state = VirtualMachine.PENDING
				return (True, vm)
		
		instance = self.get_instance_by_id(instance_id, region, auth_data)
		if (instance != None):
			instance.update()
			
			if instance.state == 'pending':
				res_state = VirtualMachine.PENDING
			elif instance.state == 'running':
				res_state = VirtualMachine.RUNNING
			elif instance.state == 'stopped':
				res_state = VirtualMachine.STOPPED
			elif instance.state == 'stopping':
				res_state = VirtualMachine.RUNNING
			elif instance.state == 'shutting-down':
				res_state = VirtualMachine.OFF
			elif instance.state == 'terminated':
				res_state = VirtualMachine.OFF
			else:
				res_state = VirtualMachine.UNKNOWN
				
			vm.state = res_state
			
			self.setIPsFromInstance(vm, instance)
			self.attach_volumes(instance, vm)
			
			try:
				vm.info.systems[0].setValue('launch_time', int(time.mktime(time.strptime(instance.launch_time[:19],'%Y-%m-%dT%H:%M:%S'))))
			except:
				self.logger.exception("Error setting the launch_time of the instance")
			
		else:
			vm.state = VirtualMachine.OFF
		
		return (True, vm)

	def cancel_spot_requests(self, conn, vm):
		"""
		Cancel the spot requests of a VM

		Arguments:
		   - conn(:py:class:`boto.ec2.connection`): object to connect to EC2 API.
		   - vm(:py:class:`IM.VirtualMachine`): VM information.	
		"""
		try:
			instance_id = vm.id.split(";")[1]
			request_list = conn.get_all_spot_instance_requests()
			for sir in request_list:
				if sir.instance_id == instance_id:
					conn.cancel_spot_instance_requests(sir.id)
					self.logger.debug("Spot instance request " + str(sir.id) + " deleted")
					break
		except Exception:
			self.logger.exception("Error deleting the spot instance request")


	def finalize(self, vm, auth_data):
		region_name = vm.id.split(";")[0]
		instance_id = vm.id.split(";")[1]
		
		conn = self.get_connection(region_name, auth_data)

		public_key = vm.getRequestedSystem().getValue('disk.0.os.credentials.public_key')
		if public_key is None or len(public_key) == 0 or (len(public_key) >= 1 and public_key.find('-----BEGIN CERTIFICATE-----') != -1):
			# only delete in case of the user do not specify the keypair name
			conn.delete_key_pair(vm.keypair_name)
		
		# Delete the EBS volumes
		self.delete_volumes(conn, vm)

		# Delete the elastic IPs
		self.delete_elastic_ips(conn, vm)
		
		# Delete the  spot instance requests
		self.cancel_spot_requests(conn, vm)

		instance = self.get_instance_by_id(instance_id, region_name, auth_data)
		if (instance != None):
			instance.update()
			instance.terminate()
		
		return (True, "")
		
	def stop(self, vm, auth_data):
		region_name = vm.id.split(";")[0]
		instance_id = vm.id.split(";")[1]

		instance = self.get_instance_by_id(instance_id, region_name, auth_data)
		if (instance != None):
			instance.update()
			instance.stop()
		
		return (True, "")
		
	def start(self, vm, auth_data):
		region_name = vm.id.split(";")[0]
		instance_id = vm.id.split(";")[1]

		instance = self.get_instance_by_id(instance_id, region_name, auth_data)
		if (instance != None):
			instance.update()
			instance.start()
		
		return (True, "")
		
	def alterVM(self, vm, radl, auth_data):
		return (False, "Not supported")

class InstanceTypeInfo:
	"""
	Information about the instance type

	Args:
		- name(str, optional): name of the type of the instance
		- cpu_arch(list of str, optional): cpu architectures supported
		- num_cpu(int, optional): number of cpus
		- cores_per_cpu(int, optional): number of cores per cpu
		- mem(int, optional): amount of memory
		- price(int, optional): price per hour
		- cpu_perf(int, optional): performance of the type in ECUs
		- disks(int, optional): number of disks
		- disk_space(int, optional): size of the disks
	"""
	def __init__(self, name = "", cpu_arch = ["i386"], num_cpu = 1, cores_per_cpu = 1, mem = 0, price = 0, cpu_perf = 0, disks = 0, disk_space = 0):
		self.name = name
		self.num_cpu = num_cpu
		self.cores_per_cpu = cores_per_cpu
		self.mem = mem
		self.cpu_arch = cpu_arch
		self.price = price
		self.cpu_perf = cpu_perf
		self.disks = disks
		self.disk_space = disk_space

# TODO: use some like Cloudymetrics or CloudHarmony
class EC2InstanceTypes:
	"""
	Information about the instance types in EC2 (Static class)
	"""
	
	@staticmethod
	def get_all_instance_types():
		"""
		Get all the EC2 instance types
		
		Returns: a list of :py:class:`InstanceTypeInfo`
		"""
		list = []
		
		t1_micro = InstanceTypeInfo("t1.micro", ["i386", "x86_64"], 1, 1, 613, 0.02, 0.5)
		list.append(t1_micro)
		m1_small = InstanceTypeInfo("m1.small", ["i386", "x86_64"], 1, 1, 1740, 0.06, 1, 1, 160)
		list.append(m1_small)
		m1_medium = InstanceTypeInfo("m1.medium", ["i386", "x86_64"], 1, 1, 3840, 0.12, 2, 1, 410)
		list.append(m1_medium)
		m1_large = InstanceTypeInfo("m1.large", ["x86_64"], 1, 2, 7680, 0.24, 4, 2, 420)
		list.append(m1_large)
		m1_xlarge = InstanceTypeInfo("m1.xlarge", ["x86_64"], 1, 4, 15360, 0.48, 8, 4, 420)
		list.append(m1_xlarge)
		m2_xlarge = InstanceTypeInfo("m2.xlarge", ["x86_64"], 1, 2, 17510, 0.41, 6.5, 1, 420)
		list.append(m2_xlarge)
		m2_2xlarge = InstanceTypeInfo("m2.2xlarge", ["x86_64"], 1, 4, 35020, 0.82, 13, 1, 850)
		list.append(m2_2xlarge)
		m2_4xlarge = InstanceTypeInfo("m2.4xlarge", ["x86_64"], 1, 4, 70041, 1.64, 13, 2, 840)
		list.append(m2_4xlarge)
		m3_xlarge = InstanceTypeInfo("m3.xlarge", ["x86_64"], 1, 8, 15360, 0.45, 26, 2, 40)
		list.append(m3_xlarge)
		m3_2xlarge = InstanceTypeInfo("m3.2xlarge", ["x86_64"], 1, 8, 30720, 0.9, 26, 2, 80)
		list.append(m3_2xlarge)
		c1_medium = InstanceTypeInfo("c1.medium", ["i386", "x86_64"], 1, 2, 1740, 0.145, 5, 1, 350)
		list.append(c1_medium)
		c1_xlarge = InstanceTypeInfo("c1.xlarge", ["x86_64"], 1, 8, 7680, 0.58, 20, 4, 420)
		list.append(c1_xlarge)
		cc2_8xlarge = InstanceTypeInfo("cc2.8xlarge", ["x86_64"], 2, 8, 61952, 2.4, 88, 4, 840)
		list.append(cc2_8xlarge)
		cr1_8xlarge = InstanceTypeInfo("cr1.8xlarge", ["x86_64"], 2, 8, 249856, 3.5, 88, 2, 120)
		list.append(cr1_8xlarge)
		
		return list

	@staticmethod
	def get_instance_type_by_name(name):
		"""
		Get the EC2 instance type with the specified name
		
		Returns: an :py:class:`InstanceTypeInfo` or None if the type is not found
		"""
		for type in EC2InstanceTypes.get_all_instance_types():
			if type.name == name:
				return type
		return None
