import os
import sys
import json
from random import choice
from string import digits, letters

from plugin import connection  # FIXME

from cloudify import ctx
from cloudify.state import ctx_parameters as params


node_instance = {}
kwargs = {}
kwargs['name'] = params.minion_id

client = connection.MistConnectionClient(properties=ctx.node.properties)
machine = client.other_machine(kwargs)

node_instance['node_id'] = 'kube_worker'
node_instance['name'] = 'kube_worker'
node_instance['id'] = (
    'kube_worker_' + ''.join(choice(letters + digits) for _ in range(5))
).lower()
node_instance['host_id'] = node_instance['id']
node_instance['version'] = 9  # TODO
node_instance['state'] = 'started'
node_instance['runtime_properties'] = {
    'info': machine.info,
    'mist_type': 'machine',
    'machine_id': machine.info['machine_id'],
    'ip': machine.info['public_ips'][0],
    'master_ip': ctx.instance.runtime_properties['master_ip'],
    'networks:': [machine.info['public_ips'][0]]
}
node_instance['relationships'] = [
    {
        'target_id': ctx.instance.id,
        'target_name': 'kube_master',
        'type': 'cloudify.relationships.connected_to'
    }
]

_storage = os.path.join(os.getcwd(), 'local-storage/local/node-instances')
with open(os.path.join(_storage, node_instance['id']), 'w') as _instance_file:
    _instance_file.write(json.dumps(node_instance))

