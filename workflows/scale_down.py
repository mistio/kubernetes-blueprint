import os
import sys
import glob
import json
import netaddr
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
                           'kubernetes-blueprint',
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
    master_ip = master_node.runtime_properties['server_ip']
    username = master_node.runtime_properties['auth_user']
    password = master_node.runtime_properties['auth_pass']
    # TODO deprecate this! /
    mist_client = connection.MistConnectionClient(properties=master.properties)
    cloud = mist_client.cloud
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
    # nodes' metadata. If the master node does not expose a publicly accessible
    # IP address, then the connection will fail. In that case, we won't be
    # able to retrieve and verify the list of nodes in order to remove them
    # from the cluster.
    try:
        url = 'https://%s:%s@%s' % (username, password, master_ip)
        nodes = requests.get('%s/api/v1/nodes' % url, verify=False)
    except Exception as exc:
        if netaddr.IPAddress(master_ip).is_private():
            raise NonRecoverableError(
                'Cannot connect to the kubernetes master to automatically '
                'remove nodes from the cluster. It seems like the kubernetes '
                'master listens at a private IP address. You can manually '
                'remove nodes by destroying them or by simply disassociating '
                'them from the kubernetes cluster. For instance, the current '
                'node can be removed from the cluster by issuing an HTTP '
                'DELETE request at https://%s:%s@%s/api/v1/nodes/%s from the '
                'same network' % (username, password, master_ip, worker_name)
            )
        raise NonRecoverableError('Connection to master failed: %s', exc)
    if not nodes.ok:
        raise NonRecoverableError('Got %s: %s', nodes.status_code, nodes.text)
    nodes = nodes.json()

    # If any of the machines specified, match a kubernetes node, then
    # we attempt to remove the node from the cluster and destroy it.
    for m in machines:
        for node in nodes['items']:
            labels = node['metadata']['labels']
            if labels['kubernetes.io/hostname'] == m.name.lower():
                if 'node-role.kubernetes.io/master' in labels.iterkeys():
                    raise NonRecoverableError('Cannot remove master')
                break
        else:
            workctx.logger.error('%s does not match a kubernetes node', m)
            continue

        workctx.logger.info('Removing %s from cluster', m)
        api = node['metadata']['selfLink']
        resp = requests.delete('%s%s' % (url, api), verify=False)
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

