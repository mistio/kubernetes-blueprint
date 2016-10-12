from cloudify import ctx
from cloudify.state import ctx_parameters as params
from cloudify.exceptions import NonRecoverableError


if params.action == 'associate':
    master_token = ctx.instance.runtime_properties.get('master_token', '')
    if not master_token:
        ctx.logger.error('Failed to find Token. '
                         'Worker-Master communication will fail')
        raise NonRecoverableError('Token is missing')
    ctx.logger.info('Kubernetes Token found')
    return master_token
elif params.action == 'disassociate':
    username = ctx.instance.runtime_properties.get('auth_user', '')
    password = ctx.instance.runtime_properties.get('auth_pass', '')
    if not (username or password):
        ctx.logger.warn('Basic Authentication credentials may be incomplete. '
                        'Kubernetes nodes may not be properly disassociated '
                        'from the cluster. Will try anyway...')
    return username, password
