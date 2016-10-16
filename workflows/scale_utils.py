import os
import json
import glob

import string
import random


CONSTANTS = {
    'CREATE_TIMEOUT': 60 * 10,
    'SCRIPT_TIMEOUT': 60 * 30
}

STORAGE = 'local-storage/local/node-instances/%s_[A-Za-z0-9]*'


class LocalStorage(object):
    """
    LocalStorage gives full access to a node instance's properties by reading
    the instance object directly from file
    """
    def __init__(self, node):
        """
        Searches for the file in local-storage that corresponds to the node
        provided
        """
        instace_file = self.fetch_instance_file(node)
        print '*******************' + instance_file
        with open(instance_file, 'r') as _instance:
            instance_from_file = _instance.read()

        self.instance_from_file = json.loads(instance_from_file)

    @classmethod
    def get(cls, node):
        """
        A class method to initiate the LocalStorage with the specified node
        """
        node = cls(node)
        return node

    @property
    def runtime_properties(self):
        """
        Returns the node instance's runtime properties in a way similiar to
        `ctx`
        """
        print '************' + self.instance_from_file['runtime_properties']
        return self.instance_from_file['runtime_properties']

    def fetch_instance_file(self, node):
        """
        Tries to discover the path of local-storage in order to fetch the
        required node instance
        """
        local_storage = os.path.join('/tmp/templates',
                                     'mistio-kubernetes-blueprint-[A-Za-z0-9]*',
                                     STORAGE % node)
        local_storage = glob.glob(local_storage)
        if local_storage:
            local_storage = local_storage[0]
        # TODO: Well, this is weird, but the local-storage exists on a different
        # path in case a user executes `cfy local` directly from his terminal
        else:
            if not os.path.exists(os.path.join('..', STORAGE % node)):
                raise Exception('Failed to locate local-storage')
            node_file = os.path.join('..', STORAGE % node)
        print '****************************' + node_file
        return node_file


def random_chars(length=4):
    """Create a random alphanumeric string"""
    _chars = string.letters + string.digits
    return ''.join(random.choice(_chars) for _ in range(4))


def random_name(length=4):
    """Generate random names for new Kubernetes nodes"""
    return 'MistCfyNode-%s-%s' % (random_chars(length), random_chars(length))

