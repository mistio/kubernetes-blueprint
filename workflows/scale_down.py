import os
import sys
import glob
import json
import requests
import pkg_resources

from plugin import connection
from plugin.utils import LocalStorageOld as LocalStorage

from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs
from cloudify.exceptions import NonRecoverableError

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


def scale_cluster_down(quantity):
    master = workctx.get_node('kube_master')
    # Get node directly from local-storage in order to have access to all of
    # its runtime_properties
    master_node = LocalStorage.get('kube_master')
    # Public IP of the Kubernetes Master used to remove nodes from the cluster
    master_ip = master_node.runtime_properties['ip']
    username = master_node.runtime_properties['auth_user']
    password = master_node.runtime_properties['auth_pass']
    # TODO deprecate this! /
    mist_client = connection.MistConnectionClient(properties=master.properties)
    cloud = mist_client.cloud
    master_machine = mist_client.machine
    # / deprecate

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

    # Get all nodes via the kubernetes API. This will give us access to all
    # nodes' metadata.
    url = 'https://%s:%s@%s' % (username, password, master_ip)
    nodes = requests.get('%s/api/v1/nodes' % url, verify=False)
    if not nodes.ok:
        workctx.logger.debug('Kubernetes API returned: %s', nodes.text)
        raise NonRecoverableError('Failed to connect to the kubernetes API')
    nodes = nodes.json()

    # If any of the machines specified, match a kubernetes node, then
    # we attempt to remove the node from the cluster and destroy it.
    for m in machines:
        for node in nodes['items']:
            labels = node['metadata']['labels']
            if labels['kubernetes.io/hostname'] == m.name:
                if 'node-role.kubernetes.io/master' in labels.iterkeys():
                    raise NonRecoverableError('Cannot remove master')
                break
        else:
            workctx.logger.error('%s does not match a kubernetes node', m)
            continue

        workctx.logger.info('Removing %s from cluster', m)
        api = node['metadata']['selfLink']
        resp = requests.delete('%s/%s' % (url, api), verify=False)
        if not resp.ok:
            workctx.logger.error('Bad response from kubernetes: %s', resp.text)

        workctx.logger.info('Destroying machine')
        m.destroy()

        # FIXME Why?
        counter += 1
        if counter == quantity:
            break

    workctx.logger.info('Downscaling the kubernetes cluster completed!')


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


scale_cluster(inputs['delta'])

