from cloudify import ctx
from cloudify.state import ctx_parameters as params


if 'minions' not in ctx.instance.runtime_properties:
    ctx.instance.runtime_properties['minions'] = []
ctx.instance.runtime_properties['minions'].append(params.minion_id)
