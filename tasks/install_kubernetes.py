from cloudify import ctx
from cloudify.exceptions import NonRecoverableError

import sys
import os
import uuid
import pkg_resources
import glob

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
    kubernetes_script = pkg_resources.resource_string(resource_package, resource_path)
except IOError:
    # This path is for executions performed by Mist.io
    tmp_dir = os.path.join('/tmp/templates',
                           'mistio-kubernetes-blueprint-[A-Za-z0-9]*',
                           'scripts')
    scripts_dir = glob.glob(tmp_dir)[0]
    resource_path = os.path.join(scripts_dir, 'mega-deploy.sh')
    with open(resource_path) as f:
        kubernetes_script = f.read()


SCRIPT_TIMEOUT = 60 * 30


def random_string(length=6):
    _chars = string.letters + string.digits
    return ''.join(random.choice(_chars) for _ in range(length))

# TODO deprecate this!
client = connection.MistConnectionClient().client
machine = connection.MistConnectionClient().machine

is_configured = ctx.node.properties['configured']
is_master = ctx.node.properties['master']
kube_type = 'master' if is_master else 'worker'

ctx.logger.info('Setting up Kubernetes %s Node...', kube_type.upper())

if is_master:
    # Master's private IP
    ctx.instance.runtime_properties['master_ip'] = \
        machine.info['private_ips'][0]
    # Token for secure Master-Worker communication
    ctx.instance.runtime_properties['master_token'] = \
        '%s.%s' % (random_string(), random_string())
else:
    kube_master = ctx.instance.relationships[0]._target.instance
    # Master's private IP
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
        script_name = 'install_kubernetes_%s' % uuid.uuid1().hex 
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
        if job['error']:
            # Print entire output only in case an error has occured
            _stdout = job['logs'][2]['stdout']
            _extra_stdout = job['logs'][2]['extra_output']
            _stdout += _extra_stdout if _extra_stdout else ''
            ctx.logger.error(_stdout)
            raise NonRecoverableError('Kubernetes %s installation failed',
                                      kube_type.upper())
        if time() > started_at + SCRIPT_TIMEOUT:
            _stdout = job['logs'][2]['stdout']
            _extra_stdout = job["logs"][2]['extra_output']
            _stdout += _extra_stdout if _extra_stdout else ''
            ctx.logger.debug(_stdout)
            raise NonRecoverableError('Kubernetes %s installation script '
                                      'is taking too long! Giving up...',
                                      kube_type.upper())
        if job['finished_at']:
            break

        ctx.logger.info('Waiting for Kubernetes %s installation to finish...',
                        kube_type.upper())
        sleep(5)
        job = client.get_job(job_id)

    ctx.logger.info('Kubernetes %s installation succeeded!', kube_type.upper())

