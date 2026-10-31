def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        return {
            'success': False,
            'result': None,
            'error': 'Division by zero is not allowed'
        }
    return {
        'success': True,
        'result': a / b,
        'error': None
    }


def run(inputs):
    operation = inputs.get('operation')
    a = inputs.get('a')
    b = inputs.get('b')

    if operation not in {'add', 'subtract', 'multiply', 'divide'}:
        return {
            'success': False,
            'operation': operation,
            'a': a,
            'b': b,
            'result': None,
            'error': 'Unsupported operation'
        }

    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return {
            'success': False,
            'operation': operation,
            'a': a,
            'b': b,
            'result': None,
            'error': 'Inputs a and b must be numeric'
        }

    operations = {
        'add': lambda x, y: {'success': True, 'result': add(x, y), 'error': None},
        'subtract': lambda x, y: {'success': True, 'result': subtract(x, y), 'error': None},
        'multiply': lambda x, y: {'success': True, 'result': multiply(x, y), 'error': None},
        'divide': divide
    }

    outcome = operations[operation](a, b)

    return {
        'success': outcome['success'],
        'operation': operation,
        'a': a,
        'b': b,
        'result': outcome['result'],
        'error': outcome['error']
    }