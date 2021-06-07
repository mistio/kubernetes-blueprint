import os

from cloudify import ctx

from plugin import constants

from plugin.utils import random_string
from plugin.utils import wait_for_event

from plugin.connection import MistConnectionClient


def remove_kubernetes_script():
    """Attempt to remove the kubernetes installation script.

    This method tries to remove the already uploaded installation script after
    each kubernetes node has been provisioned to prevent multiple scripts from
    accumulating in the user's account.

    If an error is raised, it's logged and the workflow execution is carried
    on.

    """
    # FIXME Perhaps, scrtipt should not be handled here or this way. The
    # cloudify-mist plugin should define a `Script` node type to execute
    # operations on scripts, such as uploading, deleting, etc.
    script_id = ctx.instance.runtime_properties.pop('script_id', '')
    if script_id:
        try:
            MistConnectionClient().client.remove_script(script_id)
        except Exception as exc:
            ctx.logger.warn('Failed to remove installation script: %r', exc)


def prepare_kubernetes_script():
    """Upload kubernetes installation script, if missing.

    This method is executed at the very beginning, in a pre-configuration
    phase, to make sure that the kubernetes installation script has been
    uploaded to mist.io.

    This method is meant to be invoked early on by:

        configure_kubernetes_master()
        configure_kubernetes_worker()

    The script_id inside each instance's runtime properties is used later
    on in order to configure kubernetes on the provisioned machines.

    """
    if ctx.instance.runtime_properties.get('script_id'):
        ctx.logger.info('Kubernetes installation script already exists')
    else:
        ctx.logger.info('Uploading fresh kubernetes installation script')
        # If a script_id does not exist in the node instance's runtime
        # properties, perhaps because this is the first node that is being
        # configured, load the script from file, upload it to mist.io, and
        # run it over ssh.
        client = MistConnectionClient().client
        script = os.path.join(os.path.dirname(__file__), 'deploy-node.sh')
        ctx.download_resource(
            os.path.join('scripts', 'deploy-node.sh'), script
        )
        with open(os.path.abspath(script)) as fobj:
            script = fobj.read()
        script = client.add_script(
            name='install_kubernetes_%s' % random_string(length=4),
            script=script, location_type='inline', exec_type='executable'
        )
        ctx.instance.runtime_properties['script_id'] = script['id']


def configure_kubernetes_master():
    """Configure the kubernetes master.

    Sets up the master node and stores the necessary settings inside the node
    instance's runtime properties, which are required by worker nodes in order
    to join the kubernetes cluster.

    """
    ctx.logger.info('Setting up kubernetes master node')
    prepare_kubernetes_script()

    conn = MistConnectionClient()
    machine = conn.get_machine(
        cloud_id=ctx.instance.runtime_properties['cloud_id'],
        machine_id=ctx.instance.runtime_properties['machine_id'],
    )

    # Token for secure master-worker communication.
    token = '%s.%s' % (random_string(length=6), random_string(length=16))
    ctx.instance.runtime_properties['master_token'] = token.lower()

    # Store kubernetes dashboard credentials in runtime properties.
    ctx.instance.runtime_properties.update({
        'auth_user': ctx.node.properties['auth_user'],
        'auth_pass': ctx.node.properties['auth_pass'] or random_string(10),
    })

    ctx.logger.info('Installing kubernetes on master node')

    # Prepare script parameters.
    params = "-u '%s' " % ctx.instance.runtime_properties['auth_user']
    params += "-p '%s' " % ctx.instance.runtime_properties['auth_pass']
    params = "-n '%s' " % ctx.instance.runtime_properties['machine_name']
    params += "-t '%s' " % ctx.instance.runtime_properties['master_token']
    params += "-r 'master'"

    # Run the script.
    script = conn.client.run_script(
        script_id=ctx.instance.runtime_properties['script_id'], su=True,
        machine_id=machine.id,
        cloud_id=machine.cloud.id,
        script_params=params,
    )
    ctx.instance.runtime_properties['job_id'] = script['job_id']


def configure_kubernetes_worker():
    """Configure a new kubernetes node.

    Configures a new worker node and connects it to the kubernetes master.

    """
    # Get master node from relationships schema.
    master = ctx.instance.relationships[0]._target.instance
    ctx.instance.runtime_properties.update({
        'script_id': master.runtime_properties.get('script_id', ''),
        'master_ip': master.runtime_properties.get('master_ip', ''),
        'master_token': master.runtime_properties.get('master_token', ''),
    })

    ctx.logger.info('Setting up kubernetes worker')
    prepare_kubernetes_script()

    conn = MistConnectionClient()
    machine = conn.get_machine(
        cloud_id=ctx.instance.runtime_properties['cloud_id'],
        machine_id=ctx.instance.runtime_properties['machine_id'],
    )

    ctx.logger.info('Configuring kubernetes node')

    # Prepare script parameters.
    params = "-m '%s' " % ctx.instance.runtime_properties['master_ip']
    params += "-n '%s' " % ctx.instance.runtime_properties['machine_name']
    params += "-t '%s' " % ctx.instance.runtime_properties['master_token']
    params += "-r 'node'"

    # Run the script.
    script = conn.client.run_script(
        script_id=ctx.instance.runtime_properties['script_id'], su=True,
        machine_id=machine.id,
        cloud_id=machine.cloud.id,
        script_params=params,
    )
    ctx.instance.runtime_properties['job_id'] = script['job_id']


if __name__ == '__main__':
    """Setup kubernetes on the machines defined by the blueprint."""
    conn = MistConnectionClient()
    cloud = conn.get_cloud(ctx.instance.runtime_properties['cloud_id'])
    if cloud.provider in constants.CLOUD_INIT_PROVIDERS:
        wait_for_event(
            job_id=ctx.instance.runtime_properties['job_id'],
            job_kwargs={
                'action': 'cloud_init_finished',
                'machine_name': ctx.instance.runtime_properties['machine_name']
            }
        )
    elif not ctx.node.properties['configured']:
        if not ctx.node.properties['master']:
            configure_kubernetes_worker()
        else:
            configure_kubernetes_master()
        try:
            wait_for_event(
                job_id=ctx.instance.runtime_properties['job_id'],
                job_kwargs={
                    'action': 'script_finished',
                    'external_id': ctx.instance.runtime_properties[
                        'machine_id'],
                }
            )
        except Exception:
            remove_kubernetes_script()
            raise
        else:
            remove_kubernetes_script()
        ctx.logger.info('Kubernetes installation succeeded!')
    else:
        ctx.logger.info('Kubernetes already configured')
