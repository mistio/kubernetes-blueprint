import os

from cloudify import ctx
from cloudify.exceptions import NonRecoverableError

from plugin import constants
from plugin.utils import random_string
from plugin.utils import generate_name
from plugin.utils import get_stack_name

from plugin.server import create_machine
from plugin.connection import MistConnectionClient


def prepare_cloud_init():
    """"""
    if ctx.node.properties['master']:
        arguments = get_master_init_args()
    else:
        arguments = get_worker_init_args()

    ctx.logger.debug('Will run mega-deploy.sh with: %s', arguments)
    ctx.instance.runtime_properties['cloud_init_arguments'] = arguments

    ctx.logger.debug('Current runtime: %s', ctx.instance.runtime_properties)

    ctx.logger.info('Rendering cloud-init.yml')

    cloud_init = os.path.join(os.path.dirname(__file__), 'cloud_init.yml')
    ctx.download_resource_and_render(
        os.path.join('cloud-init', 'cloud-init.yml'), cloud_init
    )
    with open(os.path.abspath(cloud_init)) as fobj:
        ctx.instance.runtime_properties['cloud_init'] = fobj.read()


def get_master_init_args():
    """"""
    ctx.logger.info('Preparing cloud-init for kubernetes master')

    # Token for secure master-worker communication.
    token = '%s.%s' % (random_string(length=6), random_string(length=16))
    ctx.instance.runtime_properties['master_token'] = token.lower()

    # Store kubernetes dashboard credentials in runtime properties.
    ctx.instance.runtime_properties.update({
        'auth_user': ctx.node.properties['auth_user'],
        'auth_pass': ctx.node.properties['auth_pass'] or random_string(10),
    })

    #
    arguments = "-u '%s' " % ctx.instance.runtime_properties['auth_user']
    arguments += "-p '%s' " % ctx.instance.runtime_properties['auth_pass']
    arguments += "-t '%s' " % ctx.instance.runtime_properties['master_token']
    arguments += "-r 'master'"

    return arguments

def get_worker_init_args():
    """"""
    ctx.logger.info('Preparing cloud-init for kubernetes worker')

    # Get master node from relationships schema.
    master = ctx.instance.relationships[0]._target.instance

    #
    ctx.instance.runtime_properties.update({
        'master_ip': master.runtime_properties.get('master_ip', ''),
        'master_token': master.runtime_properties.get('master_token', ''),
    })

    #
    arguments = "-m '%s' " % master.runtime_properties['master_ip']
    arguments += "-t '%s' " % master.runtime_properties['master_token']
    arguments += "-r 'node'"

    return arguments


if __name__ == '__main__':
    """"""
    # FIXME Re-think this.
    conn = MistConnectionClient()
    ctx.instance.runtime_properties['job_id'] = conn.client.job_id

    #
    name = generate_name(
        get_stack_name(),
        'master' if ctx.node.properties['master'] else 'worker'
    )
    ctx.instance.runtime_properties['machine_name'] = name

    if conn.cloud.provider in constants.CLOUD_INIT_PROVIDERS:
        prepare_cloud_init()

    if ctx.node.properties['master']:
        create_machine(
            name=name,
            node_type='master',
            cloud_init=ctx.instance.runtime_properties.get('cloud_init', '')
        )

        # Get the master node's IP address. NOTE We prefer to use private IPs.
        ips = (ctx.instance.runtime_properties['info']['private_ips'] +
               ctx.instance.runtime_properties['info']['public_ips'])
        ips = filter(lambda ip: ':' not in ip, ips)
        if not ips:
            raise NonRecoverableError('No IPs associated with the machine')

        ctx.instance.runtime_properties['master_ip'] = ips[0]
    else:
        create_machine(
            name=name,
            node_type='worker',
            cloud_init=ctx.instance.runtime_properties.get('cloud_init', '')
        )
