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
server_new_server_port = 52000

leader_election_port = 49161

multicast_group_ip = '224.0.1.1'

last_heartbeat_timestamp = None

# Create a UDP socket for the ring communication
ring_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
ring_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

class Server(multiprocessing.Process):
    def __init__(self):
        super(Server, self).__init__()
        # Initialize caches
        self.local_servers_cache = dict()
        self.local_clients_cache = dict()
        self.local_group_cache = dict()
        self.client_counter = 0  # Global counter for all clients

        # Rest of initialization
        # self.os = self.get_os_type()
        self.active_interface = self.get_active_interface()
        self.server_address = self.get_local_ip_address()
        self.subnet_mask = self.get_subnet_mask(self.active_interface)
        self.broadcast_address = self.get_broadcast_address()
        print(self.active_interface)
        self.last_heartbeat_timestamp = last_heartbeat_timestamp
        self.ring_socket = ring_socket
        self.ring_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ring_socket.bind((self.server_address, leader_election_port))
        self.server_uuid = self.generate_server_uuid()
        print("Server UUID: ", self.server_uuid)
        self.participant = False
        self.keep_running_nonLeader = True
        self.is_admin_of_groupchat = False
        
        # Initialize with LEADER server ID
        self.server_id = "LEADER"
        
        # Initialize the single chat group
        self.local_group_cache["MAIN_CHAT"] = "MAIN"
        print("Server initialized with default chat group MAIN_CHAT")

    # Get the local IP address of the machine
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
        
    # Get the active network interface
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
    
    # Get the subnet mask of the active network interface
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
        
    # Get the operating system type
    # @staticmethod
    #def get_os_type():
    #    system = platform.system()
    #    if system == "Windows":
    #        return "Windows"
    #    else:
    #        return "Unknown"
        
    # Generate a UUID for the server
    @staticmethod
    def generate_server_uuid():
        return str(uuid.uuid4())
    
    # Run the server process
    def run(self):
        print("Server started")
        
        # Get the broadcast address from the existing server_instance
        broadcast_address = self.broadcast_address   
        if broadcast_address is None:
            print("Failed to obtain broadcast address. Exiting.")
            exit(1)

        # determine the os type
        # os = self.get_os_type()

        BROADCAST_PORT = server_broadcast_listener_port     
        MSG = bytes("HI LEADER SERVER", 'utf-8')

        broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        broadcast_socket.settimeout(2)

        received_response = False

        # try 5 times to find the LEADER server, otherwise remains as LEADER
        for i in range(0,5):
            print("Trying to find other servers...")
        
            broadcast_socket.sendto(MSG, (broadcast_address, BROADCAST_PORT))

            try:             
                message, server = broadcast_socket.recvfrom(1024)
                server_response = message.decode('utf-8')
            except socket.timeout:
                pass
            else:
                if server_response:
                    match = re.search(r'\b([A-Za-z])\b$', message.decode('utf-8'))
                    self.server_id = match.group(1)
                    print('Received message from LEADER server: ', message.decode('utf-8'))
                    received_response = True
                    self.run_funcs()
                    break
        
        broadcast_socket.close()
        
        if not received_response:
            # We keep our initial LEADER server_id
            print("No other server was found, continuing as LEADER server.")
            self.run_funcs()

    # Run the server functions
    def run_funcs(self):
        if self.server_id == "LEADER":
            print(f"{self.server_id}: Starting LEADER server functions...")
            
            client_listener_thread = threading.Thread(target=self.listen_for_clients)
            server_listener_thread = threading.Thread(target=self.listen_for_servers)
            heartbeat_send_thread = threading.Thread(target=self.send_heartbeat)
            message_listener_thread = threading.Thread(target=self.listen_for_client_messages)
            
            client_listener_thread.start()
            server_listener_thread.start()
            heartbeat_send_thread.start()
            message_listener_thread.start()
            
            print(f"{self.server_id}: All server threads started")
        else:
            self.cache_update_listener_thread = threading.Thread(target=self.listen_for_cache_update)
            self.heartbeat_receive_thread = threading.Thread(target=self.listen_for_heartbeats)
            self.heartbeat_timeout_thread = threading.Thread(target=self.check_heartbeat_timeout)
            self.leader_election_thread = threading.Thread(target=self.leader_election)
            self.client_message_listener_thread = threading.Thread(target=self.listen_for_client_messages)

            self.cache_update_listener_thread.start()
            self.heartbeat_receive_thread.start()
            self.heartbeat_timeout_thread.start()
            self.leader_election_thread.start()
            self.client_message_listener_thread.start()

            self.is_admin_of_groupchat = True
    
    # Handle the tasks of the LEADER server
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

    # find every group where the dead server was admin and reassign group to (new) LEADER server
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
        PORT = server_new_server_port

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
        # Get all server IDs from the servers cache
        if self.local_servers_cache:
            server_ids = [id for id in self.local_servers_cache.keys() if len(id) == 1]
            if server_ids:
                return ord(max(server_ids))
        
        # If no servers or only LEADER exists, return ascii value before 'A'
        return 64  # ASCII value before 'A'

    # listen for servers if they want to join the DS
    def listen_for_servers(self):

        BROADCAST_PORT = server_broadcast_listener_port
        BROADCAST_ADDRESS = self.broadcast_address

        # Create a UDP socket
        listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Set the socket to broadcast and enable reusing addresses
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
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
        BROADCAST_PORT = client_broadcast_listener_port
        BROADCAST_ADDRESS = self.broadcast_address

        listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)  
        listen_socket.bind(('', BROADCAST_PORT))

        print(f"{self.server_id}: Listening for client connections")

        while True:
            try:
                data, addr = listen_socket.recvfrom(1024)
                if data:
                    message = data.decode('utf-8')
                    parts = message.split('|')  # Using | as separator
                    if len(parts) >= 3 and parts[0] == 'join':
                        username = parts[2]
                        print(f"{self.server_id}: New client {username} connected from {addr}")
                        self.handle_client_join(addr, username)
                        print(f"{self.server_id}: Client cache after join: {self.local_clients_cache}")
                        
                        update_cache_thread = threading.Thread(target=self.updateCacheList)
                        update_cache_thread.start()
            except Exception as e:
                print(f"Error in listen_for_clients: {e}")
                
    def handle_client_join(self, client_addr, username):
        try:
            server_addr = self.server_address
            self.send_reply_to_client(server_addr, client_addr)

            # Increment global counter
            self.client_counter += 1
            client_cache_key = f"MAIN_CHAT{self.client_counter}"
            
            # Store both address and username
            self.local_clients_cache[client_cache_key] = {
                'addr': client_addr,
                'username': username
            }
            
            print(f"{self.server_id}: Added client {username} to MAIN_CHAT group with key {client_cache_key}")
            print(f"{self.server_id}: Current cache: {self.local_clients_cache}")
        except Exception as e:
            print(f"Error in handle_client_join: {e}")
            import traceback
            print(traceback.format_exc())
                        

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
        # This method is no longer used for generating keys
        return len([key for key in self.local_clients_cache if group in key])
    
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
        try:
            PORT = 5980
            BROADCAST_ADDRESS = self.broadcast_address

            # Create proper JSON strings with double quotes
            servers_cache_json = json.dumps(self.local_servers_cache)
            clients_cache_json = json.dumps(self.local_clients_cache)
            group_cache_json = json.dumps(self.local_group_cache)
            
            # Use a different separator that won't appear in JSON
            separator = "|||"

            MSG = f"{servers_cache_json}{separator}{clients_cache_json}{separator}{group_cache_json}"
            
            broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            time.sleep(2)

            broadcast_socket.sendto(MSG.encode('utf-8'), (BROADCAST_ADDRESS, PORT))
            print(f"{self.server_id}: Cache update broadcast sent")
            
        except Exception as e:
            print(f"Error in updateCacheList: {e}")
        finally:
            broadcast_socket.close()

    # listen for update of the groupview/server cache by LEADER server
    def listen_for_cache_update(self):
        BROADCAST_ADDRESS = self.broadcast_address
        BROADCAST_PORT = 5980

        listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        listen_socket.bind(('', BROADCAST_PORT))

        print(f"{self.server_id}: Listening to cache update broadcast messages")

        while self.keep_running_nonLeader == True:
            try:
                data, addr = listen_socket.recvfrom(1024)
                if data:
                    message = data.decode('utf-8')
                    print(f"{self.server_id}: Received cache update broadcast message")
                    
                    # Split using the new separator
                    splitted = message.split("|||")
                    
                    if len(splitted) == 3:
                        try:
                            server_cache_json = json.loads(splitted[0])
                            client_cache_json = json.loads(splitted[1])
                            group_cache_json = json.loads(splitted[2])
                            
                            self.local_servers_cache = server_cache_json
                            self.local_clients_cache = client_cache_json
                            self.local_group_cache = group_cache_json
                            
                            print(f"{self.server_id}: Cache updated successfully")
                        except json.JSONDecodeError as e:
                            print(f"Error decoding JSON: {e}")
                    else:
                        print(f"Invalid message format: expected 3 parts, got {len(splitted)}")
                        
            except Exception as e:
                print(f"Error in listen_for_cache_update: {e}")
                time.sleep(1)  # Add small delay to prevent tight loop

    # listen for client chat messages to distribute them to all group members afterwards
    def listen_for_client_messages(self):
        PORT = client_broadcast_listener_port
        
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            print(f"{self.server_id}: Attempting to bind to {self.server_address}:{PORT}")
            server_socket.bind((self.server_address, PORT))
            print(f"{self.server_id}: Successfully bound to {self.server_address}:{PORT}")
            
            server_socket.listen(5)
            print(f"{self.server_id}: Socket is now listening for incoming connections")

            while True:
                try:
                    connection, addr = server_socket.accept()
                    connection.settimeout(5)
                    
                    try:
                        data = connection.recv(1024)
                        if data:
                            message_text = data.decode('utf-8')
                            parts = message_text.split('|')  # Split username and message
                            if len(parts) >= 2:
                                sender_username = parts[0]
                                message = parts[1]
                                print(f"{self.server_id}: Received message from {sender_username}: {message}")
                                self.distribute_chat_message(message.encode('utf-8'), addr, sender_username)
                    except socket.timeout:
                        print(f"Timeout receiving message from {addr}")
                    except Exception as e:
                        print(f"Error receiving message from {addr}: {e}")
                    finally:
                        connection.close()
                        
                except Exception as e:
                    print(f"Error accepting connection: {e}")
                    time.sleep(1)
                    
        except Exception as e:
            print(f"Error setting up message listener: {e}")
        finally:
            server_socket.close()

    # determine the receiver list of the received client chat message
    def distribute_chat_message(self, message, addr, sender_username=None):
        try:
            group = "MAIN_CHAT"
            receiver_list = []

            # Just use the username that was passed with the message
            if not sender_username:
                # Fallback to finding username in cache if not provided
                for key, client_info in self.local_clients_cache.items():
                    if client_info['addr'][0] == addr[0]:
                        sender_username = client_info['username']
                        break

            # Build receiver list
            for key, client_info in self.local_clients_cache.items():
                if client_info['addr'][0] != addr[0]:
                    receiver_list.append(client_info['addr'][0])

            if receiver_list:
                print(f"{self.server_id}: Distributing message to clients: {receiver_list}")
                self.send_chat_message_to_clients(message, receiver_list, sender_username)
            else:
                print(f"{self.server_id}: No other clients to send message to")
                
        except Exception as e:
            print(f"Error in distribute_chat_message: {e}")

    # distribute the received client chat message to all members of the group
    def send_chat_message_to_clients(self, message, receiver_list, sender_username):
        PORT = client_forward_message_multicast_port

        try:
            decoded_message = message.decode('utf-8')
            new_message = f"{sender_username}: {decoded_message}"
            print(f"{self.server_id}: Preparing to send: {new_message}")
            encoded_message = new_message.encode('utf-8')

            for client in receiver_list:
                try:
                    print(f"{self.server_id}: Attempting to send to {client}:{PORT}")
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
                        server_socket.settimeout(5)
                        server_socket.connect((client, PORT))
                        server_socket.sendall(encoded_message)
                        print(f"{self.server_id}: Successfully sent to {client}")
                except (ConnectionRefusedError, TimeoutError) as e:
                    print(f'{self.server_id}: Unable to send to {client}: {e}')
                except Exception as e:
                    print(f'{self.server_id}: Error sending to {client}: {e}')
                    
        except Exception as e:
            print(f"{self.server_id}: Error in send_chat_message_to_clients: {e}")        

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
        # Remove the reference to Server.local_servers_cache
        while self.keep_running_nonLeader == True:
            try:
                data, address = self.ring_socket.recvfrom(4096)
                # Reset last heartbeat timestamp
                self.last_heartbeat_timestamp = None
                if data:
                    election_message = json.loads(data.decode())
                    received_id = election_message.get('id')
                    received_isLeader = election_message.get('isLeader')
                    print("Received UUID:", received_id)
                    print("Own UUID:", self.server_uuid)
                    print("Received isLeader:", received_isLeader)
                    if received_isLeader == False:
                        if not self.participant:
                            neighbor_info = self.get_neighbour('right')
                            print("got neighbor", neighbor_info)
                            if neighbor_info:
                                self.send_election_message(neighbor_info['server_address'], received_id, received_isLeader)
                        self.participant = True
                    elif received_isLeader == True:
                        if received_id == self.server_uuid:
                            self.handle_leader_tasks()
                        elif received_id > self.server_uuid:
                            neighbor_info = self.get_neighbour('right')
                            print("got neighbor", neighbor_info)
                            if neighbor_info:
                                self.send_election_message(neighbor_info['server_address'], received_id, received_isLeader)
                        else:
                            print("Failed")
                        self.participant = False
                    else:
                        print("Leader Election failed")
            except Exception as e:
                print(f"Error in leader election: {e}")
                time.sleep(1)  # Add small delay to prevent tight loop


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
        print(f"{self.server_id}: ++++++++++++++++++++++++++++++++++++")
        print(f"{self.server_id}: {self.server_address} is now the leader")
        print(f"{self.server_id}: ++++++++++++++++++++++++++++++++++++")
        
        # Save old ID before changing to LEADER
        old_server_id = self.server_id
        self.server_id = "LEADER"
        print(f"{self.server_id}: Server ID was changed from: {old_server_id} to {self.server_id}")

        # Reassign chat groups
        self.reassign_chat_groups(old_server_id)
        self.reassign_chat_groups(self.server_id)

        # Stop non-leader threads
        self.keep_running_nonLeader = False
        time.sleep(2)  # Give threads time to stop

        # Start LEADER server functionality
        print(f"{self.server_id}: Starting LEADER server functionality after election")
        self.run_funcs()

        # Notify all clients about the new server
        self.notify_clients_new_server()

    def notify_clients_new_server(self):
        PORT = server_new_server_port
        for key, client_info in self.local_clients_cache.items():
            try:
                client_addr = client_info['addr'][0]
                print(f"{self.server_id}: Notifying client at {client_addr} about new server")
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_socket.settimeout(5)  # Add timeout
                server_socket.connect((client_addr, PORT))
                # Send new server address directly
                server_socket.sendall(self.server_address.encode('utf-8'))
                server_socket.close()
                print(f"{self.server_id}: Successfully notified client at {client_addr}")
            except Exception as e:
                print(f"{self.server_id}: Failed to notify client at {client_addr}: {e}")

    def receive_new_server(self):
        PORT = server_new_server_port
        try:
            client_receive_message_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_receive_message_socket.bind((self.client_address, PORT))
            client_receive_message_socket.listen()

            print("Client: Listening for server address update messages")

            while True:
                try:
                    connection, addr = client_receive_message_socket.accept()
                    message = connection.recv(1024).decode('utf-8')
                    
                    if message.startswith("SERVER_CHANGE"):
                        _, new_server = message.split("|")
                        print(f"Client: Switching to new server: {new_server}")
                        self.registered_server_address = new_server
                        # Re-establish connection with new server
                        self.auto_join()
                except Exception as e:
                    print(f"Error receiving server update: {e}")
        except Exception as e:
            print(f"Error setting up server update listener: {e}")

    def stop_threads(self):
        self.keep_running_nonLeader = False
