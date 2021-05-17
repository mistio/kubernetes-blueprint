import os

from cloudify import ctx
from cloudify.state import ctx_parameters as params
from cloudify.exceptions import NonRecoverableError

from plugin import constants
from plugin.utils import random_string
from plugin.utils import generate_name
from plugin.utils import get_stack_name
from plugin.utils import is_resource_external

from plugin.server import get_cloud_id
from plugin.server import create_machine
from plugin.connection import MistConnectionClient


def prepare_cloud_init():
    """Render the cloud-init script.

    This method is executed if the cloud provider is included in the
    CLOUD_INIT_PROVIDERS in order to prepare the cloud-init that is
    used to install kubernetes on each of the provisioned VMs at boot
    time.

    This method, based on each node's type, is meant to invoke:

        get_master_init_args()
        get_worker_init_args()

    in order to get the arguments required by the kubernetes installation
    script.

    The cloud-init.yml is just a wrapper around the mega-deploy.sh, which
    is provided as a parameter at VM provision time. In return, we avoid
    the extra step of uploading an extra script and executing it over SSH.

    """
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
    """Return the arguments required to install the kubernetes master."""

    ctx.logger.info('Preparing cloud-init for kubernetes master')

    # Token for secure master-worker communication.
    token = '%s.%s' % (random_string(length=6), random_string(length=16))
    ctx.instance.runtime_properties['master_token'] = token.lower()

    # Store kubernetes dashboard credentials in runtime properties.
    ctx.instance.runtime_properties.update({
        'auth_user': ctx.node.properties['auth_user'],
        'auth_pass': ctx.node.properties['auth_pass'] or random_string(10),
    })

    arguments = "-t '%s' " % ctx.instance.runtime_properties['master_token']
    arguments += "-r 'master'"

    return arguments


def get_worker_init_args():
    """Return the arguments required to install a kubernetes worker."""

    ctx.logger.info('Preparing cloud-init for kubernetes worker')

    # Get master node from relationships schema.
    master = ctx.instance.relationships[0]._target.instance
    ctx.instance.runtime_properties.update({
        'master_ip': master.runtime_properties.get('master_ip', ''),
        'master_token': master.runtime_properties.get('master_token', ''),
    })

    arguments = "-m '%s' " % master.runtime_properties['master_ip']
    arguments += "-t '%s' " % master.runtime_properties['master_token']
    arguments += "-r 'node'"

    return arguments


if __name__ == '__main__':
    """Create the nodes on which to install kubernetes.

    Besides creating the nodes, this method also decides the way kubernetes
    will be configured on each of the nodes.

    The legacy way is to upload the script and execute it over SSH. However,
    if the cloud provider supports cloud-init, a cloud-config can be used as
    a wrapper around the actual script. In this case, the `configure` lifecycle
    operation of the blueprint is mostly skipped. More precisely, it just waits
    to be signalled regarding cloud-init's result and exits immediately without
    performing any additional actions.

    """
    conn = MistConnectionClient()
    ctx.instance.runtime_properties['job_id'] = conn.job_id

    # Create a copy of the node's immutable properties in order to update them.
    node_properties = ctx.node.properties.copy()

    # Override the node's properties with parameters passed from workflows.
    for key in params:
        if key in constants.INSTANCE_REQUIRED_PROPERTIES + ('machine_id', ):
            node_properties['parameters'][key] = params[key]
            ctx.logger.info('Added %s=%s to node parameters', key, params[key])

    # Generate a somewhat random machine name. NOTE that we need the name at
    # this early point in order to be passed into cloud-init, if used, so that
    # we may use it later on to match log entries.
    name = generate_name(
        get_stack_name(),
        'master' if ctx.node.properties['master'] else 'worker'
    )
    node_properties['parameters']['name'] = name
    ctx.instance.runtime_properties['machine_name'] = name

    # Get the cloud based on the node's properties.
    cloud = conn.get_cloud(get_cloud_id(node_properties))

    # Generate cloud-init, if supported.
    # TODO This is NOT going to work when use_external_resource is True. We
    # are using cloud-init to configure the newly provisioned nodes in case
    # the VMs are unreachable over SSH. If the VMs already exist, cloud-init
    # is not an option. Perhaps, we should allow to toggle cloud-init on/off
    # in some way after deciding if the VMs are accessible over the public
    # internet.
    if cloud.provider in constants.CLOUD_INIT_PROVIDERS:
        if is_resource_external(node_properties):
            raise NonRecoverableError('use_external_resource may not be set')
        prepare_cloud_init()
        cloud_init = ctx.instance.runtime_properties.get('cloud_init', '')
        node_properties['parameters']['cloud_init'] = cloud_init

    # Do not wait for post-deploy-steps to finish in case the configuration
    # is done using a cloud-init script.
    skip_post_deploy = cloud.provider in constants.CLOUD_INIT_PROVIDERS

    # Create the nodes. Get the master node's IP address. NOTE that we prefer
    # to use private IP addresses for master-worker communication. Public IPs
    # are used mostly when connecting to the kubernetes API from the outside.
    if ctx.node.properties['master']:
        create_machine(node_properties, skip_post_deploy, node_type='master')

        ips = (ctx.instance.runtime_properties['info']['private_ips'] +
               ctx.instance.runtime_properties['info']['public_ips'])
        ips = filter(lambda ip: ':' not in ip, ips)
        if not ips:
            raise NonRecoverableError('No IPs associated with the machine')

        ctx.instance.runtime_properties['master_ip'] = ips[0]
        ctx.instance.runtime_properties['server_ip'] = ips[-1]
    else:
        create_machine(node_properties, skip_post_deploy, node_type='worker')
