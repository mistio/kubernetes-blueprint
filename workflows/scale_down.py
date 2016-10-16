from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs
from cloudify.exceptions import NonRecoverableError

import sys
import os
import uuid
import pkg_resources
import glob
import requests
import json

import string
import random

from time import time, sleep

try:
    import connection
except ImportError:
    sys.path.insert(0, 'lib/python2.7/site-packages/plugin/')  # TODO
    import connection

# FIXME
resource_package = __name__

try:
    # This path is for `cfy local` executions
    resource_path = os.path.join('../scripts', 'mega-deploy.sh')
    kubernetes_script = pkg_resources.resource_string(resource_package,
                                                      resource_path)
except IOError:
    # This path is for executions performed by Mist.io
    tmp_dir = os.path.join('/tmp/templates',
                           'mistio-kubernetes-blueprint-[A-Za-z0-9]*',
                           'scripts')
    scripts_dir = glob.glob(tmp_dir)[0]
    resource_path = os.path.join(scripts_dir, 'mega-deploy.sh')
    with open(resource_path) as f:
        kubernetes_script = f.read()


def scale_cluster(delta):
    if isinstance(delta, basestring):
        delta = int(delta)

    if delta == 0:
        workctx.logger.info('Delta parameter equals 0! No scaling will '
                            'take place')
        return
    else:
        # TODO verify that (current number of nodes) - (delta) > 0
        delta = abs(delta)
        workctx.logger.info('Scaling Kubernetes cluster down '
                            'by %s node(s)', delta)
        scale_cluster_down(delta)


def scale_cluster_down(quantity):
    master = workctx.get_node('kube_master')
    master_instance = [instance for instance in master.instances][0]
    # TODO Get runtime properties directly from local-storage
    # Factor this out, since it exists in both workflow files atm
    master_instance_from_file = instance_from_local_storage(
        instance='kube_master')
    # TODO deprecate this! /
    mist_client = connection.MistConnectionClient(properties=master.properties)
    cloud = mist_client.cloud
    master_machine = mist_client.machine
    # / deprecate
    # Private IP of Kubernetes Master
    #master_ip = master_machine.info['public_ips'][0]
    master_ip = master_instance_from_file['runtime_properties']['master_ip']

    # NOTE: Such operations run asynchronously
    master_instance.execute_operation(
        'cloudify.interfaces.lifecycle.authenticate',
        kwargs={'action': 'disassociate'}
    )

    worker_name = inputs.get('worker_name')
    if not worker_name:
        raise NonRecoverableError('Kubernetes Worker\'s name is missing')

    machines = cloud.machines(search=worker_name)
    if not machines:
        workctx.logger.warn('Cannot find node \'%s\'. Already removed? '
                            'Exiting...', worker_name)
        return

    workctx.logger.info('Terminating %d Kubernetes Worker(s)...', len(machines))
    counter = 0
    for m in machines:
        if not m.info['state'] in ('stopped', 'running'):
            continue
        counter += 1
        # Properly modify the IP in order to be used in the URL
        worker_priv_ip = m.info['private_ips'][0]
        worker_selfLink = 'ip-' + str(worker_priv_ip).replace('.', '-')
        # Destroy machine
        m.destroy()

        # Get the token from file in order to secure communication
        with open('/tmp/cloudify-mist-plugin-kubernetes-credentials', 'r') as f:
            basic_auth = f.read()

        workctx.logger.info('Removing node from the Kubernetes cluster...')
        requests.delete('https://%s@%s/api/v1/nodes/%s' % \
                        (basic_auth, master_ip, worker_selfLink), verify=False)

        if counter == quantity:
            break

    workctx.logger.info('Downscaling Kubernetes cluster succeeded!')


def instance_from_local_storage(instance):
    local_storage = os.path.join('/tmp/templates',
                                 'mistio-kubernetes-blueprint-[A-Za-z0-9]*',
                                 'local-storage/local/node-instances',
                                 '%s_[A-Za-z0-9]*' % instance)

    instance_file = glob.glob(local_storage)[0]
    with open(instance_file, 'r') as ifile:
        node_instance = ifile.read()

    return json.loads(node_instance)


scale_cluster(inputs['delta'])

