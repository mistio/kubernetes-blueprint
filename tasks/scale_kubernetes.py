from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs
from cloudify.exceptions import NonRecoverableError

import sys
import os
import uuid
import pkg_resources
import glob
import requests

from time import sleep

try:
    import connection  # FIXME
except ImportError:
    sys.path.insert(0, 'lib/python2.7/site-packages/plugin/')
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
    with open(resource_path) as f:
        install_worker_script = f.read()
    resource_path = os.path.join(scripts_dir, 'master.sh')
    with open(resource_path) as f:
        install_master_script = f.read()
    resource_path = os.path.join(scripts_dir, 'coreos_master.sh')
    with open(resource_path) as f:
        install_coreos_master_script = f.read()
    resource_path = os.path.join(scripts_dir, 'coreos_worker.sh')
    with open(resource_path) as f:
        install_coreos_worker_script = f.read()


def scale_cluster():
    delta = inputs.get('delta')
    if isinstance(delta, basestring):
        delta = int(delta)

    if delta == 0:
        workctx.logger.info('Delta parameter equals 0! No scaling will take '
                            'place')
        return
    elif delta > 0:
        workctx.logger.info('Scaling kubernetes cluster up by {0} minion '
                            'node(s)'.format(delta))
        scale_cluster_up(delta)
    elif delta < 0:
        # TODO verify that (current number of nodes) - (delta) > 0
        workctx.logger.info('Scaling kubernetes cluster down by {0} minion '
                            'node(s)'.format(abs(delta)))
        scale_cluster_down(abs(delta))


def scale_cluster_up(delta):
    master = workctx.get_node("kube_master")
    master_instance = [instance for instance in master.instances][0]
    mist_client = connection.MistConnectionClient(properties=master.properties)
    client = mist_client.client
    cloud = mist_client.cloud
    master_machine = mist_client.machine
    master_ip = master_machine.info["private_ips"][0]

    if inputs['use_external_resource']:
        machine = mist_client.other_machine(inputs)  # FIXME

    machine_name = inputs["name"]
    machines = cloud.machines(search=machine_name)
    if len(machines):
        for m in machines:
            if m.info["state"] in ["running", "stopped"]:
                raise NonRecoverableError(
                    "Machine with name {0} exists".format(machine_name))

    key = ""
    if inputs.get("key"):
        key = client.keys(search=inputs["key"])
        if len(key):
            key = key[0]
        else:
            raise NonRecoverableError("key not found")
    else:
        raise NonRecoverableError("key not found")

    image_id = inputs.get('image_id', '')
    if not image_id:
        raise NonRecoverableError('No image ID provided')

    size_id = inputs.get('size_id', '')
    if not size_id:
        raise NonRecoverableError('No size ID provided')

    location_id = inputs.get('location_id', '')
    if not location_id:
        raise NonRecoverableError('No location ID provided')

    networks = inputs.get('networks', [])

    workctx.logger.info("Deploying %d '%s' minion node(s)", delta, machine_name)
    quantity = delta
    job_id = cloud.create_machine(async=True, name=machine_name, key=key,
                                  image_id=image_id, location_id=location_id,
                                  size_id=size_id, quantity=quantity,
                                  networks=networks)

    job_id = job_id.json()["job_id"]
    job = client.get_job(job_id)
    timer = 0
    while True:
        if job["summary"]["probe"]["success"]:
            break
        if job["summary"]["create"]["error"] or job["summary"]["probe"]["error"]:
            workctx.logger.error('Error on machine creation:{0}'.format(job))
            raise NonRecoverableError("Not able to create machine")
        sleep(10)
        job = client.get_job(job_id)
        timer += 1
        if timer >= 360:
            raise NonRecoverableError("Timed-out! Not able to create machine")

    kub_type = "worker"

    if not inputs["coreos"]:
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
        cloud_id = inputs['cloud_id']

        machine_ids = []
        for i in xrange(quantity):
            machine_ids.append(job['logs'][2+i]['machine_id'])
        if not machine_ids:
            raise NonRecoverableError('Could not retrieve machine IDs')
        workctx.logger.info("Docker installation started")
        for machine_id in machine_ids:
            job_id = client.run_script(script_id=script_id, cloud_id=cloud_id,
                                       machine_id=machine_id, script_params="",
                                       su=False)
            job_id = job_id["job_id"]
            job = client.get_job(job_id)
            while True:
                if job["error"]:
                    raise NonRecoverableError("Not able to install docker")
                if job["finished_at"]:
                    break
                sleep(10)
                job = client.get_job(job_id)
            workctx.logger.info(job["logs"][2]['stdout'])
            workctx.logger.info(job["logs"][2]['extra_output'])
            workctx.logger.info("Docker installation script succeeded")

    if inputs["coreos"]:
        install_script = install_coreos_worker_script
    else:
        install_script = install_worker_script
    response = client.add_script(name="install_kubernetes_worker" + uuid.uuid1().hex,
                                 script=install_script, location_type="inline",
                                 exec_type="executable")

    for m in xrange(quantity):
        machine_name = machine_name.rsplit('-', 1)[0] if quantity > 1 else machine_name
        machine_name += '-' + str(m + 1) if quantity > 1 else ''
        inputs['name'] = machine_name
        machine = mist_client.other_machine(inputs)
        inputs['machine_id'] = machine.info['id']
        workctx.logger.info('Machine created')

        machine_id = inputs['machine_id']
        cloud_id = inputs['cloud_id']
        script_params = "-m '{0}'".format(master_ip)
        script_id = response['id']
        job_id = client.run_script(script_id=script_id, cloud_id=cloud_id,
                                   machine_id=machine_id,
                                   script_params=script_params, su=True)
        workctx.logger.info("Kubernetes worker installation started for "
                            "{0}".format(machine_name))
        job_id = job_id["job_id"]
        job = client.get_job(job_id)
        while True:
            if job["error"]:
                raise NonRecoverableError("Not able to install kubernetes worker")
            if job["finished_at"]:
                break
            sleep(10)
            job = client.get_job(job_id)
        master_instance.execute_operation('cloudify.interfaces.lifecycle.associate',
                                          kwargs={'minion_id': machine_id})
        workctx.logger.info(job["logs"][2]['stdout'])
        workctx.logger.info(job["logs"][2]['extra_output'])
        workctx.logger.info("Kubernetes worker {0} installation script succeeded".format(inputs["name"]))
    workctx.logger.info("Upscaling kubernetes cluster succeeded")


def scale_cluster_down(delta):
    master = workctx.get_node('kube_master')
    mist_client = connection.MistConnectionClient(properties=master.properties)
    cloud = mist_client.cloud
    master_machine = mist_client.machine
    master_ip = master_machine.info['public_ips'][0]

    worker_name = inputs.get('name')
    machines = cloud.machines(search=worker_name)
    if not machines:
        workctx.logger.info('%s minion node(s) already undeployed', worker_name)
        return

    workctx.logger.info('Terminating worker node(s)')
    counter = 0
    for m in machines:
        if not m.info['state'] in ('stopped', 'running'):
            continue
        counter += 1
        worker_priv_ip = m.info['private_ips'][0]
        worker_selfLink = 'ip-' + str(worker_priv_ip).replace('.', '-')
        m.destroy()
        # FIXME Basic Auth
        requests.delete("https://%s/api/v1/nodes/%s, auth=HTTPBasicAuth('%s', '%s'), verify=False"
                        % (master_ip, worker_selfLink, 
                           master.properties['auth_user'], master.properties['auth_pass']))
        if counter == delta:
            break
    workctx.logger.info('Downscaling kubernetes cluster succeeded')


scale_cluster()
