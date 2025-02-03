from server import Server
import time

if __name__ == '__main__':

    # Create the Server instance
    server = Server()

    # Start the Server process
    server.start()

    # Wait indefinitely until the server process completes its execution
    server.join()
