import os

from cloudify import ctx

from plugin import constants
from plugin.utils import generate_name
from plugin.utils import get_stack_name
from plugin.utils import wait_for_event

from plugin.server import create_machine
from plugin.connection import MistConnectionClient


if __name__ == '__main__':
    """"""
    # FIXME Re-think this.
    conn = MistConnectionClient()
    ctx.instance.runtime_properties['job_id'] = conn.client.job_id

    #
    node_properties = ctx.node.properties.copy()

    # Generate a somewhat random machine name. NOTE that we need the name at
    # this early point in order to be passed into cloud-init, if used, so that
    # we may use it later on to match log entries.
    name = generate_name(get_stack_name(), 'worker')
    node_properties['parameters']['name'] = name
    ctx.instance.runtime_properties['machine_name'] = name

    # Generate cloud-init, if supported.
    if conn.cloud.provider in constants.CLOUD_INIT_PROVIDERS:
        cloud_init = os.path.join(os.path.dirname(__file__), 'cloud_init.yml')
        ctx.download_resource_and_render(
            os.path.join('cloud-init', 'cloud-init.yml'), cloud_init
        )
        with open(os.path.abspath(cloud_init)) as fobj:
            cloud_init = fobj.read()
        node_properties['parameters']['cloud_init'] = cloud_init
        ctx.instance.runtime_properties['cloud_init'] = cloud_init

    # Create the nodes. Get the master node's IP address. NOTE that we prefer
    # to use private IP addresses.
    create_machine(node_properties, node_type='worker')

    # FIXME Re-think this.
    if conn.cloud.provider in constants.CLOUD_INIT_PROVIDERS:
        wait_for_event(
            job_id=ctx.instance.runtime_properties['job_id'],
            job_kwargs={
                'action': 'cloud_init_finished',
                'machine_name': ctx.instance.runtime_properties['machine_name']
            }
        )
    elif not ctx.node.properties['configured']:
        ctx.logger.info('Configuring kubernetes node')

        # Prepare script parameters.
        script_params = "-m '%s' " % ctx.instance.runtime_properties['master_ip']
        script_params += "-t '%s' " % ctx.instance.runtime_properties['master_token']  # NOQA
        script_params += "-r 'node'"

        # Run the script.
        script = client.run_script(
            script_id=ctx.instance.runtime_properties['script_id'], su=True,
            machine_id=ctx.instance.runtime_properties['machine_id'],
            cloud_id=ctx.instance.runtime_properties['cloud_id'],
            script_params=script_params,
        )
        wait_for_event(
            job_id=ctx.instance.runtime_properties['job_id'],
            job_kwargs={
                'action': 'script_finished',
                'machine_id': ctx.instance.runtime_properties['machine_id'],
            }
        )
        ctx.logger.info('Kubernetes installation succeeded!')
    else:
        ctx.logger.info('Kubernetes already configured')
