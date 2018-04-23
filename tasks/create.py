import os
import uuid

from cloudify import ctx
from cloudify.exceptions import NonRecoverableError

from plugin import constants
from plugin.utils import random_string
from plugin.server import create_operation
from plugin.connection import MistConnectionClient


#def create(node_type):
#    """"""
#    create_operation(**{'node_type': node_type})


def prepare_cloud_init():
    """"""
    if ctx.node.properties['master']:
        ctx.logger.info('Preparing cloud-init for kubernetes master')

        # Token for secure master-worker communication.
        token = '%s.%s' % (random_string(length=6), random_string(length=16))
        ctx.instance.runtime_properties['master_token'] = token.lower()

        # Store kubernetes dashboard credentials in runtime properties.
        ctx.instance.runtime_properties.update({
            'script_id': uuid.uuid4().hex,
            'auth_user': ctx.node.properties['auth_user'],
            'auth_pass': ctx.node.properties['auth_pass'] or random_string(10),
        })

        #
        arguments = "-u '%s' " % ctx.instance.runtime_properties['auth_user']
        arguments += "-p '%s' " % ctx.instance.runtime_properties['auth_pass']
        arguments += "-t '%s' " % ctx.instance.runtime_properties['master_token']  # NOQA
        arguments += "-r 'master'"
    else:
        ctx.logger.info('Preparing cloud-init for kubernetes worker')

        # Get master node from relationships schema.
        master = ctx.instance.relationships[0]._target.instance

        #
        ctx.instance.runtime_properties.update({
            'script_id': uuid.uuid4().hex,
            'master_ip': master.runtime_properties.get('master_ip', ''),
            'master_token': master.runtime_properties.get('master_token', ''),
        })

        #
        arguments = "-m '%s' " % master.runtime_properties['master_ip']
        arguments += "-t '%s' " % master.runtime_properties['master_token']
        arguments += "-r 'node'"

    #
    ctx.instance.runtime_properties['cloud_init_arguments'] = arguments

    #
    cloud_init = os.path.join(os.path.dirname(__file__), 'cloud_init.yml')
    ctx.download_resource_and_render(
        os.path.join('scripts', 'cloud_init.yml'), cloud_init
    )
    with open(os.path.abspath(cloud_init)) as fobj:
        ctx.instance.runtime_properties['cloud_init'] = fobj.read()


if __name__ == '__main__':
    """"""
    # FIXME Re-think this.
    #
    if MistConnectionClient().cloud.provider in constants.CLOUD_INIT_PROVIDERS:
        prepare_cloud_init()

    #
    if ctx.node.properties['master']:
        create_operation(ctx=ctx, node_type='master')
        print '############## AFTER CREATE_OPERATION'
        #machine = MistConnectionClient().machine
        print '############## MistConnectionClient'

        # Filter out IPv6 addresses. NOTE We prefer to use private IPs.
        ips = (ctx.instance.runtime_properties.info['private_ips'] +
               ctx.instance.runtime_properties.info['public_ips'])
        ips = filter(lambda ip: ':' not in ip, ips)
        if not ips:
            raise NonRecoverableError('No IPs associated with the machine')

        # Master node's IP address.
        ctx.instance.runtime_properties['master_ip'] = ips[0]
    else:
        create_operation(ctx=ctx, node_type='worker')
