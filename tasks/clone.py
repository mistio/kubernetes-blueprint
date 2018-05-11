from cloudify import ctx

from plugin.utils import LocalStorage


if __name__ == '__main__':
    # FIXME HACK
    storage = LocalStorage()
    storage.copy_node_instance(ctx.instance.id)
