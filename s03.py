import multiprocessing
import socket
import threading
import json
import time
import ipaddress
import netifaces as ni
import platform
import uuid
import re

client_broadcast_listener_port = 49153
server_broadcast_listener_port = 49154

server_heartbeat_tcp_listener_port = 49160

client_receive_chat_tcp_port = 50001
client_forward_message_multicast_port = 51000

leader_election_port = 49161

multicast_group_ip = '224.0.1.1'

last_heartbeat_timestamp = None

# create election sockets
ring_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
ring_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

class Server(multiprocessing.Process):
    client_cache_key_offset = 0
    local_servers_cache = dict()
    local_clients_cache = dict()
    local_group_cache = dict()

    def __init__(self):
        super(Server, self).__init__()
        self.os = self.get_os_type()
        print("Server running on OS: ", self.os)
        self.active_interface = self.get_active_interface()
        self.server_address = self.get_local_ip_address()
        self.subnet_mask = self.get_subnet_mask(self.active_interface)
        self.broadcast_address = self.get_broadcast_address()
        print(self.active_interface)
        self.last_heartbeat_timestamp = last_heartbeat_timestamp
        self.ring_socket = ring_socket
        if self.os == "macOS":
            self.ring_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.ring_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ring_socket.bind((self.server_address, leader_election_port))
        self.server_uuid = self.generate_server_uuid()
        print("My UUID: ", self.server_uuid)
        self.participant = False
        self.keep_running_nonLeader = True
        self.is_admin_of_groupchat = False
        if self.server_id == "MAIN":
            self.local_group_cache["MAIN_CHAT"] = "MAIN"
        print("Server initialized with default chat group MAIN_CHAT")

    @staticmethod
    def get_local_ip_address():
        """ Get the local IP address of the machine. """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                print("Attempting to connect to an external host for IP resolution...")
                s.connect(("8.8.8.8", 80))  # Google's public DNS server
                local_ip = s.getsockname()[0]
                print(f"Local IP obtained: {local_ip}")
                return local_ip
        except Exception as e:
            print(f"Error obtaining local IP address: {e}")
            return '127.0.0.1'  # Fallback to localhost
        
    @staticmethod
    def get_active_interface():
        interfaces = ni.interfaces()
        for interface in interfaces:
            if interface != 'lo0':
                addr = ni.ifaddresses(interface)
                try:
                    # Check if the interface has an IPv4 configuration
                    if ni.AF_INET in addr:
                        ipv4_info = addr[ni.AF_INET][0]
                        ip = ipv4_info['addr']
                        netmask = ipv4_info['netmask']

                        # Optionally check for an active internet connection
                        # This attempts to create a socket using the interface's IP
                        try:
                            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                                s.bind((ip, 0))
                                s.connect(("8.8.8.8", 80))  # Google's DNS server
                                return interface
                        except socket.error:
                            pass  # Interface is not suitable

                except KeyError:
                    # Interface does not have IPv4 configuration
                    pass

        return None
    
    @staticmethod
    def get_subnet_mask(interface):
        try:
            addr = ni.ifaddresses(interface)
            if ni.AF_INET in addr:
                ipv4_info = addr[ni.AF_INET][0]
                subnet_mask = ipv4_info['netmask']
                return subnet_mask
            else:
                return None  # Interface does not have IPv4 configuration
        except KeyError:
            return None  # Interface does not have IPv4 configuration
        
    @staticmethod
    def get_os_type():
        system = platform.system()
        if system == "Windows":
            return "Windows"
        elif system == "Darwin":
            return "macOS"
        else:
            return "Unknown"
        
    @staticmethod
    def generate_server_uuid():
        return str(uuid.uuid4())
    
    def run(self):
        print("I'm alive")

        # Get the broadcast address from the existing server_instance
        broadcast_address = self.broadcast_address   
        if broadcast_address is None:
            print("Failed to obtain broadcast address. Exiting.")
            exit(1)

        # determine the os type
        os = self.get_os_type()

        BROADCAST_PORT = 49154     
        MSG = bytes("HI MAIN SERVER", 'utf-8')

        broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        broadcast_socket.settimeout(2)

        received_response = False

        # try 5 times to find the MAIN server, otherwise declares as new MAIN
        for i in range(0,5):
            print("Trying to find other servers...")
        
            if os == "macOS":
                broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                broadcast_socket.sendto(MSG, (broadcast_address, BROADCAST_PORT))
            else:
                broadcast_socket.sendto(MSG, (broadcast_address, BROADCAST_PORT))

            try:             
                message, server = broadcast_socket.recvfrom(1024)
                server_response = message.decode('utf-8')
            except socket.timeout:
                #print("No answer from MAIN server")
                pass
            else:
                if server_response:
                    match = re.search(r'\b([A-Za-z])\b$', message.decode('utf-8'))
                    self.server_id = match.group(1)
                    print('Received message from MAIN server: ', message.decode('utf-8'))
                    received_response = True
                    self.run_funcs()
                    break
        
        broadcast_socket.close()
        # no response from MAIN server? declare as new MAIN server
        if not received_response:
            print("No other server was found, declare as MAIN server.")
            self.server_id = "MAIN"
            self.run_funcs()

    def run_funcs(self):
        #print(self.server_id+": "+"Up and running")
        if self.server_id == "MAIN":
            client_listener_thread = threading.Thread(target=self.listen_for_clients)
            client_listener_thread.start()

            server_listener_thread = threading.Thread(target=self.listen_for_servers)
            server_listener_thread.start()
            
            heartbeat_send_thread = threading.Thread(target=self.send_heartbeat)
            heartbeat_send_thread.start()
        
        else:
            self.cache_update_listener_thread = threading.Thread(target=self.listen_for_cache_update)
            self.heartbeat_receive_thread = threading.Thread(target=self.listen_for_heartbeats)
            self.heartbeat_timeout_thread = threading.Thread(target=self.check_heartbeat_timeout)
            self.leader_election_thread = threading.Thread(target=self.leader_election)

            self.cache_update_listener_thread.start()
            self.heartbeat_receive_thread.start()
            self.heartbeat_timeout_thread.start()
            self.leader_election_thread.start()

            self.start_listen_client_messages()
    
    def start_listen_client_messages(self):

        self.client_message_listener_thread = threading.Thread(target=self.listen_for_client_messages)

        self.client_message_listener_thread.start()

        self.is_admin_of_groupchat = True
    
    def get_broadcast_address(self):
        IP = self.server_address
        MASK = self.subnet_mask
        host = ipaddress.IPv4Address(IP)
        net = ipaddress.IPv4Network(IP + '/' + MASK, False)

        # print('Host:', ipaddress.IPv4Address(int(host) & int(net.hostmask)))
        # print('Broadcast:', net.broadcast_address)
        broadcast_address = str(net.broadcast_address) 
        return broadcast_address
            
    def send_heartbeat(self):
        print(self.server_id+": "+"Heartbeat Sending started")
        #print("Local Server Cache:", self.local_servers_cache)
        while True:
            time.sleep(10)
            failed_group_server = []
            for server_id, server_address in self.local_servers_cache.items():
                if server_address[0] != self.server_address:
                    count = 0
                    for i in range(0,3):
                        acknowledgment_received = self.send_heartbeat_to_server(server_address[0], server_heartbeat_tcp_listener_port)
                        #acknowledgment_received = "YES"
                        if acknowledgment_received:
                            print(self.server_id+": "+"Heartbeat acknowledgment received from "+server_id)
                            break
                        else:
                            count = count + 1
                            print(f"No acknowledgment received from {server_id}. Server may be down. Error Count : {count}")
                            if count == 3:
                                failed_group_server.append(server_id)

            for server_id in failed_group_server:
                del self.local_servers_cache[server_id]
                self.reassign_chat_groups(server_id)


    def send_heartbeat_to_server(self, server_address, server_port):
        acknowledgment_received = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if self.os == "macOS":
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                s.settimeout(2)  # Timeout for the connection
                # Combine server address and port into a tuple
                server_address_with_port = (server_address, server_port)
                #print(self.server_id+": "+"Send Heartbeat to: "+str(server_address_with_port[0])+":"+str(server_address_with_port[1]))
                print(self.server_id+": "+"Send Heartbeat to: "+str(server_address_with_port))
                s.connect(server_address_with_port)
                s.sendall(b'HEARTBEAT')
                acknowledgment = s.recv(1024)
                if acknowledgment == b'ACK':
                    acknowledgment_received = True
        except socket.error:
            pass  # Error handling for connection errors or timeout
        return acknowledgment_received
    
    def listen_for_heartbeats(self):
        print(self.server_id+": "+"Heartbeat listener started")
        while self.keep_running_nonLeader == True:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        if self.os == "macOS":
                            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                        s.bind((self.server_address, server_heartbeat_tcp_listener_port))
                        actual_port = s.getsockname()[1]
                        #print(self.server_id+": "+"Heartbeat Listener Started on port "+str(actual_port))
                        s.listen()
                        conn, addr = s.accept()
                        with conn:
                            data = conn.recv(1024)
                            if data == b'HEARTBEAT':
                                # Handle the received heartbeat
                                print(self.server_id+": "+"Heartbeat received from "+str(addr))
                                # Update the timestamp of the last received heartbeat
                                self.last_heartbeat_timestamp = time.time()
                                # Send an acknowledgment
                                conn.sendall(b'ACK')
                    except socket.timeout:
                        pass #socket timeout
            except socket.error as e:
                print(f"Error: {e}")

    # find every group where the dead server was admin and reassign group to (new) MAIN server
    def reassign_chat_groups(self, dead_server_id):
        
        reassigned_groups = []

        for group in self.local_group_cache:
            if self.local_group_cache[group] == dead_server_id:         
                self.local_group_cache[group] = self.server_id
                reassigned_groups.append(group)
            
        update_cache_thread2 = threading.Thread(target=self.updateCacheList)
        update_group_server_of_client_thread = threading.Thread(target=self.update_group_server_of_client(reassigned_groups))
        update_cache_thread2.start()
        update_group_server_of_client_thread.start()

        # if server is not already a groupchat server start threads for groupchat tasks
        if self.is_admin_of_groupchat == False:
            start_listen_client_message_thread = threading.Thread(target=self.start_listen_client_messages)
            start_listen_client_message_thread.start()

    # find out which clients need to be informed about their groupchat server change
    def update_group_server_of_client(self, reassigned_groups):

        for group in reassigned_groups:
            clients_to_inform = []
            for client in self.local_clients_cache:
                if client[0] == group:
                    clients_to_inform.append(client)

            new_group_server_addr = self.server_address    
            self.send_client_new_group_server_address(new_group_server_addr, clients_to_inform)

    # inform clients about the address of their new groupchat server
    def send_client_new_group_server_address(self, addr, clients_to_inform):
        PORT = 52000

        for client in clients_to_inform: 
            client_addr = self.local_clients_cache[client]

            try:
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.connect((client_addr[0], PORT))
                server_socket.sendall(addr.encode('utf-8'))
                server_socket.close()
            except (ConnectionRefusedError, TimeoutError):
                print(f'Unable to send to {client_addr}') 

    
    def check_heartbeat_timeout(self):
        while self.keep_running_nonLeader == True:
            time.sleep(5)  # Adjust the interval as needed
            if self.last_heartbeat_timestamp is not None:
                current_time = time.time()
                # Define the timeout period (15 seconds)
                timeout_duration = 15
                if current_time - self.last_heartbeat_timestamp >= timeout_duration:
                    print(self.server_id+": "+"No heartbeats received for "+str(timeout_duration)+" seconds. Initiating LCR...")
                    # Call a function to initiate the LCR algorithm here
                    print("Server cache:", self.local_servers_cache)
                    Server.local_servers_cache = self.local_servers_cache
                    self.start_leader_election()
    
    # find highest server ID in cache
    def get_last_server_id(self):      
        if self.local_group_cache:
            return ord(max(self.local_group_cache, key=lambda k: ord(k)))
        else:
            # ascii value before A
            return 64
        
    def register_server(self):
        return

    # listen for servers if they want to join the DS
    def listen_for_servers(self):

        BROADCAST_PORT = 49154
        BROADCAST_ADDRESS = self.broadcast_address

        # Create a UDP socket
        listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Set the socket to broadcast and enable reusing addresses
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if self.os == "macOS":
            listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            listen_socket.bind((BROADCAST_ADDRESS, BROADCAST_PORT))
        else:
            listen_socket.bind(('', BROADCAST_PORT))

        print(self.server_id+": "+"Listening to server register broadcast messages")

        while True:
            data, addr = listen_socket.recvfrom(1024)
            if data:
                message = data.decode('utf-8')
                last_server_id = self.get_last_server_id()
                new_server_id = chr(last_server_id + 1)
                #new_server_id = last_server_id + 1
                self.local_servers_cache[new_server_id] = addr
                self.local_group_cache[new_server_id] = new_server_id
                #print("GroupCache: ", self.local_group_cache)

                print(self.server_id+": "+"Received server register broadcast message:", message)

                #self.register_server(addr, new_server_id)

                register_server_thread = threading.Thread(target=self.register_server(addr, new_server_id))
                register_server_thread.start()
                update_cache_thread = threading.Thread(target=self.updateCacheList)
                if update_cache_thread.is_alive:
                    update_cache_thread.run()
                else:
                    update_cache_thread.start()
                    
    def register_server(self, addr, server_id):
        message = 'Hi ' + addr[0] + ' this is your chat-group ID: ' + str(server_id)
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_socket.connect((addr[0], addr[1]))
        server_socket.sendto(str.encode(message), addr)
        server_socket.close()

    def listen_for_clients(self):
        
        BROADCAST_PORT = 49153
        BROADCAST_ADDRESS = self.broadcast_address

        # Create a UDP socket
        listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Set the socket to broadcast and enable reusing addresses
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if self.os == "macOS":
            listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            listen_socket.bind((BROADCAST_ADDRESS, BROADCAST_PORT))
        else:
            listen_socket.bind(('', BROADCAST_PORT))

        print(self.server_id+": "+"Listening to client register broadcast messages")

        while True:
            data, addr = listen_socket.recvfrom(1024)
            if data:
                message = data.decode('utf-8')
                print(self.server_id+": "+"Received client register broadcast message:", message)
                splitted = message.split("_")
                if (splitted[0] == 'register'):
                    #self.register_client(splitted[1].upper(), addr)
                    self.register_client(splitted[1].upper(), addr)

                    update_cache_thread = threading.Thread(target=self.updateCacheList)
                    if update_cache_thread.is_alive:
                        update_cache_thread.run()
                    else:
                        update_cache_thread.start()
                        

    # Register client. Check if chatgroup exists, if yes answer client with chatgroup server IP   
    def register_client(self, group, client_addr):
        # Since we only have one group, we can simplify this function
        if group != "MAIN_CHAT":
            print(self.server_id + ": Only MAIN_CHAT group is available")
            self.send_negative_reply_to_client(client_addr)
            return

        server_addr = self.server_address
        self.send_reply_to_client(server_addr, client_addr)

        client_count = self.filter_clients(group)
        self.client_cache_key_offset = client_count + 1
        client_cache_key = group + str(self.client_cache_key_offset)
        self.local_clients_cache[client_cache_key] = client_addr
        print(f"{self.server_id}: Added client to MAIN_CHAT group")

    # find address of groupchat server to inform client
    def find_groupchat_server_addresse(self, group):
        # double check if group really exist
        for key in self.local_group_cache:
            if group == str(key):
                if self.local_group_cache[key] == "MAIN":
                    addr = self.server_address
                else:
                    id = self.local_group_cache[key]
                    addr = self.local_servers_cache[id][0]

        return addr
    
    def send_negative_reply_to_client(self, client_addr):
        PORT = 1001
        message = "Group does not exist"
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_socket.connect((self.server_address, PORT))
        server_socket.sendto(str.encode(message), client_addr)
        server_socket.close()
    
    def send_reply_to_client(self, server_addr, client_addr):
        PORT = 1000
        message = 'Hi ' + client_addr[0] + ' this is your groupchat server: ' + server_addr
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_socket.connect((self.server_address, PORT))
        server_socket.sendto(str.encode(message), client_addr)
        server_socket.close()

     
    def filter_clients(self, group):
        # Simplified since we only have one group
        return len([key for key in self.local_clients_cache if "MAIN_CHAT" in key])
    
    def filter_clients(self, group):
        client_count = 0
        for key in self.local_clients_cache:
            if group == key[0]:
                client_count = client_count + 1
                
        return client_count
    
    def find_group_of_client(self, addr):
        # Since all clients are in the same group, we can simplify this
        return "MAIN_CHAT"
    

    # send updated group view/servers cache to all server
    def updateCacheList(self):
        PORT = 5980
        BROADCAST_ADDRESS = self.broadcast_address
        servers_cache_as_string = json.dumps(self.local_servers_cache, indent=2).encode('utf-8')
        clients_cache_as_string = json.dumps(self.local_clients_cache, indent=2).encode('utf-8')
        group_cache_as_string = json.dumps(self.local_group_cache, indent=2).encode('utf-8')
        separator = "_"

        MSG = servers_cache_as_string + separator.encode('utf-8') + clients_cache_as_string + separator.encode('utf-8') + group_cache_as_string
        broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        time.sleep(2)

        if self.os == "macOS":
            broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            broadcast_socket.sendto(MSG, (BROADCAST_ADDRESS, PORT))
            print("broadcast sent to", BROADCAST_ADDRESS, PORT, "with message", MSG)
        else:
            broadcast_socket.sendto(MSG, (BROADCAST_ADDRESS, PORT))
        broadcast_socket.close()

    # listen for update of the groupview/server cache by MAIN server
    def listen_for_cache_update(self):
        BROADCAST_ADDRESS = self.broadcast_address
        BROADCAST_PORT = 5980

        # Local host information
        # MY_HOST = socket.gethostname()
        # MY_IP = socket.gethostbyname(MY_HOST)

        # Create a UDP socket
        listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Set the socket to broadcast and enable reusing addresses
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        if self.os == "macOS":
            listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            listen_socket.bind((BROADCAST_ADDRESS, BROADCAST_PORT))
        else:
            listen_socket.bind(('', BROADCAST_PORT))

        print(self.server_id+": "+"Listening to cache update broadcast messages")

        while self.keep_running_nonLeader == True:
            try:
                data, addr = listen_socket.recvfrom(1024)
                if data:
                    message = data.decode('utf-8')
                    print(self.server_id+": "+"Received cache update broadcast message:")
                    splitted = message.split("_")
                    server_cache_json = json.loads(splitted[0])
                    client_cache_json = json.loads(splitted[1])
                    group_cache_json = json.loads(splitted[2])
                    self.local_servers_cache = server_cache_json
                    self.local_clients_cache = client_cache_json
                    self.local_group_cache = group_cache_json
                    print("Group Cache:  ", self.local_group_cache)
                    print("Server Cache: ", self.local_servers_cache)
                    print("Client Cache: ", self.local_clients_cache)
            except socket.timeout:
                pass #Timeout reached

    # listen for client chat messages to distribute them to all group members afterwards
    def listen_for_client_messages(self):

        PORT = 50001

        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind((self.server_address, PORT))
        server_socket.listen()

        print(self.server_id+": "+"Group-chat server is listening for client messages at port: ", PORT)

        while True:
            connection, addr = server_socket.accept()

            message = connection.recv(1024)
            
            print(self.server_id+": "+"Received message from client: "+message.decode('utf-8'))

            #response = "Hello, client! I received your message."
            #connection.sendall(bytes(response, 'utf-8'))
            connection.close()

            if message:
                self.distribute_chat_message(message, addr)

    # determine the receiver list of the received client chat message
    def distribute_chat_message(self, message, addr):
        group = self.find_group_of_client(addr)

        receiver_list = []

        for key in self.local_clients_cache:
            if group in key:
                if addr[0] != self.local_clients_cache[key][0]:
                    if self.local_clients_cache[key][0] not in receiver_list:
                        receiver_list.append(self.local_clients_cache[key][0])
                        print(self.server_id+": "+"Group receiver list "+str(receiver_list))
                elif addr[0] == self.local_clients_cache[key][0]:
                    sender = key

        distribute_chat_thread = threading.Thread(target=self.send_chat_message_to_clients(message, receiver_list, sender))
        distribute_chat_thread.start()

    # distribute the received client chat message to all members of the group
    def send_chat_message_to_clients(self, message, receiver_list, sender):

        PORT = 51000

        decoded_message = message.decode('utf-8')
        new_message = sender + ": " + decoded_message
        encoded_message = new_message.encode('utf-8')

        for client in receiver_list:
            try:
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.connect((client, PORT))
                server_socket.sendall(encoded_message)
                server_socket.close()
            except (ConnectionRefusedError, TimeoutError):
                print(f'Unable to send to {client}')           

    def start_leader_election(self):
        #Reset last heartbeat timestamp
        self.last_heartbeat_timestamp = None

        #Check if last remaining server -> instant self leader declaration
        if len(self.local_servers_cache) == 1:
            self.handle_leader_tasks()
        else:
            # Get the address of the neighboring server to the 'right' in the ring.
            neighbor_info = self.get_neighbour('right')
            if neighbor_info:
                self.send_election_message(neighbor_info['server_address'], uuid=None, isLeader=False)

    def send_election_message(self, neighbor_address, uuid, isLeader):
        if isLeader == False and uuid is None:
            # Create an election message with the server's UUID.
            election_message = {"id": self.server_uuid, "isLeader": False}
            # Send the election message to the neighbor's address.
            self.ring_socket.sendto(json.dumps(election_message).encode(), (neighbor_address[0], leader_election_port))
            print(election_message, "...sent to: ", neighbor_address, "...on port: ", leader_election_port)
        elif isLeader == False and uuid < self.server_uuid: #Suggesting itself to leader
            election_message = {"id": self.server_uuid, "isLeader": True}
            # Send the election message to the neighbor's address.
            self.ring_socket.sendto(json.dumps(election_message).encode(), (neighbor_address[0], leader_election_port))
            print(election_message, "...sent to: ", neighbor_address, "...on port: ", leader_election_port)
        elif isLeader == False and uuid == self.server_uuid: #Suggesting itself to leader
            election_message = {"id": self.server_uuid, "isLeader": True}
            # Send the election message to the neighbor's address.
            self.ring_socket.sendto(json.dumps(election_message).encode(), (neighbor_address[0], leader_election_port))
            print(election_message, "...sent to: ", neighbor_address, "...on port: ", leader_election_port)
        elif isLeader == True and uuid != self.server_uuid: #Agreeing to someone else as leader
            election_message = {"id": uuid, "isLeader": True}
            # Send the election message to the neighbor's address.
            self.ring_socket.sendto(json.dumps(election_message).encode(), (neighbor_address[0], leader_election_port))
        else: #Sending pid 
            election_message = {"id": uuid, "isLeader": False}
            # Send the election message to the neighbor's address.
            self.ring_socket.sendto(json.dumps(election_message).encode(), (neighbor_address[0], leader_election_port))
            print(election_message, "...sent to: ", neighbor_address, "...on port: ", leader_election_port)

    def get_neighbour(self, direction):
        # First, convert the dictionary keys (server IDs) to a list.
        server_ids = list(self.local_servers_cache.keys())

        # Find the server ID corresponding to self.server_address.
        current_server_id = None
        for server_id, address in self.local_servers_cache.items():
            if address[0] == self.server_address:
                current_server_id = server_id
                break

        if current_server_id is None:
            print(f"Server with IP address {self.server_address} not found in the cache.")
            return None

        # Find the index of the current server ID in the list of server IDs.
        current_index = server_ids.index(current_server_id)

        # Determine the index of the next or previous server in the ring.
        if direction == 'right':
            # Get the next server in the ring.
            neighbor_index = (current_index + 1) % len(server_ids)
        elif direction == 'left':
            # Get the previous server in the ring.
            neighbor_index = (current_index - 1) % len(server_ids)
        else:
            return None

        # Get the neighbor's server ID.
        neighbor_server_id = server_ids[neighbor_index]

        # Return the neighbor's address as a tuple (IP, port).
        neighbor_address = self.local_servers_cache[neighbor_server_id]
        return {'server_address': (neighbor_address[0], neighbor_address[1])}
    
    def set_participant(p):
        global participant
        participant = p
    
    def leader_election(self):
        self.local_servers_cache = Server.local_servers_cache
        #ring_socket.bind((self.server_address, leader_election_port))  # Bind to the server's IP and leader election port
        while self.keep_running_nonLeader == True:
            # Receive election messages from neighbors.
            #print(self.ring_socket)
            data, address = self.ring_socket.recvfrom(4096)
            #Reset last heartbeat timestamp
            self.last_heartbeat_timestamp = None
            if data:
                election_message = json.loads(data.decode())
                received_id = election_message.get('id')
                received_isLeader = election_message.get('isLeader')
                print("Received UUID:", received_id)
                print("Own UUID:", self.server_uuid)
                print("Received isLeader:", received_isLeader)
                if received_isLeader == False:
                    # Logic to handle the election process based on the received UUID.
                    if not self.participant:
                        # Forward the message to the next server in the ring.
                        neighbor_info = self.get_neighbour('right')
                        print("got neighbor", neighbor_info)
                        if neighbor_info:
                            self.send_election_message(neighbor_info['server_address'], received_id, received_isLeader)
                    self.participant = True
                elif received_isLeader == True:
                    if received_id == self.server_uuid:
                        self.handle_leader_tasks()
                    elif received_id > self.server_uuid:
                        # Agree to leader
                        neighbor_info = self.get_neighbour('right')
                        print("got neighbor", neighbor_info)
                        if neighbor_info:
                            self.send_election_message(neighbor_info['server_address'], received_id, received_isLeader)
                    else:
                        print("Failed")
                    self.participant = False
                else:
                    print("Leader Election failed")


    def declare_victory(self):
        # Declare this server as the leader and notify neighbors.
        victory_message = {"mid": self.server_uuid, "isLeader": True}
        neighbor_info = self.get_neighbour('right')
        self.ring_socket.sendto(json.dumps(victory_message).encode(), neighbor_info['server_address'])
        #print(self.ring_socket)
        #if neighbor_info:
        #    self.send_election_message(neighbor_info['server_address'], self.server_uuid, True)

    def handle_leader_tasks(self):
        # Perform leader-specific tasks here
        print(self.server_id+": "+"++++++++++++++++++++++++++++++++++++")
        print(self.server_id+": "+str(self.server_address)+" is now the leader")
        print(self.server_id+": "+"++++++++++++++++++++++++++++++++++++")
        print("Server Cache:", self.local_servers_cache)
        del self.local_servers_cache[self.server_id]
        print("Server Cache:", self.local_servers_cache)
        old_server_id = self.server_id
        self.server_id = "MAIN"
        print(self.server_id+": "+"Server ID was changed from: "+str(old_server_id)+" to "+str(self.server_id))

        # check if new MAIN server was leader of any groupchats and reassign these to serverID MAIN
        self.reassign_chat_groups(old_server_id)
        # reassign the groupchats of the old MAIN server to the new MAIN server
        self.reassign_chat_groups(self.server_id)
        self.stop_threads()
        self.run_funcs()

    def stop_threads(self):
        self.keep_running_nonLeader = False
