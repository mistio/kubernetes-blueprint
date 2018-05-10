from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs


def graph_scale_workflow(delta):
    """"""
    graph = workctx.graph_mode()

    send_event_starting_tasks = {}
    send_event_done_tasks = {}

    #
    node = workctx.get_node('kube_worker')
    instance = [instance for instance in node.instances][0]

    #
    for i in range(delta):
        key = 'node%d' % i
        send_event_starting_tasks[key] = instance.send_event('Adding node to cluster')
        send_event_done_tasks[key] = instance.send_event('Node added to cluster')

    #
    for i in range(delta):
        key = 'node%d' % i
        sequence = graph.sequence()
        sequence.add(
            send_event_starting_tasks[key],
            instance.execute_operation(
                operation='cloudify.interfaces.lifecycle.scale',
                kwargs={
                    'cloud_id': inputs.get('mist_cloud', ''),
                    'image_id': inputs.get('mist_image', ''),
                    'size_id': inputs.get('mist_size_1', ''),
                    'location_id': inputs.get('mist_location'),
                    'networks': inputs.get('mist_networks', []),
                    'key': inputs.get('mist_key', ''),
                },
            ),
            send_event_done_tasks[key],
        )

    #
    for i in range(delta - 1):
        instance_one = 'node%d' % (i, )
        instance_two = 'node%d' % (i + 1)
        graph.add_dependency(
            send_event_done_tasks[instance_one],
            send_event_starting_tasks[instance_two],
        )

    return graph.execute()


if __name__ == '__main__':
    """"""
    try:
        delta = int(inputs.get('delta') or 0)
    except ValueError:
        raise RuntimeError()

    if not delta:
        raise RuntimeError()

    graph_scale_workflow(delta)

    workctx.logger.info('Scaling kubernetes cluster up by %s node(s)', delta)


def scale_old(quantity):
    """"""
    #
    master = workctx.get_node('kube_master')
    master_instance = [instance for instance in master.instances][0]

    # Get node directly from local-storage in order to have access to all of
    # its runtime properties.
    master_node = LocalStorage.get('kube_master')

    # Private IP of the Kubernetes Master
    master_ip = master_node.runtime_properties['master_ip']
    master_token = master_node.runtime_properties['master_token']

    # FIXME Re-think this.
    mist_client = connection.MistConnectionClient(properties=master.properties)
    client = mist_client.client
    cloud = mist_client.cloud
    master_machine = mist_client.machine

    # # TODO
    # if inputs.get('use_external_resource', False):
    #     machine = mist_client.other_machine(inputs)

    node_properties = {}

    #
    name = generate_name(get_stack_name(), 'worker')

    node_properties['parameters']['name'] = name
    node_properties['parameters']['quantity'] = quantity

    #
    for param in ('mist_key', 'mist_network', ):
        key = param.replace('mist_', '')
        node_properties['parameters'][key] = inputs.get(param)

    for param in ('mist_size', 'mist_image', 'mist_location', ):
        key = param.replace('mist_', '') + '_id'
        node_properties['parameters'][key] = inputs.get(param)
