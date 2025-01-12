import subprocess
import time

# Configuration
num_servers = 3  # Number of server instances to start
num_clients = 3  # Number of client instances to start
time_between_starts = 2  # Delay (seconds) between starting instances

# Paths to scripts
server_script = "C:/git/DistributedSystems/start_server.py"
client_script = "C:/git/DistributedSystems/client_register.py"

# Function to start servers
def start_servers():
    server_processes = []
    for i in range(num_servers):
        print(f"Starting server instance {i + 1}...")
        process = subprocess.Popen(["python", server_script])
        server_processes.append(process)
        time.sleep(time_between_starts)
    return server_processes

# Function to start clients
def start_clients():
    client_processes = []
    for i in range(num_clients):
        print(f"Starting client instance {i + 1}...")
        process = subprocess.Popen(["python", client_script])
        client_processes.append(process)
        time.sleep(time_between_starts)
    return client_processes

if __name__ == "__main__":
    print("Launching servers...")
    servers = start_servers()

    print("Launching clients...")
    clients = start_clients()

    try:
        print("All instances started. Press Ctrl+C to terminate.")
        while True:
            time.sleep(1)  # Keep the main process alive
    except KeyboardInterrupt:
        print("Terminating all instances...")
        for process in servers + clients:
            process.terminate()
        print("All instances terminated.")
