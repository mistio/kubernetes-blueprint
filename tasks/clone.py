from cloudify import ctx

from plugin.utils import LocalStorage


if __name__ == '__main__':
    # FIXME HACK This operation is required by the scale_up workflow. It tries
    # to mimic - in a very simple, dummy way - the functionality of Deployment
    # Modification. Deployment Modification changes the data model by adding or
    # removing node instances, and returns the modified node instances for the
    # workflow to operate on them. However, the Deployment Modification does
    # not work for local deployments. Instead, it requires an active Cloudify
    # Manager. The built-in scale workflow makes use of this API in order to
    # scale a node instance up or down. More on Deployment Modification here:
    # https://docs.cloudify.co/4.2.0/workflows/creating-your-own-workflow/. In
    # our case, we are creating an exact copy of the specified node instance so
    # that we may re-execute the node's operations.
    storage = LocalStorage()
    storage.clone_node_instance(ctx.instance.id)
