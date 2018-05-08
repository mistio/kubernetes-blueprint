import os
import sys
import json
import glob
import time
import requests
import pkg_resources

from plugin import connection
from plugin.utils import LocalStorage
from plugin.utils import get_stack_name, generate_name, random_string
from plugin.constants import CREATE_TIMEOUT, SCRIPT_TIMEOUT

from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs
from cloudify.exceptions import NonRecoverableError

from plugin.server import create_machine


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
    from cloudify.workflows import ctx as workctx

    master = workctx.get_node('kube_master')
    master_instance = [instance for instance in master.instances][0]
    # Get node directly from local-storage in order to have access to all of
    # its runtime properties
    master_node = LocalStorage.get('kube_master')
    # Private IP of the Kubernetes Master
    master_ip = master_node.runtime_properties['master_ip']
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
    machine_name = inputs.get('worker_name', '') or generate_name(
                                                    get_stack_name(), 'worker')
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
    kwargs['networks'] = inputs.get('mist_networks', [])

    workctx.logger.info("Deploying %d '%s' node(s)", quantity, machine_name)
    job = cloud.create_machine(async=True, name=machine_name, key=key,
                               quantity=quantity, **kwargs)

    job_id = job['job_id']
    job = client.get_job(job_id)
    started_at = time.time()

    while True:
        err = job.get('error')
        if err:
            workctx.logger.error('An error occured during machine provisioning')
            raise NonRecoverableError(err)
        elif time.time() > started_at + CREATE_TIMEOUT:
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
                time.sleep(10)
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
                if time.time() > started_at + SCRIPT_TIMEOUT:
                    raise NonRecoverableError('Kubernetes installation script '
                                              'is taking too long. Giving up')

                workctx.logger.info('Waiting for Kubernetes installation to '
                                    'finish')
                time.sleep(10)
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
    from cloudify.workflows import ctx as workctx

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


#scale_cluster(inputs['delta'])

def scale_new(**kwargs):
    from cloudify.workflows import ctx as workctx

    ctx = workctx

    new_number_of_instances = 5

    node_id = 'kube_worker'
    node = ctx.get_node(node_id)
    if node.number_of_instances == new_number_of_instances:
        # no change is required
        return

    modification = ctx.deployment.start_modification({
        node.id: {
            'instances': new_number_of_instances
        }
    })

    going_up = node.number_of_instances < new_number_of_instances
    try:
        if going_up:
            # added.node_instances returns all node instances that are
            # affected by the increasing a node's number of instances.
            # Some are newly added and have their
            # instance.modification == 'added'.
            # Others are node instances that have new relationships
            # to the added node instances.
            added_and_related = modification.added.node_instances

            ctx.logger.info('********* Added_And_Related instances', added_and_related)

            for instance in added_and_related:
                if instance.modification == 'added':
                    # do stuff
                    ctx.logger.info('********* Going up! Added instances!')
                    pass
                else:
                    # do other stuff
                    ctx.logger.info('********* Going up! ...')
                    pass
        else:
            # removed.node_instances returns all node instances that are
            # affected by the decreasing a node's number of instances.
            # Some are removed and have their
            # instance.modification == 'removed'.
            # Others are node instances that will have relationships
            # to the removed node instances removed after calling
            # modification.finish().
            for instance in removed_and_related:
                if instance.modification == 'removed':
                    # do stuff
                    ctx.logger.info('********* Going down! Removed instances!')
                    pass
                else:
                    # do other stuff
                    ctx.logger.info('********* Going down! ...')
                    pass
    except:
        # Do stuff to restore the logical state and then
        # call this to restore that storage state
        modification.rollback()
        raise
    else:
        modification.finish()


if __name__ == '__main__':
    """"""
    try:
        delta = int(inputs.get('delta') or 0)
    except ValueError:
        raise RuntimeError()

    if not delta:
        raise RuntimeError()

    #
    storage = LocalStorage()
    #new_instance = storage.add_node_instance('kube_worker')
    copied_worker_instance = storage.copy_node_instance(worker_instance.id)

    #
    worker_node = workctx.get_node('kube_worker')
    worker_instance = [instance for instance in worker_node.instances][0]

    #for _ in range(delta):
    # NOTE: This is an asynchronous operation
    worker_instance.execute_operation(
        operation='cloudify.interfaces.lifecycle.create',
        kwargs={
            'cloud_id': inputs.get('mist_cloud', ''),
            'image_id': inputs.get('mist_image', ''),
            'size_id': inputs.get('mist_size', ''),
            'location_id': inputs.get('mist_location'),
            'networks': inputs.get('mist_networks', []),
            'key': inputs.get('mist_key', ''),
        },
        allow_kwargs_override=True
    )

    worker_instance.execute_operation(
        operation='cloudify.interfaces.lifecycle.configure',
        kwargs={
            'cloud_id': inputs.get('mist_cloud', ''),
            'image_id': inputs.get('mist_image', ''),
            'size_id': inputs.get('mist_size', ''),
            'location_id': inputs.get('mist_location'),
            'networks': inputs.get('mist_networks', []),
            'key': inputs.get('mist_key', ''),
        },
        allow_kwargs_override=True
    )

    # worker_instance.execute_operation(
    #     operation='cloudify.interfaces.lifecycle.associate',
    #     kwargs={'minion_id': machine_id}
    # )

    workctx.logger.info('Scaling Kubernetes cluster up by %s node(s)', delta)


def scale_old(quantity):
    """"""
    #
    master = workctx.get_node('kube_master')
    master_instance = [instance for instance in master.instances][0]

    # Get node directly from local-storage in order to have access to all of
    # its runtime properties.
    master_node = LocalStorage.get('kube_master')

    # Private IP of the Kubernetes Master
    master_ip = master_node.runtime_properties['master_ip']
    master_token = master_node.runtime_properties['master_token']

    # FIXME Re-think this.
    mist_client = connection.MistConnectionClient(properties=master.properties)
    client = mist_client.client
    cloud = mist_client.cloud
    master_machine = mist_client.machine

    # # TODO
    # if inputs.get('use_external_resource', False):
    #     machine = mist_client.other_machine(inputs)

    node_properties = {}

    #
    name = generate_name(get_stack_name(), 'worker')

    node_properties['parameters']['name'] = name
    node_properties['parameters']['quantity'] = quantity

    #
    for param in ('mist_key', 'mist_network', ):
        key = param.replace('mist_', '')
        node_properties['parameters'][key] = inputs.get(param)

    for param in ('mist_size', 'mist_image', 'mist_location', ):
        key = param.replace('mist_', '') + '_id'
        node_properties['parameters'][key] = inputs.get(param)

    #
    create_machine(node_properties, node_type='worker')

    # TODO Move to plugin?
    #
    workctx.logger.info('Uploading fresh kubernetes installation script')
    # If a script_id does not exist in the node instance's runtime
    # properties, perhaps because this is the first node that is being
    # configured, load the script from file, upload it to mist.io, and
    # run it over ssh.
    script = os.path.join(os.path.dirname(__file__), 'mega-deploy.sh')
    ctx.download_resource(  # ?????
        os.path.join('scripts', 'mega-deploy.sh'), script
    )
    with open(os.path.abspath(script)) as fobj:
        script = fobj.read()
    script = client.add_script(
        name='install_kubernetes_%s' % random_string(length=4),
        script=script, location_type='inline', exec_type='executable'
    )
    master_node.instance.runtime_properties['script_id'] = script['id']

    # TODO Move to plugin?
    # Get master node from relationships schema.
    ctx.instance.runtime_properties.update({
        'script_id': master_node.runtime_properties.get('script_id', ''),
        'master_ip': master_node.runtime_properties.get('master_ip', ''),
        'master_token': master_node.runtime_properties.get('master_token', ''),
    })

    ctx.logger.info('Setting up kubernetes worker')

    ctx.logger.info('Configuring kubernetes node')

    # Prepare script parameters.
    script_params = "-m '%s' " % master_node.instance.runtime_properties['master_ip']
    script_params += "-t '%s' " % master_node.instance.runtime_properties['master_token']
    script_params += "-r 'node'"

    # Run the script.
    script = client.run_script(
        script_id=master_node.instance.runtime_properties['script_id'], su=True,
        machine_id=machine.id,
        cloud_id=machine.cloud.id,
        script_params=script_params,
    )
    master_node.instance.runtime_properties['job_id'] = script['job_id']

    #
    wait_for_event(
        job_id=master_node.instance.runtime_properties['job_id'],
        job_kwargs={
            'action': 'script_finished',
            'machine_id': master_node.instance.runtime_properties['machine_id'],
        }
    )
    ctx.logger.info('Kubernetes installation succeeded!')
