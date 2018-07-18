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
    ctx.logger.info('Uploading script to reset kubeadm')

    conn = MistConnectionClient()

    # Get script from path.
    script = os.path.join(os.path.dirname(__file__), 'mega-reset.sh')
    ctx.download_resource(
        os.path.join('scripts', 'mega-reset.sh'), script
    )
    with open(os.path.abspath(script)) as fobj:
        script = fobj.read()

    # Upload script.
    script = conn.client.add_script(
        name='kubeadm_reset_%s' % random_string(length=4),
        script=script, location_type='inline', exec_type='executable'
    )

    machine = conn.get_machine(
        cloud_id=ctx.instance.runtime_properties['cloud_id'],
        machine_id=ctx.instance.runtime_properties['machine_id'],
    )

    ctx.logger.info('Running "kubeadm reset" on %s', machine)

    # Run the script.
    job = conn.client.run_script(script_id=script['id'], machine_id=machine.id,
                                 cloud_id=machine.cloud.id)

    # Wait for the script to exit. The script should exit fairly quickly,
    # thus we only wait for a couple of minutes for the corresponding log
    # entry.
    try:
        wait_for_event(
            job_id=job['job_id'],
            job_kwargs={
                'action': 'script_finished', 'machine_id': machine_id
            },
            timeout=120
        )
    except Exception:
        ctx.logger.warn('Command "kubeadm reset" finished with errors!')
    else:
        ctx.logger.info('Command "kubeadm reset" finished successfully')

    # Remove the script.
    try:
        conn.client.remove_script(script['id'])
    except Exception as exc:
        ctx.logger.warn('Failed to remove installation script: %r', exc)


if __name__ == '__main__':
    """Uninstall the kubernetes services and undo configuration settings.

    The `reset_kubeadm` method will only run in case an already existing
    resource has been used in order to setup the kubernetes cluster. As
    we do not destroy already existing resources, which have been used to
    setup kubernetes, we opt for uninstall the corresponding kubernetes
    services and undoing all configuration in order to bring the machines
    to their prior state.

    If `use_external_resource` is False, then this method is skipped and
    the resources will be destroyed later on.

    """
    if ctx.instance.runtime_properties.get('use_external_resource'):
        reset_kubeadm()
