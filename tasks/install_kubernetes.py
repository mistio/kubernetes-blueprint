import os
import sys
import glob
import time
import pkg_resources

from plugin import connection
from plugin.utils import random_string
from plugin.constants import SCRIPT_TIMEOUT

from cloudify import ctx
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
                           'mistio-kubernetes-blueprint-[A-Za-z0-9]*',
                           'scripts')
    scripts_dir = glob.glob(tmp_dir)[0]
    resource_path = os.path.join(scripts_dir, 'mega-deploy.sh')
    with open(resource_path) as f:
        kubernetes_script = f.read()


# TODO deprecate this!
client = connection.MistConnectionClient().client
machine = connection.MistConnectionClient().machine

is_configured = ctx.node.properties['configured']
is_master = ctx.node.properties['master']
kube_type = 'master' if is_master else 'worker'

ctx.logger.info('Setting up Kubernetes %s Node...', kube_type.upper())

if is_master:
    # Filter out IPv6 addresses
    private_ips = machine.info['private_ips']
    private_ips = filter(lambda ip: ':' not in ip, private_ips)
    if not private_ips:
        public_ips = machine.info['public_ips']
        public_ips = filter(lambda ip: ':' not in ip, public_ips)
    master_ip = private_ips[0] if private_ips else public_ips[0]
    # Master node's IP address
    ctx.instance.runtime_properties['master_ip'] = master_ip
    # Token for secure Master-Worker communication
    ctx.instance.runtime_properties['master_token'] = '%s.%s' % \
                                                      (random_string(length=6),
                                                       random_string(length=6))
else:
    kube_master = ctx.instance.relationships[0]._target.instance
    ctx.instance.runtime_properties['master_ip'] = \
        kube_master.runtime_properties.get('master_ip', '')
    ctx.instance.runtime_properties['master_token'] = \
        kube_master.runtime_properties.get('master_token', '')
    ctx.instance.runtime_properties['script_id'] = \
        kube_master.runtime_properties.get('script_id', '')

if not is_configured:
    if not is_master and ctx.instance.runtime_properties['script_id']:
        ctx.logger.info('Found existing Kubernetes installation script with '
                        'resource ID: %s', ctx.instance.runtime_properties[
                                           'script_id'])
    else:
        script_name = 'install_kubernetes_%s' % random_string(length=4)
        ctx.logger.info('Uploading Kubernetes installation script [%s]...',
                        script_name)
        script = client.add_script(name=script_name, script=kubernetes_script,
                                   location_type='inline',
                                   exec_type='executable')
        ctx.instance.runtime_properties['script_id'] = script['id']

    if is_master:
        passwd = ctx.node.properties.get('auth_pass', '') or random_string(10)
        ctx.instance.runtime_properties['auth_user'] = \
            ctx.node.properties['auth_user']
        ctx.instance.runtime_properties['auth_pass'] = passwd
        script_params = "-u '%s' -p '%s' -r 'master' -t '%s'" % \
                        (ctx.node.properties['auth_user'], passwd,
                         ctx.instance.runtime_properties['master_token'])
    else:
        script_params = "-m '%s' -r 'node' -t '%s'" % \
                        (ctx.instance.runtime_properties['master_ip'],
                         ctx.instance.runtime_properties['master_token'])

    ctx.logger.info('Deploying Kubernetes on %s node...', kube_type.upper())
    machine_id = ctx.instance.runtime_properties['machine_id']
    cloud_id = ctx.node.properties['parameters']['cloud_id']
    script_id = ctx.instance.runtime_properties['script_id']

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
                ctx.logger.error(_stdout)
                raise NonRecoverableError('Installation of Kubernetes failed')
        else:
            if time.time() > started_at + SCRIPT_TIMEOUT:
                raise NonRecoverableError('Kubernetes installation script is '
                                          'taking too long. Giving up')
            ctx.logger.info('Waiting for Kubernetes installation to finish')
            time.sleep(10)
            job = client.get_job(job_id)
            continue
        break

    ctx.logger.info('Kubernetes %s installation succeeded', kube_type.upper())

