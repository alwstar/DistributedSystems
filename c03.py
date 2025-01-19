import multiprocessing
import socket
import threading
import re

class Client(multiprocessing.Process):

    registered_server = None
    host = socket.gethostname()
    client_address = socket.gethostbyname(host)

    def __init__(self):
        self.host = socket.gethostname()
        self.client_address = socket.gethostbyname(self.host)
        # Get username at startup
        self.username = self.get_username()
        self.run()

    def get_username(self):
        while True:
            username = input("Enter your username: ").strip()
            if username and len(username) <= 20:  # Basic validation
                return username
            print("Please enter a valid username (max 20 characters)")

    def run(self):
        print("Client: Starting chat...")
        
        # Auto-join by sending broadcast
        self.auto_join()
        
        # Start sending and receiving messages
        send_thread = threading.Thread(target=self.send_message)
        receive_thread = threading.Thread(target=self.receive_messages)
        receive_new_server_thread = threading.Thread(target=self.receive_new_server)

        send_thread.start()
        receive_thread.start()
        receive_new_server_thread.start()

        send_thread.join()
        receive_thread.join()

    def auto_join(self):
        PORT = 49153
        # Include username in the join message
        MSG = bytes(f"join_MAIN_CHAT_{self.username}", 'utf-8')

        broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        broadcast_socket.sendto(MSG, ('<broadcast>', PORT))
        
        data, server = broadcast_socket.recvfrom(1024)
        print('Client: Connected to chat server')

        ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
        matches = re.findall(ip_pattern, data.decode('utf-8'))
        self.registered_server_address = matches[1]
        print(f"Client: Connected to server: {self.registered_server_address}")
        broadcast_socket.close()
                

    def register(self, message_type, message_group):
        PORT = 49153

        MSG = bytes(message_type + '_' + message_group, 'utf-8')

        broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        broadcast_socket.sendto(MSG, ('<broadcast>', PORT))
        
        data, server = broadcast_socket.recvfrom(1024)
        
        print('Client: Received message from server: ', data.decode('utf-8'))

        if data.decode('utf-8') == "Group does not exist":
            success = 0
        else: 
            # search for server ip_address in message from server
            ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
            matches = re.findall(ip_pattern, data.decode('utf-8'))

            # 2nd ip address in message from server is server address
            self.registered_server_address = matches[1]
            
            #self.registered_server = server
            print("Client: My server: ", self.registered_server_address)

            success = 1

        broadcast_socket.close()

        return success

    def send_message(self):
        PORT = 49153

        while True:
            try:
                message = input()
                if message.lower() == 'exit':
                    print("Client: Shutting down chat client")
                    break

                try:
                    print(f"Client: Attempting to connect to {self.registered_server_address}:{PORT}")
                    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    client_socket.settimeout(5)
                    client_socket.connect((self.registered_server_address, PORT))
                    client_socket.sendall(bytes(message, 'utf-8'))
                    print("Client: Connected successfully!")
                    client_socket.close()
                except ConnectionRefusedError:
                    print("Unable to connect to server. Server might be down or unreachable.")
                except socket.timeout:
                    print("Connection attempt timed out. Server might be busy.")
                except Exception as e:
                    print(f"Error sending message: {e}")

            except Exception as e:
                print(f"Error in message input: {e}")
                break

    def receive_messages(self):
        PORT = 51000

        client_receive_message_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_receive_message_socket.bind((self.client_address, PORT))
        client_receive_message_socket.listen()

        print("Client: Listening for groupchat messages")

        while True:
            connection, addr = client_receive_message_socket.accept()
            message = connection.recv(1024)
            print(message.decode('utf-8'))  # Message already includes username from server

    def receive_new_server(self):
        PORT = 52000

        client_receive_message_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_receive_message_socket.bind((self.client_address, PORT))
        client_receive_message_socket.listen()

        print("Client: Listening for server address update messages")

        while True:
            connection, addr = client_receive_message_socket.accept()
            message = connection.recv(1024)
            print(f"Client: New server: {message.decode('utf-8')}")
            self.registered_server_address = message.decode('utf-8')
