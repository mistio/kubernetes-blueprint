from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs
from cloudify.exceptions import NonRecoverableError

import sys
import os
import requests
import json
import pkg_resources
import glob

from time import time, sleep

from utils import LocalStorage
from utils import generate_name, random_string
from constants import CREATE_TIMEOUT, SCRIPT_TIMEOUT


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


def scale_cluster_up(quantity):
    master = workctx.get_node('kube_master')
    master_instance = [instance for instance in master.instances][0]
    # Get node directly from local-storage in order to have access to all of
    # its runtime properties
    master_node = LocalStorage.get('kube_master')
    # Private IP of the Kubernetes Master
    master_ip = master_node.runtime_properties['master_private_ip']
    master_token = master_node.runtime_properties['master_token']
    # TODO deprecate this! /
    mist_client = connection.MistConnectionClient(properties=master.properties)
    client = mist_client.client
    cloud = mist_client.cloud
    master_machine = mist_client.machine
    # /deprecate

    # FIXME
    if inputs.get('use_external_resource', False):
        machine = mist_client.other_machine(inputs)

    # Name of the new Kubernetes Worker
    machine_name = inputs.get('worker_name', '') or generate_name()
    machines = cloud.machines(search=machine_name)
    if len(machines):
        for m in machines:
            if m.info['state'] in ['running', 'stopped']:
                workctx.log.warn('Machine \'%s\' already exists. Will try to '
                                 'create anyway', machine_name)

    # FIXME improve this! This should not raise a NonRecoverableError. Instead,
    # we should allow users to add a key and use it
    if inputs.get('mist_key'):
        key = client.keys(search=inputs['mist_key'])
        if len(key):
            key = key[0]
        else:
            raise NonRecoverableError('SSH Key not found')
    else:
        raise NonRecoverableError('No SSH Key provided')

    # TODO Does this scenario cover all providers?
    kwargs = {}
    for param in ['mist_image', 'mist_size', 'mist_location']:
        if not inputs.get(param):
            raise NonRecovarableError('Input parameter \'%s\' is required, but '
                                      'it is missing', param)
        kwargs['%s_id' % param.split('_')[1]] = inputs[param]
    kwargs['networks'] = inputs.get('networks', [])

    workctx.logger.info("Deploying %d '%s' node(s)", quantity, machine_name)
    job = cloud.create_machine(async=True, name=machine_name, key=key,
                               quantity=quantity, **kwargs)

    job_id = job.json()['job_id']
    job = client.get_job(job_id)
    started_at = time()

    while True:
        err = job.get('error')
        if err:
            workctx.logger.error('An error occured during machine provisioning')
            raise NonRecoverableError(err)
        elif time() > started_at + CREATE_TIMEOUT:
            raise NonRecoverableError('Machine creation is taking too long! '
                                      'Backing away...')
        else:
            pending_machines = quantity
            created_machines = set()
            for log in job['logs']:
                if 'post_deploy_finished' in log.values():
                    created_machines.add(log['machine_id'])
                    pending_machines -= 1
                    if not pending_machines:
                        break
            else:
                workctx.logger.info('Waiting for machine to become '
                                    'responsive...')
                sleep(10)
                job = client.get_job(job_id)
                continue
        break

    workctx.logger.debug('Re-uploading Kubernetes installation script just '
                         'in case...')
    script_name = 'install_kubernetes_%s' % random_string()
    script = client.add_script(name=script_name, script=kubernetes_script,
                               location_type='inline', exec_type='executable')

    for machine_id in created_machines:
        cloud_id = inputs['mist_cloud']

        script_params = "-m '%s' -r 'node' -t '%s'" % (master_ip, master_token)
        script_id = script['id']
        workctx.logger.info('Kubernetes Worker installation started for '
                            'machine with ID \'%s\'', machine_id)
        script_job = client.run_script(script_id=script_id, cloud_id=cloud_id,
                                       machine_id=machine_id,
                                       script_params=script_params, su=True)

        job_id = script_job['job_id']
        job = client.get_job(job_id)
        started_at = job['started_at']

        while True:
            for log in job['logs']:
                if log['action'] == 'script_finished' and \
                    log.get('script_id', '') == script_id and \
                        log.get('machine_id', '') == machine_id:
                    if not log['error']:
                        break
                    # Print entire output only in case an error has occured
                    _stdout = log['stdout']
                    _extra_stdout = log['extra_output']
                    _stdout += _extra_stdout if _extra_stdout else ''
                    workctx.logger.error(_stdout)
                    raise NonRecoverableError('Installation of Kubernetes '
                                              'failed')
            else:
                if time() > started_at + SCRIPT_TIMEOUT:
                    raise NonRecoverableError('Kubernetes installation script '
                                              'is taking too long. Giving up')

                workctx.logger.info('Waiting for Kubernetes installation to '
                                    'finish')
                sleep(10)
                job = client.get_job(job_id)
                continue
            break

        # NOTE: This is an asynchronous operation
        master_instance.execute_operation(
            'cloudify.interfaces.lifecycle.associate',
            kwargs={'minion_id': machine_id}
        )

    workctx.logger.info('Upscaling Kubernetes cluster succeeded')


def scale_cluster(delta):
    if isinstance(delta, basestring):
        delta = int(delta)

    if delta == 0:
        workctx.logger.info('Delta parameter equals 0! No scaling will '
                            'take place')
        return
    else:
        workctx.logger.info('Scaling Kubernetes cluster up '
                            'by %s node(s)', delta)
        scale_cluster_up(delta)


scale_cluster(inputs['delta'])

