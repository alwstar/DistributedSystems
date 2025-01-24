import multiprocessing
import socket
import threading
import json
import time
import ipaddress
import netifaces as ni
import uuid
import re

# Port and IP configurations
CLIENT_BROADCAST_PORT = 49153
SERVER_BROADCAST_PORT = 49154
SERVER_HEARTBEAT_PORT = 49160
CLIENT_CHAT_PORT = 50001
FORWARD_MESSAGE_PORT = 51000
NEW_SERVER_PORT = 52000
LEADER_ELECTION_PORT = 49161
MULTICAST_GROUP_IP = '224.0.1.1'

# Shared variables
last_heartbeat_timestamp = None
ring_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
ring_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

class Server(multiprocessing.Process):
    def __init__(self):
        super().__init__()
        self.local_servers_cache = {}
        self.local_clients_cache = {}
        self.local_group_cache = {"MAIN_CHAT": "MAIN"}
        self.client_counter = 0
        
        # Network information
        self.active_interface = self.get_active_interface()
        self.server_address = self.get_local_ip()
        self.subnet_mask = self.get_subnet_mask(self.active_interface)
        self.broadcast_address = self.get_broadcast_address()

        # Server settings
        self.server_uuid = self.generate_uuid()
        self.server_id = None  # Initially, no leader declared
        self.participant = False
        self.keep_running = True
        self.is_admin_of_groupchat = False

        # Socket setup
        self.ring_socket = ring_socket
        self.ring_socket.bind((self.server_address, LEADER_ELECTION_PORT))

        print(f"Server initialized with UUID: {self.server_uuid}")

    @staticmethod
    def get_local_ip():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception as e:
            print(f"Error obtaining local IP: {e}")
            return '127.0.0.1'

    @staticmethod
    def get_active_interface():
        for interface in ni.interfaces():
            if interface == 'lo0':
                continue
            addr = ni.ifaddresses(interface)
            if ni.AF_INET in addr:
                try:
                    ipv4_info = addr[ni.AF_INET][0]
                    ip = ipv4_info['addr']
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        s.bind((ip, 0))
                        s.connect(("8.8.8.8", 80))
                        return interface
                except socket.error:
                    pass
        return None

    @staticmethod
    def get_subnet_mask(interface):
        try:
            addr = ni.ifaddresses(interface)
            return addr[ni.AF_INET][0].get('netmask')
        except KeyError:
            return None

    @staticmethod
    def generate_uuid():
        return str(uuid.uuid4())

    def get_broadcast_address(self):
        try:
            ip = ipaddress.IPv4Address(self.server_address)
            net = ipaddress.IPv4Network(f"{self.server_address}/{self.subnet_mask}", False)
            return str(net.broadcast_address)
        except Exception as e:
            print(f"Error calculating broadcast address: {e}")
            return None

    def run(self):
        if not self.broadcast_address:
            print("Failed to determine broadcast address. Exiting.")
            return

        self.discover_leader_or_elect()

    def discover_leader_or_elect(self):
        broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        broadcast_socket.settimeout(3)

        for _ in range(5):
            print("Searching for existing LEADER...")
            broadcast_socket.sendto(b"HI LEADER SERVER", (self.broadcast_address, SERVER_BROADCAST_PORT))
            try:
                response, _ = broadcast_socket.recvfrom(1024)
                match = re.search(r'\b([A-Za-z])\b$', response.decode('utf-8'))
                if match:
                    self.server_id = match.group(1)
                    print(f"LEADER found with ID: {self.server_id}")
                    self.start_server_functionalities()
                    return
            except socket.timeout:
                continue

        print("No LEADER found. Declaring self as LEADER.")
        self.server_id = "LEADER"
        self.start_server_functionalities()

    def start_server_functionalities(self):
        if self.server_id == "LEADER":
            self.start_leader_server()
        else:
            self.start_non_leader_server()

    def start_leader_server(self):
        print("Starting LEADER server functionalities...")
        threads = [
            threading.Thread(target=self.listen_for_clients),
            threading.Thread(target=self.listen_for_servers),
            threading.Thread(target=self.send_heartbeat),
            threading.Thread(target=self.listen_for_client_messages)
        ]

        for thread in threads:
            thread.start()
        print("LEADER server threads started.")

    def start_non_leader_server(self):
        print("Starting NON-LEADER server functionalities...")
        threads = [
            threading.Thread(target=self.listen_for_heartbeats),
            threading.Thread(target=self.check_heartbeat_timeout),
            threading.Thread(target=self.leader_election)
        ]

        for thread in threads:
            thread.start()
        print("NON-LEADER server threads started.")

    def send_heartbeat(self):
        while True:
            time.sleep(10)
            failed_servers = []  # Temporary list to track servers to be removed
            for server_id, server_addr in list(self.local_servers_cache.items()):
                if server_addr[0] != self.server_address:
                    if not self.send_heartbeat_to_server(server_addr):
                        print(f"No heartbeat response from {server_id}. Marking for removal.")
                        failed_servers.append(server_id)
            # Remove servers after the iteration to avoid runtime modification error
            for server_id in failed_servers:
                self.local_servers_cache.pop(server_id, None)

    def send_heartbeat_to_server(self, server_addr):
        try:
            with socket.create_connection((server_addr[0], SERVER_HEARTBEAT_PORT), timeout=3) as conn:
                conn.sendall(b'HEARTBEAT')
                response = conn.recv(1024)
                return response == b'ACK'
        except socket.error:
            return False

    def listen_for_heartbeats(self):
        print("Listening for heartbeats...")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((self.server_address, SERVER_HEARTBEAT_PORT))
            s.listen()
            while self.keep_running:
                conn, _ = s.accept()
                with conn:
                    data = conn.recv(1024)
                    if data == b'HEARTBEAT':
                        conn.sendall(b'ACK')
                        self.last_heartbeat_timestamp = time.time()

    def check_heartbeat_timeout(self):
        while self.keep_running:
            time.sleep(5)
            if self.last_heartbeat_timestamp and time.time() - self.last_heartbeat_timestamp > 15:
                print("No heartbeat received. Initiating leader election.")
                self.start_leader_election()

    def start_leader_election(self):
        print("Starting leader election...")
        if len(self.local_servers_cache) == 1:
            self.declare_self_as_leader()
        else:
            neighbor = self.get_neighbor('right')
            if neighbor:
                self.send_election_message(neighbor['server_address'])

    def send_election_message(self, neighbor_address):
        message = {"id": self.server_uuid, "isLeader": False}
        self.ring_socket.sendto(json.dumps(message).encode(), neighbor_address)

    def declare_self_as_leader(self):
        print("Declaring self as LEADER.")
        self.server_id = "LEADER"
        self.start_leader_server()

    def get_neighbor(self, direction):
        server_ids = list(self.local_servers_cache.keys())
        if self.server_id in server_ids:
            current_index = server_ids.index(self.server_id)
            neighbor_index = (current_index + 1) % len(server_ids) if direction == 'right' else (current_index - 1) % len(server_ids)
            return self.local_servers_cache.get(server_ids[neighbor_index])
        return None

    def listen_for_clients(self):
        print("Listening for client connections...")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind((self.server_address, CLIENT_BROADCAST_PORT))
            while self.keep_running:
                data, addr = s.recvfrom(1024)
                if data:
                    message = data.decode('utf-8')
                    print(f"Received client connection request: {message} from {addr}")
                    self.handle_client_join(addr, message)

    def listen_for_servers(self):
        print("Listening for server registration messages...")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind((self.server_address, SERVER_BROADCAST_PORT))
            while self.keep_running:
                data, addr = s.recvfrom(1024)
                if data:
                    message = data.decode('utf-8')
                    print(f"Received server registration message: {message} from {addr}")
                    self.register_server(addr, message)

    def listen_for_client_messages(self):
        print("Listening for client messages...")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((self.server_address, CLIENT_CHAT_PORT))
            s.listen()
            while self.keep_running:
                conn, addr = s.accept()
                with conn:
                    data = conn.recv(1024)
                    if data:
                        message = data.decode('utf-8')
                        print(f"Received client message: {message} from {addr}")
                        self.distribute_message_to_clients(message)

    def handle_client_join(self, addr, message):
        print(f"Handling new client join from {addr}: {message}")
        self.local_clients_cache[addr] = message

    def register_server(self, addr, message):
        print(f"Registering new server from {addr}: {message}")
        self.local_servers_cache[addr] = message

    def distribute_message_to_clients(self, message):
        print("Distributing message to all clients...")
        for client_addr in self.local_clients_cache.keys():
            try:
                with socket.create_connection((client_addr[0], CLIENT_CHAT_PORT), timeout=3) as conn:
                    conn.sendall(message.encode('utf-8'))
                    print(f"Message sent to {client_addr}")
            except Exception as e:
                print(f"Failed to send message to {client_addr}: {e}")

if __name__ == "__main__":
    server = Server()
    server.start()
