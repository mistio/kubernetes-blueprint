from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs
from cloudify.exceptions import NonRecoverableError

import sys
import os
import uuid
import pkg_resources
import glob
import requests

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
    kubernetes_script = \
        pkg_resources.resource_string(resource_package, resource_path)
except IOError:
    # This path is for executions performed by Mist.io
    tmp_dir = os.path.join('/tmp/templates',
                           'mistio-kubernetes-blueprint-[A-Za-z0-9]*',
                           'scripts')
    scripts_dir = glob.glob(tmp_dir)[0]
    resource_path = os.path.join(scripts_dir, 'mega-deploy.sh')
    with open(resource_path) as f:
        kubernetes_script = f.read()


#KUBE_TYPE = 'worker'
CREATE_TIMEOUT = 60 * 5
SCRIPT_TIMEOUT = 60 * 30


def random_name(length=4):

    def random_chars(length):
        _chars = string.letters + string.digits
        return ''.join(random.choice(_chars) for _ in range(4))

    return 'MistCfyNode-%s-%s' % (random_chars(length), random_chars(length))


def scale_cluster():
    delta = inputs.get('delta')
    if isinstance(delta, basestring):
        delta = int(delta)

    if delta == 0:
        workctx.logger.info('Delta parameter equals 0! No scaling will take '
                            'place')
        return
    elif delta > 0:
        workctx.logger.info('Scaling Kubernetes cluster up by %s node(s)',
                            delta)
        scale_cluster_up(delta)
    elif delta < 0:
        # TODO verify that (current number of nodes) - (delta) > 0
        workctx.logger.info('Scaling Kubernetes cluster down by %s node(s)',
                            abs(delta))
        scale_cluster_down(abs(delta))


def scale_cluster_up(delta):
    master = workctx.get_node('kube_master')
    master_instance = [instance for instance in master.instances][0]
    # TODO deprecate this! /
    mist_client = connection.MistConnectionClient(properties=master.properties)
    client = mist_client.client
    cloud = mist_client.cloud
    master_machine = mist_client.machine
    # Private IP of the Kubernetes Master
    master_ip = master_machine.info['private_ips'][0]
    # /deprecate

    if inputs['use_external_resource']:
        machine = mist_client.other_machine(inputs)  # FIXME

    # Name of the new Kubernetes Worker
    machine_name = inputs.get('worker_name', '') or random_name()
    machines = cloud.machines(search=machine_name)
    if len(machines):
        for m in machines:
            if m.info['state'] in ['running', 'stopped']:
                workctx.log.warn('Machine \'%s\' already exists. Will try to '
                                 'create anyway', machine_name)

    # FIXME improve this!
    # This should not raise a NonRecoverableError. Instead, we should allow
    # users to add a key and use it
    if inputs.get('mist_key'):
        key = client.keys(search=inputs['mist_key'])
        if len(key):
            key = key[0]
        else:
            raise NonRecoverableError('Key not found')
    else:
        raise NonRecoverableError('No key provided')

    # TODO is this correct? Does this scenario cover all providers?
    for param in ['mist_image', 'mist_size', 'mist_location']:
        if not inputs.get(param):
            raise NonRecovarableError('Input parameter \'%s\' is required, but '
                                      'it is missing', param)
    image_id = inputs['mist_image']
    size_id = inputs['mist_size']
    location_id = inputs['mist_location']
    networks = inputs.get('networks', [])

    workctx.logger.info("Deploying %d '%s' node(s)", delta, machine_name)
    quantity = delta
    job = cloud.create_machine(async=True, name=machine_name, key=key,
                               image_id=image_id, location_id=location_id,
                               size_id=size_id, quantity=quantity,
                               networks=networks)

    job_id = job.json()['job_id']
    job = client.get_job(job_id)
    started_at = time()

    workctx.logger.info('Machine creation succeeded. Probing...')
    while True:
        if job['summary']['probe']['success']:
            workctx.logger.info('Machine probed successfully')
            break
        if job['summary']['create']['error'] or \
            job['summary']['probe']['error']:
            err = job['logs'][2]
            if err.get('error', ''):
                workctx.logger.error('An error occured, while probing '
                                     'machine:\n%s', err.get('error', ''))
            raise NonRecoverableError('Machine has encountered an error')

        if time() > started_at + CREATE_TIMEOUT:
            # TODO print something!
            raise NonRecoverableError('Machine creation is taking too long! '
                                      'Backing away...')

        workctx.logger.info('Waiting for machine to become responsive...')
        sleep(5)
        job = client.get_job(job_id)

    # FIXME re-uploading Kubernetes script
    script_name = 'install_kubernetes_%s' % uuid.uuid1().hex
    script = client.add_script(name=script_name, script=kubernetes_script,
                               location_type='inline', exec_type='executable')

    for m in xrange(quantity):
        machine_name = \
            machine_name.rsplit('-', 1)[0] if quantity > 1 else machine_name
        machine_name += '-' + str(m + 1) if quantity > 1 else ''

        inputs['name'] = machine_name
        # FIXME `other_machine` must be ERADICATED!
        machine = mist_client.other_machine(inputs)
        inputs['machine_id'] = machine.info['id']

        machine_id = inputs['machine_id']
        cloud_id = inputs['mist_cloud']

        # NOTE TEST THIS # # # #
        master_token =  master_instance.execute_operation( 
            'cloudify.interfaces.lifecycle.authenticate',
            kwargs={'action': 'associate'}
        )
        # # # # # # # # # # # #

        script_params = "-m '%s' -r 'node' -t '%s'" % (master_ip, master_token)
        script_id = script['id']
        workctx.logger.info('Kubernetes Worker installation started for %s',
                            machine_name)
        script_job = client.run_script(script_id=script_id, cloud_id=cloud_id,
                                       machine_id=machine_id,
                                       script_params=script_params, su=True)

        job_id = script_job['job_id']
        job = client.get_job(job_id)
        started_at = job['started_at']

        while True:
            if job['error']:
                _stdout = job['logs'][2]['stdout']
                _extra_stdout = job['logs'][2]['extra_output']
                _stdout += _extra_stdout if _extra_stdout else ''
                workctx.logger.error('Encountered an error during '
                                     'Kubernetes installation:\n%s', _stdout)
                raise NonRecoverableError('Installation of Kubernetes failed')
            if time() > started_at + SCRIPT_TIMEOUT:
#                _stdout = job['logs'][2]['stdout']
#                _extra_stdout = job['logs'][2]['extra_output']
#                _stdout += _extra_stdout if _extra_stdout else ''
#                workctx.logger.debug(_stdout)
                raise NonRecoverableError('Installation of Kubernetes is '
                                          'taking too long! Giving up...')
            if job['finished_at']:
                break

            workctx.logger.info('Waiting for Kubernetes to be installed...')
            sleep(5)
            job = client.get_job(job_id)

        workctx.logger.info('Associating node to cluster...')
        master_instance.execute_operation(
            'cloudify.interfaces.lifecycle.associate',
            kwargs={'minion_id': machine_id}
        )

        workctx.logger.info('Kubernetes Worker %s installation script '
                            'succeeded!', inputs['name'])

    workctx.logger.info('Upscaling Kubernetes cluster succeeded!')


def scale_cluster_down(delta):
    master = workctx.get_node('kube_master')
    # TODO deprecate this! /
    mist_client = connection.MistConnectionClient(properties=master.properties)
    cloud = mist_client.cloud
    master_machine = mist_client.machine
    # / deprecate
    # Private IP of Kubernetes Master
    master_ip = master_machine.info['public_ips'][0]

    worker_name = inputs.get('worker_name')
    if not worker_name:
        raise NonRecoverableError('Kubernetes Worker\'s name is missing')

    machines = cloud.machines(search=worker_name)
    if not machines:
        workctx.logger.warn('Cannot find node \'%s\'. Already removed? '
                            'Exiting...', worker_name)
        return

    workctx.logger.info('Terminating %d Kubernetes Worker(s)...', len(machines))
    # NOTE TEST THIS # # # #
    username, password =  master_instance.execute_operation( 
        'cloudify.interfaces.lifecycle.authenticate',
        kwargs={'action': 'disassociate'}
    )
    # # # # # # # # # # # #
    counter = 0
    for m in machines:
        if not m.info['state'] in ('stopped', 'running'):
            continue
        counter += 1
        worker_priv_ip = m.info['private_ips'][0]
        worker_selfLink = 'ip-' + str(worker_priv_ip).replace('.', '-')
        m.destroy()
        workctx.logger.info('Removing node from the Kubernetes cluster...')
        requests.delete('https://%s:%s@%s/api/v1/nodes/%s' % \
                        (username, password, master_ip, worker_selfLink),
                        verify=False
                       )

        if counter == delta:
            break

    workctx.logger.info('Downscaling Kubernetes cluster succeeded!')


scale_cluster()
