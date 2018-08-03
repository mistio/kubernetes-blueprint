from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs

from plugin import constants


def graph_scale_up_workflow(delta):
    """Scale up the kubernetes cluster.

    This method implements the scale up workflow using the Graph Framework.

    Scaling is based on the `delta` input, which must be greater than 0 for
    the workflow to run.

    """
    # Set the workflow to be in graph mode.
    graph = workctx.graph_mode()

    # Get the instance for which to add an execute operation task to the graph.
    node = workctx.get_node('kube_worker')
    instance = [instance for instance in node.instances][0]

    # Setup events to denote the beginning and end of tasks. The events will be
    # also used to control dependencies amongst tasks.
    start_events, done_events = {}, {}

    for i in range(delta):
        start_events[i] = instance.send_event('Adding node to cluster')
        done_events[i] = instance.send_event('Node added to cluster')

    # Prepare the operations' kwargs.
    worker_data = inputs.get('mist_machine_worker', {})

    if worker_data.get('machine_id'):
        operation_kwargs_list = [
            {
                'cloud_id': worker_data.get('cloud_id', ''),
                'machine_id': worker_data['machine_id'],
            }
        ]
    else:
        operation_kwargs_list = [
            {
                'cloud_id': worker_data.get('cloud_id', ''),
                'image_id': worker_data.get('image_id', ''),
                'size_id': worker_data.get('size_id', ''),
                'location_id': worker_data.get('location_id', ''),
                'key_id': worker_data.get('key_id', ''),
                'networks': worker_data.get('networks', []),
                'machine_id': '',
            }
        ]

    # Create `delta` number of TaskSequence objects. That way we are able to
    # control the sequence of events and the dependencies amongst tasks. One
    # graph sequence corresponds to a new node added to the cluster.
    for i in range(delta):
        sequence = graph.sequence()
        sequence.add(
            start_events[i],
            instance.execute_operation(
                operation='cloudify.interfaces.lifecycle.clone',
            ),
            instance.execute_operation(
                operation='cloudify.interfaces.lifecycle.create',
                kwargs=operation_kwargs_list[i],
            ),
            instance.execute_operation(
                operation='cloudify.interfaces.lifecycle.configure',
            ),
            done_events[i],
        )

    # Now, we use the events to control the tasks' dependencies, ensuring that
    # tasks are executed in the correct order. We aim to create dependencies
    # between a sequence's last event and the next sequence's initial event.
    # That way, we ensure that sequences are executed sequentially, and not in
    # parallel. This is required, since the cloudify.interfaces.lifecycle.clone
    # operation modifies the node instances in local-storage and we want to
    # avoid having multiple tasks messing with the same files at the same time.
    for i in range(delta - 1):
        graph.add_dependency(start_events[i + 1], done_events[i])

    # Start execution.
    return graph.execute()


if __name__ == '__main__':
    delta = int(inputs.get('delta') or 1)
    workctx.logger.info('Scaling kubernetes cluster up by %d node(s)', delta)
    if delta:
        graph_scale_up_workflow(delta)
