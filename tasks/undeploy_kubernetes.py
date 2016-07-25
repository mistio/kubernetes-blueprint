from cloudify import ctx
from cloudify.exceptions import NonRecoverableError

import sys

try:
    import connection  # FIXME
except ImportError:
    sys.path.insert(0, 'lib/python2.7/site-packages/plugin/')
    import connection


try:
    # destroying kubernetes master
    client = connection.MistConnectionClient()
    client.machine.destroy()
    # destroying extra minions
    minions = ctx.instance.runtime_properties.get('minions', [])
    if minions:
        client.undeploy_minions(minions)
except Exception as exc:
    raise NonRecoverableError(exc)
