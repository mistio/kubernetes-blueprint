from cloudify import ctx
from cloudify.exceptions import NonRecoverableError

import sys
import os
import uuid
import pkg_resources
import glob

from time import sleep

try:
    import connection
except ImportError:
    sys.path.insert(0, 'lib/python2.7/site-packages/plugin/')   # TODO
    import connection


resource_package = __name__  # Could be any module/package name.
try:
    resource_path = os.path.join('../scripts', 'worker.sh')
    install_worker_script = pkg_resources.resource_string(resource_package, resource_path)
    resource_path = os.path.join('../scripts', 'master.sh')
    install_master_script = pkg_resources.resource_string(resource_package, resource_path)
    resource_path = os.path.join('../scripts', 'coreos_master.sh')
    install_coreos_master_script = pkg_resources.resource_string(resource_package, resource_path)
    resource_path = os.path.join('../scripts', 'coreos_worker.sh')
    install_coreos_worker_script = pkg_resources.resource_string(resource_package, resource_path)
except IOError:
    tmp_dir = os.path.join('/tmp/templates', 
                           'mistio-kubernetes-blueprint-[A-Za-z0-9]*', 
                           'scripts')
    scripts_dir = glob.glob(tmp_dir)[0]
    resource_path = os.path.join(scripts_dir, 'worker.sh')
    install_worker_script = pkg_resources.resource_string(resource_package, resource_path)
    resource_path = os.path.join(scripts_dir, 'master.sh')
    install_master_script = pkg_resources.resource_string(resource_package, resource_path)
    resource_path = os.path.join(scripts_dir, 'coreos_master.sh')
    install_coreos_master_script = pkg_resources.resource_string(resource_package, resource_path)
    resource_path = os.path.join(scripts_dir, 'coreos_worker.sh')
    install_coreos_worker_script = pkg_resources.resource_string(resource_package, resource_path)

client = connection.MistConnectionClient().client
machine = connection.MistConnectionClient().machine
if ctx.node.properties["master"]:
    kub_type = "master"
    ctx.instance.runtime_properties["master_ip"] = machine.info["private_ips"][0]
    if ctx.node.properties["coreos"]:
        install_script = install_coreos_master_script
    else:
        install_script = install_master_script
else:
    kub_type = "worker"
    ctx.instance.runtime_properties["master_ip"] = \
        ctx.instance.relationships[0]._target.instance.runtime_properties["master_ip"]
    if ctx.node.properties["coreos"]:
        install_script = install_coreos_worker_script
    else:
        install_script = install_worker_script

if not ctx.node.properties["configured"]:
    if not ctx.node.properties["coreos"]:
        script = """#!/bin/sh
        command_exists() {
        command -v "$@" > /dev/null 2>&1
        }
        if command_exists curl; then
        curl -sSL https://get.docker.com/ | sh
        elif command_exists wget; then
        wget -qO- https://get.docker.com/ | sh
        fi
        """
        response = client.add_script(name="install_docker" + kub_type + uuid.uuid1().hex,
                                     script=script, location_type="inline",
                                     exec_type="executable")
        script_id = response['id']
        machine_id = ctx.instance.runtime_properties['machine_id']
        cloud_id = ctx.node.properties['parameters']['cloud_id']
        job_id = client.run_script(script_id=script_id, cloud_id=cloud_id,
                                   machine_id=machine_id, script_params="",
                                   su=False)
        ctx.logger.info("Docker installation started")
        job_id = job_id["job_id"]
        job = client.get_job(job_id)
        while True:
            if job["error"]:
                raise NonRecoverableError("Not able to install docker")
            if job["finished_at"]:
                break
            sleep(10)
            job = client.get_job(job_id)
        ctx.logger.info(job["logs"][2]['stdout'])
        ctx.logger.info(job["logs"][2]['extra_output'])
        ctx.logger.info("Docker installation script succeeded")

    response = client.add_script(name="install_kubernetes_" + kub_type + uuid.uuid1().hex,
                                 script=install_script, location_type="inline",
                                 exec_type="executable")
    script_id = response['id']
    machine_id = ctx.instance.runtime_properties['machine_id']
    cloud_id = ctx.node.properties['parameters']['cloud_id']
    if kub_type == "master":
        script_params = ""
    else:
        script_params = "-m '{0}'".format(ctx.instance.runtime_properties["master_ip"])
    job_id = client.run_script(script_id=script_id, cloud_id=cloud_id,
                               machine_id=machine_id,
                               script_params=script_params, su=True)
    ctx.logger.info("Kubernetes {0} installation started".format(kub_type))
    job_id = job_id["job_id"]
    job = client.get_job(job_id)
    while True:
        if job["error"]:
            raise NonRecoverableError("Not able to install kubernetes {0}".format(kub_type))
        if job["finished_at"]:
            break
        sleep(10)
        job = client.get_job(job_id)
    ctx.logger.info(job["logs"][2]['stdout'])
    ctx.logger.info(job["logs"][2]['extra_output'])
    ctx.logger.info("Kubernetes {0} installation script succeeded".format(kub_type))
