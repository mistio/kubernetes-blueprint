import os
import sys

from cloudify import ctx

from plugin.utils import random_string
from plugin.utils import wait_for_event

from plugin.connection import MistConnectionClient


def reset_kubeadm():
    """Uninstall kubernetes on a node.

    Runs `kubeadm reset` on the specified machine in order to remove the
    kubernetes services and undo all configuration set by `kubeadm init`.

    """
    # Get script from path.
    script = os.path.join(os.path.dirname(__file__), 'mega-reset.sh')
    ctx.download_resource(
        os.path.join('scripts', 'mega-reset.sh'), script
    )

    # Get worker.
    conn = MistConnectionClient()
    machine = conn.get_machine(
        cloud_id=ctx.instance.runtime_properties['cloud_id'],
        machine_id=ctx.instance.runtime_properties['machine_id'],
    )

    ctx.logger.info('Running "kubeadm reset" on %s', machine)

    _add_run_remove_script(
        cloud_id=machine.cloud.id,
        machine_id=machine.id,
        script_path=os.path.abspath(script),
        script_name='kubeadm_reset_%s' % random_string(length=4)
    )


def drain_and_remove():
    """Mark the node as unschedulable, evict all pods, and remove it.

    Runs `kubectl drain` and `kubectl delete nodes` on the kubernetes
    master in order to drain and afterwards remove the specified node
    from the cluster.

    """
    if ctx.node.properties['master']:  # FIXME Is this necessary?
        return

    # Get master instance.
    master = ctx.instance.relationships[0]._target.instance

    # Render script.
    script = os.path.join(os.path.dirname(__file__), 'drain-node.sh')
    ctx.download_resource_and_render(
        os.path.join('scripts', 'drain-node.sh'), script,
        template_variables={
            'hostname': ctx.instance.runtime_properties.get(
                'machine_name', '').lower()
        },
    )

    conn = MistConnectionClient()
    machine = conn.get_machine(
        cloud_id=master.runtime_properties['cloud_id'],
        machine_id=master.runtime_properties['machine_id'],
    )

    ctx.logger.info('Running "kubectl drain && kubectl delete" on %s', machine)

    _add_run_remove_script(
        cloud_id=machine.cloud.id,
        machine_id=machine.id,
        script_path=os.path.abspath(script),
        script_name='kubectl_drain_%s' % random_string(length=4)
    )


# TODO This should be moved to the cloudify-mist-plugin as a generic method.
# along with all related script stuff in tasks/create.py
def _add_run_remove_script(cloud_id, machine_id, script_path, script_name):
    """Helper method to add a script, run it, and, finally, remove it."""
    conn = MistConnectionClient()

    # Upload script.
    with open(script_path) as fobj:
        script = conn.client.add_script(
            name=script_name, script=fobj.read(),
            location_type='inline', exec_type='executable'
        )

    # Run the script.
    job = conn.client.run_script(script_id=script['id'], machine_id=machine_id,
                                 cloud_id=cloud_id, su=True)

    # Wait for the script to exit. The script should exit fairly quickly,
    # thus we only wait for a couple of minutes for the corresponding log
    # entry.
    try:
        wait_for_event(
            job_id=job['job_id'],
            job_kwargs={
                'action': 'script_finished',
                'external_id': machine_id,
            },
            timeout=180
        )
    except Exception:
        ctx.logger.warn('Script %s finished with errors!', script_name)
    else:
        ctx.logger.info('Script %s finished successfully', script_name)

    # Remove the script.
    try:
        conn.client.remove_script(script['id'])
    except Exception as exc:
        ctx.logger.warn('Failed to remove script %s: %r', script_name, exc)


if __name__ == '__main__':
    """Remove the node from cluster and uninstall the kubernetes services

    Initially, all resources will be drained from the kubernetes node and
    afterwards the node will be removed from the cluster.

    The `reset_kubeadm` method will only run in case an already existing
    resource has been used in order to setup the kubernetes cluster. As
    we do not destroy already existing resources, which have been used to
    setup kubernetes, we opt for uninstall the corresponding kubernetes
    services and undoing all configuration in order to bring the machines
    to their prior state.

    If `use_external_resource` is False, then this method is skipped and
    the resources will be destroyed later on.

    """
    drain_and_remove()
    if ctx.instance.runtime_properties.get('use_external_resource'):
        reset_kubeadm()
