from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs


def graph_scale_workflow(delta):
    """Scale up the kubernetes cluster.

    This method implements the scale up workflow using the Graph Framework.

    Scaling is based on the `delta` input, which must be greater than 0 for
    the workflow to run.

    """
    graph = workctx.graph_mode()

    start_events, done_events = {}, {}

    node = workctx.get_node('kube_worker')
    instance = [instance for instance in node.instances][0]

    for i in range(delta):
        start_events[i] = instance.send_event('Adding node to cluster')
        done_events[i] = instance.send_event('Node added to cluster')

    for i in range(delta):
        sequence = graph.sequence()
        sequence.add(
            start_events[i],
            instance.execute_operation(
                operation='cloudify.interfaces.lifecycle.scale',
                kwargs={
                    'cloud_id': inputs.get('mist_cloud', ''),
                    'image_id': inputs.get('mist_image', ''),
                    'size_id': inputs.get('mist_size', ''),
                    'location_id': inputs.get('mist_location'),
                    'networks': inputs.get('mist_networks', []),
                    'key': inputs.get('mist_key', ''),
                },
            ),
            done_events[i],
        )

    for i in range(delta - 1):
        graph.add_dependency(
            done_events[i],
            start_events[i + 1],
        )

    return graph.execute()


if __name__ == '__main__':
    delta = int(inputs.get('delta') or 0)
    workctx.logger.info('Scaling kubernetes cluster up by %d node(s)', delta)
    if delta:
        graph_scale_workflow(delta)
