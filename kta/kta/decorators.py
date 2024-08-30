import logging
import functools


# Decorator to log input and output of a function
def log_io(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Log input arguments
        logging.info(f"Function '{func.__name__}' called with arguments: {args}, {kwargs}")
        # Call the original function
        result = func(*args, **kwargs)
        # Log the output
        logging.info(f"Function '{func.__name__}' returned: {result}")
        return result

    return wrapper
