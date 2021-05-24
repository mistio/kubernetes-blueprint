from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs


def graph_scale_down_workflow(delta):
    """Scale down the kubernetes cluster.

    A maximum number of `delta` nodes will be removed from the cluster.

    """
    # Set the workflow to be in graph mode.
    graph = workctx.graph_mode()

    # Get a maximum of `delta` number of workers.
    node = workctx.get_node('kube_worker')
    instances = [instance for instance in node.instances][:delta]

    # Setup events to denote the beginning and end of tasks.
    start_events, done_events = {}, {}

    for i, instance in enumerate(instances):
        start_events[i] = instance.send_event('Removing node cluster')
        done_events[i] = instance.send_event('Node removed from cluster')

    # Create `delta` number of TaskSequence objects. That way we are able to
    # control the sequence of events and the dependencies amongst tasks. One
    # graph sequence corresponds to node being removed from the cluster.
    for i, instance in enumerate(instances):
        sequence = graph.sequence()
        sequence.add(
            start_events[i],
            instance.execute_operation(
                operation='cloudify.interfaces.lifecycle.stop',
            ),
            instance.execute_operation(
                operation='cloudify.interfaces.lifecycle.delete',
            ),
            instance.set_state('deleted'),
            done_events[i],
        )

    # Start execution.
    return graph.execute()


if __name__ == '__main__':
    delta = int(inputs.get('delta') or 0)
    workctx.logger.info('Scaling kubernetes cluster down by %d node(s)', delta)
    if delta:
        graph_scale_down_workflow(delta)
