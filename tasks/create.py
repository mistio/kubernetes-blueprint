import os
import uuid

from cloudify import ctx
from cloudify.exceptions import NonRecoverableError

from plugin import constants
from plugin.utils import random_string
from plugin.server import create_operation
from plugin.connection import MistConnectionClient


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
    ctx.logger.debug('Will run mega-deploy.sh with: %s', arguments)
    data = [
        ('action', 'cloud_init_finished'),
        ('machine_name', '$instance-id'),
    ]
    data = ['"%s=%s"' % (k, v) for k, v in data]
    data = '--data-urlencode ' + ' --data-urlencode '.join(data)
    ctx.instance.runtime_properties['cloud_init_data'] = data

    #
    url = '"%s/api/v1/jobs/%s"' % (ctx.node.properties['mist_config']['mist_uri'],
                                   ctx.instance.runtime_properties['job_id'])
    ctx.instance.runtime_properties['cloud_init_url'] = url

    #
    header = '-H "Authorization: %s"' % ctx.node.properties['mist_config']['mist_token']
    ctx.instance.runtime_properties['cloud_init_headers'] = header
    ctx.instance.runtime_properties['cloud_init_arguments'] = arguments

    ctx.logger.error('@@@@@@@@@@@@@@@@@@@@@@@ %s', ctx.instance.runtime_properties)
    #
    cloud_init = os.path.join(os.path.dirname(__file__), 'cloud_init.yml')
    ctx.download_resource_and_render(
        os.path.join('cloud-init', 'cloud-init.yml'), cloud_init
    )
    with open(os.path.abspath(cloud_init)) as fobj:
        ctx.instance.runtime_properties['cloud_init'] = fobj.read()


if __name__ == '__main__':
    """"""
    # FIXME Re-think this.
    conn = MistConnectionClient()
    ctx.instance.runtime_properties['job_id'] = conn.client.job_id

    if conn.cloud.provider in constants.CLOUD_INIT_PROVIDERS:
        prepare_cloud_init()

    # Get the master node's IP address. NOTE We prefer to use private IPs.
    if ctx.node.properties['master']:
        create_operation(
            node_type='master',
            cloud_init=ctx.instance.runtime_properties.get('cloud_init', '')
        )

        ips = (ctx.instance.runtime_properties['info']['private_ips'] +
               ctx.instance.runtime_properties['info']['public_ips'])
        ips = filter(lambda ip: ':' not in ip, ips)
        if not ips:
            raise NonRecoverableError('No IPs associated with the machine')

        ctx.instance.runtime_properties['master_ip'] = ips[0]
    else:
        create_operation(
            node_type='worker',
            cloud_init=ctx.instance.runtime_properties.get('cloud_init', '')
        )
