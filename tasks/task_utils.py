import string
import random


CONSTANTS = {
    'SCRIPT_TIMOUT': 60 * 30
}


def random_string(length=6):
    """Generate a random alphanumeric string. Default length equals 6"""
    _chars = string.letters + string.digits
    return ''.join(random.choice(_chars) for _ in range(length))

