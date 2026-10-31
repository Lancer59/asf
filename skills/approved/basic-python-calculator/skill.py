def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b

def divide(a, b):
    if b == 0:
        raise ValueError('Division by zero is not allowed')
    return a / b

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

    try:
        operations = {
            'add': add,
            'subtract': subtract,
            'multiply': multiply,
            'divide': divide
        }
        result = operations[operation](a, b)
        return {
            'success': True,
            'operation': operation,
            'a': a,
            'b': b,
            'result': result,
            'error': None
        }
    except Exception as e:
        return {
            'success': False,
            'operation': operation,
            'a': a,
            'b': b,
            'result': None,
            'error': str(e)
        }